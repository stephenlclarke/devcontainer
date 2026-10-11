// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.

"use strict";

const assert = require("node:assert/strict");
const Module = require("node:module");
const test = require("node:test");

const calls = [];
const vscode = {
  commands: {
    executeCommand: async (...args) => {
      calls.push(args);
      return true;
    },
  },
  env: { remoteName: undefined },
  Uri: { file: (fsPath) => ({ scheme: "file", fsPath }) },
  workspace: { workspaceFolders: [] },
};
const load = Module._load;
Module._load = function(request, parent, isMain) {
  return request === "vscode" ? vscode : load.call(this, request, parent, isMain);
};
const { _test: driver } = require("./extension.js");
Module._load = load;

test("only the exact local anchor window is ignored", async () => {
  const previous = process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR;
  process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR = "/private/driver/anchor";
  vscode.workspace.workspaceFolders = [{ uri: vscode.Uri.file("/private/driver/anchor") }];
  vscode.env.remoteName = undefined;
  try {
    assert.equal(driver.isAnchorWindow(), true);
    vscode.workspace.workspaceFolders = [{ uri: vscode.Uri.file("/private/driver/workspace") }];
    assert.equal(driver.isAnchorWindow(), false);
    vscode.workspace.workspaceFolders = [{ uri: vscode.Uri.file("/private/driver/anchor") }];
    vscode.env.remoteName = "dev-container";
    assert.equal(driver.isAnchorWindow(), false);
    vscode.env.remoteName = undefined;
    vscode.workspace.workspaceFolders.push({ uri: vscode.Uri.file("/another") });
    assert.equal(driver.isAnchorWindow(), false);
    vscode.workspace.workspaceFolders = [{ uri: { scheme: "vscode-remote", fsPath: "/private/driver/anchor" } }];
    assert.equal(driver.isAnchorWindow(), false);
  } finally {
    if (previous === undefined) {
      delete process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR;
    } else {
      process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR = previous;
    }
    vscode.env.remoteName = undefined;
  }
});

test("anchor activation never reads or writes driver state", async () => {
  const keys = {
    DEVCONTAINER_VSCODE_DRIVER_ANCHOR: "/private/driver/anchor",
    DEVCONTAINER_VSCODE_DRIVER_DOCKER: "/pinned/docker",
    DEVCONTAINER_VSCODE_DRIVER_RESULT: "/unavailable/result",
    DEVCONTAINER_VSCODE_DRIVER_STATE: "/unavailable/state",
    DEVCONTAINER_VSCODE_DRIVER_WORKSPACE: "/private/driver/workspace",
  };
  const previous = Object.fromEntries(Object.keys(keys).map((key) => [key, process.env[key]]));
  Object.assign(process.env, keys);
  vscode.workspace.workspaceFolders = [{ uri: vscode.Uri.file(keys.DEVCONTAINER_VSCODE_DRIVER_ANCHOR) }];
  calls.length = 0;
  try {
    await driver.run();
    assert.deepEqual(calls, []);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
});

test("anchor opens as a separate window and refuses explicit failure", async () => {
  const previous = process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR;
  process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR = "/private/driver/anchor";
  calls.length = 0;
  try {
    await driver.openAnchorWindow();
    assert.deepEqual(calls, [[
      "vscode.openFolder",
      vscode.Uri.file("/private/driver/anchor"),
      { forceNewWindow: true, noRecentEntry: true },
    ]]);
    vscode.commands.executeCommand = async () => false;
    await assert.rejects(driver.openAnchorWindow(), /could not open/);
  } finally {
    if (previous === undefined) {
      delete process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR;
    } else {
      process.env.DEVCONTAINER_VSCODE_DRIVER_ANCHOR = previous;
    }
  }
});
