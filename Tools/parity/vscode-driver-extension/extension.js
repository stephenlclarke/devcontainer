// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.

"use strict";

const fs = require("node:fs/promises");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const vscode = require("vscode");

const REQUIRED_ENVIRONMENT = [
  "DEVCONTAINER_VSCODE_DRIVER_DOCKER",
  "DEVCONTAINER_VSCODE_DRIVER_RESULT",
  "DEVCONTAINER_VSCODE_DRIVER_STATE",
  "DEVCONTAINER_VSCODE_DRIVER_WORKSPACE",
];
const REMOTE_NAME = "dev-container";
const MARKER_DIRECTORY = ".devcontainer-evidence";
const POLL_INTERVAL_MS = 250;

let activated = false;
let output;
const execFileAsync = promisify(execFile);

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function atomicJSON(filePath, value) {
  const temporary = `${filePath}.tmp`;
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await fs.rename(temporary, filePath);
}

async function readJSON(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

async function updateState(mutator) {
  const statePath = process.env.DEVCONTAINER_VSCODE_DRIVER_STATE;
  const state = await readJSON(statePath);
  await mutator(state);
  await atomicJSON(statePath, state);
  return state;
}

async function recordEvent(name, details = {}) {
  output.appendLine(`${name}: ${JSON.stringify(details)}`);
  await updateState((state) => {
    state.events.push({
      name,
      remoteName: vscode.env.remoteName || "local",
      timestamp: new Date().toISOString(),
      ...details,
    });
  });
}

function workspaceFolder() {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    throw new Error("the parity workspace is not open");
  }
  return folder;
}

function markerURI(name) {
  return vscode.Uri.joinPath(workspaceFolder().uri, MARKER_DIRECTORY, name);
}

async function readWorkspaceText(name) {
  return Buffer.from(await vscode.workspace.fs.readFile(markerURI(name)))
    .toString("utf8")
    .trim();
}

async function waitFor(description, probe) {
  const timeout = Number.parseInt(
    process.env.DEVCONTAINER_VSCODE_DRIVER_TIMEOUT_MS || "900000",
    10,
  );
  const deadline = Date.now() + timeout;
  let lastError;

  while (Date.now() < deadline) {
    try {
      const value = await probe();
      if (value) {
        return value;
      }
    } catch (error) {
      lastError = error;
    }
    await delay(POLL_INTERVAL_MS);
  }

  const suffix = lastError ? `: ${lastError.message || String(lastError)}` : "";
  throw new Error(`timed out waiting for ${description}${suffix}`);
}

async function waitForMarker(name, expected) {
  return waitFor(`${name}=${expected}`, async () => {
    const value = await readWorkspaceText(name);
    return value === expected ? value : undefined;
  });
}

function requestText(uri) {
  return new Promise((resolve, reject) => {
    const client = uri.protocol === "https:" ? https : http;
    const request = client.get(uri, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const body = Buffer.concat(chunks).toString("utf8").trim();
        if (response.statusCode !== 200) {
          reject(new Error(`forwarded port returned HTTP ${response.statusCode}`));
          return;
        }
        resolve(body);
      });
    });
    request.setTimeout(5000, () => {
      request.destroy(new Error("forwarded port request timed out"));
    });
    request.on("error", reject);
  });
}

async function verifyForwardedPort() {
  const port = Number.parseInt(
    process.env.DEVCONTAINER_VSCODE_DRIVER_PORT || "8123",
    10,
  );
  return waitFor("the VS Code forwarded HTTP port", async () => {
    const external = await vscode.env.asExternalUri(
      vscode.Uri.parse(`http://127.0.0.1:${port}/`),
    );
    const body = await requestText(new URL(external.toString(true)));
    if (body !== "devcontainer-vscode") {
      throw new Error(`forwarded port returned unexpected body ${body}`);
    }
    return external.toString(true);
  });
}

async function runIntegratedTerminal() {
  const terminal = vscode.window.createTerminal({
    cwd: workspaceFolder().uri,
    name: "Devcontainer parity terminal",
  });
  terminal.show(false);
  terminal.sendText(
    "mkdir -p .devcontainer-evidence && " +
      "printf 'integrated-terminal\\n' > " +
      ".devcontainer-evidence/integrated-terminal.txt",
    true,
  );
  await waitForMarker("integrated-terminal.txt", "integrated-terminal");
  terminal.dispose();
}

async function activateDevContainersExtension() {
  const extension = vscode.extensions.getExtension(
    "ms-vscode-remote.remote-containers",
  );
  if (!extension) {
    throw new Error("the pinned Dev Containers extension is not installed");
  }
  await extension.activate();
  await updateState((state) => {
    state.observations.extension_activation = true;
  });
}

async function developmentContainerID() {
  return waitFor("one exact development container identity", async () => {
    const workspace = process.env.DEVCONTAINER_VSCODE_DRIVER_WORKSPACE;
    const configuration = path.join(
      workspace,
      ".devcontainer",
      "devcontainer.json",
    );
    const { stdout } = await execFileAsync(
      process.env.DEVCONTAINER_VSCODE_DRIVER_DOCKER,
      [
        "ps",
        "-aq",
        "--no-trunc",
        "--filter",
        `label=devcontainer.local_folder=${workspace}`,
        "--filter",
        `label=devcontainer.config_file=${configuration}`,
      ],
      {
        env: process.env,
        maxBuffer: 1024 * 1024,
        timeout: 10000,
      },
    );
    const identifiers = stdout
      .split(/\r?\n/u)
      .map((value) => value.trim())
      .filter(Boolean);
    if (identifiers.length !== 1) {
      return undefined;
    }
    if (!/^[0-9a-f]{64}$/u.test(identifiers[0])) {
      throw new Error(`unexpected development container ID ${identifiers[0]}`);
    }
    return identifiers[0];
  });
}

async function fail(error) {
  const message = error?.stack || error?.message || String(error);
  output.appendLine(message);
  let state;
  try {
    state = await updateState((value) => {
      value.phase = "failed";
      value.diagnostic = message;
      value.events.push({
        name: "failure",
        remoteName: vscode.env.remoteName || "local",
        timestamp: new Date().toISOString(),
        message,
      });
    });
  } catch (stateError) {
    state = {
      diagnostic: `${message}; could not update state: ${stateError}`,
      observations: {},
      phase: "failed",
      schemaVersion: 1,
    };
  }
  await atomicJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_RESULT, {
    ...state,
    status: "failed",
  });
  await vscode.commands.executeCommand("workbench.action.closeWindow");
}

async function launchTransition(phase, command) {
  await updateState((state) => {
    state.phase = phase;
  });
  await recordEvent("command", { command, phase });

  setTimeout(() => {
    void readJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_STATE)
      .then((state) => {
        if (state.phase === phase) {
          return fail(
            new Error(`${command} did not complete ${phase} within 60 seconds`),
          );
        }
        return undefined;
      })
      .catch(fail);
  }, 60000);

  void vscode.commands.executeCommand(command).then(
    () => {
      setTimeout(() => {
        void readJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_STATE)
          .then((state) => {
            if (state.phase === phase) {
              return fail(
                new Error(`${command} returned without completing ${phase}`),
              );
            }
            return undefined;
          })
          .catch(fail);
      }, 5000);
    },
    (error) => {
      setTimeout(() => {
        void readJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_STATE)
          .then((state) => {
            if (state.phase === phase) {
              return fail(error);
            }
            return undefined;
          })
          .catch(fail);
      }, 1000);
    },
  );
}

async function handleInitialOpen() {
  if (vscode.env.remoteName) {
    throw new Error(`initial window is unexpectedly remote: ${vscode.env.remoteName}`);
  }
  const folder = workspaceFolder();
  if (folder.uri.scheme !== "file") {
    throw new Error(`initial workspace uses unexpected scheme ${folder.uri.scheme}`);
  }
  if (folder.uri.fsPath !== process.env.DEVCONTAINER_VSCODE_DRIVER_WORKSPACE) {
    throw new Error(`opened unexpected workspace ${folder.uri.fsPath}`);
  }
  await updateState((state) => {
    state.observations.open = true;
  });
  await recordEvent("local-open");
  await launchTransition(
    "attaching",
    "remote-containers.reopenInContainer",
  );
}

async function handleFirstAttach() {
  if (vscode.env.remoteName !== REMOTE_NAME) {
    throw new Error(`attach produced unexpected remote ${vscode.env.remoteName}`);
  }
  await waitForMarker("post-create-count.txt", "1");
  await waitForMarker("post-attach-count.txt", "1");
  await runIntegratedTerminal();
  const externalURI = await verifyForwardedPort();
  const firstContainerID = await developmentContainerID();
  await updateState((state) => {
    state.firstContainerID = firstContainerID;
    state.forwardedURI = externalURI;
    state.observations.attach = true;
    state.observations.forward_port = true;
    state.observations.integrated_command = true;
    state.observations.vscode_server = true;
  });
  await recordEvent("first-attach", { externalURI, firstContainerID });
  await launchTransition(
    "rebuilding",
    "remote-containers.rebuildContainer",
  );
}

async function handleRebuild() {
  if (vscode.env.remoteName !== REMOTE_NAME) {
    throw new Error(`rebuild produced unexpected remote ${vscode.env.remoteName}`);
  }
  await waitForMarker("post-create-count.txt", "2");
  await waitForMarker("post-attach-count.txt", "2");
  const secondContainerID = await developmentContainerID();
  const state = await readJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_STATE);
  if (!state.firstContainerID || secondContainerID === state.firstContainerID) {
    throw new Error("rebuild did not replace the development container");
  }
  await updateState((value) => {
    value.secondContainerID = secondContainerID;
    value.observations.rebuild = true;
  });
  await recordEvent("rebuild", { secondContainerID });
  await launchTransition("reopening", "remote-containers.reopenLocally");
}

async function handleReopen() {
  if (vscode.env.remoteName) {
    throw new Error(`reopen remained remote: ${vscode.env.remoteName}`);
  }
  const folder = workspaceFolder();
  if (
    folder.uri.scheme !== "file" ||
    folder.uri.fsPath !== process.env.DEVCONTAINER_VSCODE_DRIVER_WORKSPACE
  ) {
    throw new Error(`reopened unexpected workspace ${folder.uri.toString(true)}`);
  }
  await updateState((value) => {
    value.phase = "ready-for-cleanup";
    value.observations.reopen = true;
  });
  await recordEvent("reopen-local");
  const state = await readJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_STATE);
  await atomicJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_RESULT, {
    ...state,
    status: "ready-for-cleanup",
  });
  await delay(500);
  await vscode.commands.executeCommand("workbench.action.closeWindow");
}

async function run() {
  const missing = REQUIRED_ENVIRONMENT.filter((name) => !process.env[name]);
  if (missing.length) {
    throw new Error(`missing driver environment: ${missing.join(", ")}`);
  }
  await activateDevContainersExtension();
  const state = await readJSON(process.env.DEVCONTAINER_VSCODE_DRIVER_STATE);

  switch (state.phase) {
    case "local-open":
      await handleInitialOpen();
      break;
    case "attaching":
      await handleFirstAttach();
      break;
    case "rebuilding":
      await handleRebuild();
      break;
    case "reopening":
      await handleReopen();
      break;
    case "failed":
    case "ready-for-cleanup":
      break;
    default:
      throw new Error(`unknown driver phase ${state.phase}`);
  }
}

function activate(context) {
  if (activated) {
    return;
  }
  activated = true;
  output = vscode.window.createOutputChannel("Devcontainer Parity Driver", {
    log: true,
  });
  context.subscriptions.push(output);
  setTimeout(() => {
    void run().catch(fail);
  }, 500);
}

function deactivate() {}

module.exports = { activate, deactivate };
