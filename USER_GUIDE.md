# devcontainer user guide

<!-- markdownlint-disable MD013 -->

This manual explains how to install, configure, use, troubleshoot, and remove `devcontainer` 1.0.1. It is for developers who want to use the official Dev Containers CLI or the VS Code Dev Containers extension with Apple’s stock `container` runtime on an Apple-silicon Mac.

## What this project does

`devcontainer` is a local compatibility bridge. The official [`@devcontainers/cli`](https://github.com/devcontainers/cli), VS Code Dev Containers extension, Docker CLI, and Docker Compose client continue to behave as Docker clients. This project provides the user-owned Docker Engine Unix socket they use and translates the release-certified request subset into Apple `container` operations.

It does not:

- replace or modify Apple’s `container` installation;
- install a Docker engine;
- install `container-compose`;
- make Apple `container` a general-purpose Docker daemon;
- implement its own `devcontainer.json` parser;
- claim every property in the Development Containers Specification.

The exact certified scope is in [COMPATIBILITY.md](COMPATIBILITY.md). Known gaps and every audited property are in [CONFORMANCE.md](CONFORMANCE.md).

## Supported 1.0.1 environment

The stable package supports:

- Apple silicon (`arm64`);
- macOS Tahoe 26 or later;
- stock Apple `container` 1.1.0 installed separately;
- `devcontainer` 1.0.1 installed from the stable Homebrew formula or signed release archive;
- official `@devcontainers/cli` 0.88.0;
- VS Code 1.131.0 with Dev Containers extension 0.467.0;
- Docker CLI 29.6.2 and Docker Compose 5.3.1 as protocol clients;
- optional, separately installed `container-compose` 0.10.1 with its matched custom runtime stack.

These are the exact release-certified versions, not minimum-version promises. See the fingerprint table in [COMPATIBILITY.md](COMPATIBILITY.md) before changing one component independently.

## Runtime choices

There are two Apple runtime paths:

| Path | Runtime | Compose implementation | Recommended use |
| --- | --- | --- | --- |
| Stock | Unmodified Apple `container` 1.1.0 | Upstream Docker Compose over this project’s compatibility socket | Default |
| Optional provider | The exact custom runtime required by `container-compose` 0.10.1 | Separately installed `container-compose` | Explicit provider testing or features supplied by that stack |

Apple does not make a Compose plug-in for `container`. The optional `container-compose` project is independently maintained by Stephen Clarke. Installing `devcontainer` does not install it or its custom runtime.

## Install the stock path

Install Apple’s signed `container` 1.1.0 package first. Verify the stock executable before installing this project:

```console
$ /usr/local/bin/container --version
container CLI version 1.1.0 (build: release, commit: 5973b9c)
```

Install the stable formula:

```console
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer
```

Start Apple’s runtime and this project’s compatibility service:

```console
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
```

The first published-port operation can cause macOS to ask whether `container-runtime-linux` may find and connect to devices on the local network. Choose **Allow** for the stock Apple helper. This permission allows the runtime’s host-port forwarder to reach its container VM; it does not allow `devcontainer` to scan the local network.

Run the health check:

```console
devcontainer doctor --container /usr/local/bin/container
```

For archive verification, source installation, Current builds, upgrades, and complete package details, see [INSTALL.md](INSTALL.md).

## Configure the default backend

Write an explicit stock configuration:

```console
devcontainer configure \
  --backend stock \
  --compose-provider docker
```

The default configuration file is:

```text
~/.config/devcontainer/config.toml
```

Configuration does not change Docker’s global context and does not start or stop either runtime.

Use the compatibility socket only in shells that need it:

```console
eval "$(devcontainer context)"
```

Confirm that the Docker client now sees the bridge:

```console
docker version
docker info
```

The server platform is reported as `devcontainer Apple runtime bridge`. Open a new shell without evaluating `devcontainer context` when you want to use Docker’s normal context again.

## Run the first Dev Container

The repository’s [hello example](Examples/hello) contains:

```json
{
  "name": "devcontainer Apple runtime demo",
  "image": "alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b",
  "remoteUser": "root",
  "postCreateCommand": "printf 'lifecycle hook: ready\\n' > /tmp/devcontainer-ready"
}
```

From a shell configured with `devcontainer context`, run the official CLI:

```console
npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder /path/to/devcontainer/Examples/hello
```

The command returns JSON containing the container ID and remote workspace path. Run a command inside the workspace:

```console
npx --yes @devcontainers/cli@0.88.0 exec \
  --workspace-folder /path/to/devcontainer/Examples/hello \
  /bin/sh -c 'cat hello.txt && cat /tmp/devcontainer-ready'
```

Inspect and remove the environment with the Docker client attached to the same socket:

```console
docker ps --all
docker rm --force CONTAINER_ID
```

## Use your own image-based configuration

Create `.devcontainer/devcontainer.json` in a project:

```json
{
  "name": "my Apple dev environment",
  "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
  "remoteUser": "vscode",
  "workspaceFolder": "/workspaces/${localWorkspaceFolderBasename}",
  "forwardPorts": [3000],
  "postCreateCommand": "printf 'ready\\n'"
}
```

Then run:

```console
eval "$(devcontainer context)"
npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder "$PWD"
```

Use digest-pinned images when reproducibility matters. Public Linux `arm64` images are in the release-certified scope. Private-registry authentication and cross-architecture images are not certified by 1.0.1.

## Use a Dockerfile configuration

A basic Dockerfile configuration is supported:

```json
{
  "name": "Dockerfile environment",
  "build": {
    "dockerfile": "../Dockerfile",
    "context": "..",
    "target": "development",
    "args": {
      "APP_ENV": "development"
    }
  },
  "remoteUser": "vscode"
}
```

Dockerfile path, context, build arguments, target, generated Feature build context, and failed-build streaming are in the parity suite. Arbitrary `build.options` and `build.cacheFrom` combinations are not independently certified; check [CONFORMANCE.md](CONFORMANCE.md) before relying on them.

## Use Docker Compose on stock Apple container

Create a normal Compose file:

```yaml
services:
  app:
    image: alpine:3.22
    command: ["sleep", "infinity"]
    volumes:
      - ..:/workspaces/example
  database:
    image: postgres:18
    environment:
      POSTGRES_PASSWORD: local-development-only
```

Reference it from `.devcontainer/devcontainer.json`:

```json
{
  "name": "Compose environment",
  "dockerComposeFile": "../compose.yaml",
  "service": "app",
  "runServices": ["app", "database"],
  "workspaceFolder": "/workspaces/example",
  "shutdownAction": "stopCompose"
}
```

The default `devcontainer-compose` wrapper launches upstream Docker Compose against the compatibility socket:

```console
devcontainer configure \
  --backend stock \
  --compose-provider docker
eval "$(devcontainer context)"
npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder "$PWD" \
  --docker-compose-path /opt/homebrew/bin/devcontainer-compose
```

The certified Compose scope includes selected services, `runServices`, dependencies and health gates, environment files, workspace projection, named volumes, networks, aliases, recreation, restart, shutdown, signals, and Dev Container discovery labels. It is not a claim that every Docker Compose property or command is implemented.

## Use VS Code

Install the official Microsoft Dev Containers extension. Configure the Compose wrapper in VS Code settings:

```json
{
  "dev.containers.dockerComposePath": "/opt/homebrew/bin/devcontainer-compose"
}
```

Launch VS Code from a shell that has selected the compatibility socket:

```console
eval "$(devcontainer context)"
code /path/to/project
```

Use **Dev Containers: Reopen in Container**. The 1.0.1 real-VS-Code test covers extension activation, open, attach, VS Code server installation, an integrated command, a forwarded port, rebuild, reopen locally, and cleanup.

If VS Code was already running, quit all VS Code windows before launching it from the configured shell. A process started before `DOCKER_HOST` was set does not inherit the new value.

## Use lifecycle commands

The certified string-valued lifecycle path includes:

```json
{
  "initializeCommand": "printf 'host initialization\\n'",
  "onCreateCommand": "printf 'created\\n' > /tmp/lifecycle",
  "updateContentCommand": "printf 'updated\\n' >> /tmp/lifecycle",
  "postCreateCommand": "printf 'configured\\n' >> /tmp/lifecycle",
  "postStartCommand": "printf 'started\\n' >> /tmp/lifecycle",
  "postAttachCommand": "printf 'attached\\n' >> /tmp/lifecycle"
}
```

The official CLI owns lifecycle parsing and ordering; the bridge supplies exec, user, environment, workspace, and stream behavior. Object-valued parallel lifecycle commands, non-default `waitFor` values, and every failure combination have not been independently release-certified.

## Use Features

Declare OCI Features normally:

```json
{
  "image": "ubuntu:24.04",
  "features": {
    "ghcr.io/devcontainers/features/common-utils:2": {
      "installZsh": false
    },
    "ghcr.io/devcontainers/features/git:1": {
      "version": "os-provided"
    }
  }
}
```

The official CLI resolves, orders, and installs Features. Version 1.0.1 certifies the checked-in public Feature fixture, generated BuildKit context, lockfile, and frozen-lock rejection. A Feature that asks for GPU devices, full Docker privileged mode, unsupported security options, or unsupported mount options inherits the corresponding runtime non-conformance.

## Users and environment

The release fixture covers `containerUser`, `remoteUser`, static `containerEnv`, client-scoped `remoteEnv`, container-environment expansion, workspace ownership for its selected configuration, and explicit `updateRemoteUserUID: false`.

Example:

```json
{
  "containerUser": "vscode",
  "remoteUser": "vscode",
  "updateRemoteUserUID": false,
  "containerEnv": {
    "APP_ENV": "development"
  },
  "remoteEnv": {
    "EDITOR": "code",
    "PATH": "${containerEnv:PATH}:/workspaces/bin"
  }
}
```

Automatic UID/GID rewriting with `updateRemoteUserUID: true` is not independently certified in 1.0.1.

## Ports

Use `forwardPorts` for VS Code or CLI-managed forwarding:

```json
{
  "forwardPorts": [3000],
  "portsAttributes": {
    "3000": {
      "label": "Web application",
      "onAutoForward": "silent"
    }
  }
}
```

Use `appPort` when Docker-style publishing is required:

```json
{
  "appPort": ["127.0.0.1:3000:3000"]
}
```

The certified fixture proves TCP publishing, forwarding, port-collision rejection, and host/service connectivity. TCP and UDP forwarding exist in the adapter, but the complete UI behavior of every `portsAttributes` and `otherPortsAttributes` value belongs to the client and is not exhaustively certified.

## Mounts and persistent data

The Dev Container configuration fixtures certify bind workspace mounts and a
string-valued named volume with persistence and cleanup. The lower-level
engine fixture separately certifies basic bind, named-volume, read-only bind,
and tmpfs Docker requests.

Example:

```json
{
  "mounts": [
    "source=my-project-cache,target=/cache,type=volume",
    "source=${localWorkspaceFolder}/input,target=/input,type=bind,readonly"
  ]
}
```

Advanced Docker `--mount` fields such as bind propagation, consistency modes, volume `nocopy`, and tmpfs sizing/mode are not represented by 1.0.1. Image-declared anonymous `VOLUME` entries also use Apple’s writable root filesystem rather than a separate Docker anonymous-volume lifecycle. See [CONFORMANCE.md](CONFORMANCE.md).

## Provider claims

The state database records which backend owns a project. This prevents stock and optional provider operations from silently mutating the same project.

The dispatcher applies Docker Compose's project-name precedence before recording that ownership. Valid global options such as `--env-file`, `--profile`, `--parallel`, and `--progress` are consumed before command classification; an unknown global option fails explicitly instead of running an unclaimed mutation. Commands that can change resources, including `cp`, `exec`, `scale`, and `watch`, require the same durable claim as `up` and `down`.

Inspect a claim:

```console
devcontainer backend show --project PROJECT_KEY
```

Set a claim before creating resources:

```console
devcontainer backend set --project PROJECT_KEY stock
```

Reset only after the project is down and no owned resources remain:

```console
devcontainer backend reset --project PROJECT_KEY
```

The default state database is:

```text
~/Library/Application Support/devcontainer/state.sqlite
```

Do not edit the database directly.

## Optional container-compose provider

The provider path is separately installed and separately selected. It must use the exact runtime stack certified with `container-compose` 0.10.1.

Stop the stock service and runtime:

```console
brew services stop stephenlclarke/tap/devcontainer
/usr/local/bin/container system stop
```

Start the matched optional runtime and bridge explicitly:

```console
/opt/homebrew/bin/container system start
DEVCONTAINER_CONTAINER_BIN=/opt/homebrew/bin/container \
  /opt/homebrew/bin/devcontainer-engine
```

In another shell:

```console
devcontainer configure \
  --backend container-compose \
  --compose-provider container-compose
eval "$(devcontainer context)"
DEVCONTAINER_COMPOSE_PROVIDER=container-compose \
DEVCONTAINER_COMPOSE_BIN=/opt/homebrew/bin/container-compose \
  npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder "$PWD" \
  --docker-compose-path /opt/homebrew/bin/devcontainer-compose
```

Restore stock mode after stopping the foreground engine:

```console
/opt/homebrew/bin/container system stop
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
```

Do not run the stock and custom Apple runtime distributions concurrently against the same runtime state or Dev Container project.

## Apple CLI plug-in registration

Registration is optional. The standalone command and Homebrew service work without it.

Register:

```console
devcontainer plugin register --container /usr/local/bin/container
container devcontainer version
```

Inspect:

```console
devcontainer plugin status --container /usr/local/bin/container
```

Remove only the package-owned link:

```console
devcontainer plugin unregister --container /usr/local/bin/container
```

Registration refuses to overwrite a foreign plug-in.

## Routine operation

Start:

```console
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
```

Check:

```console
devcontainer doctor --container /usr/local/bin/container
eval "$(devcontainer context)"
docker ps --all
```

Restart only this project’s service:

```console
brew services restart stephenlclarke/tap/devcontainer
```

Stop:

```console
brew services stop stephenlclarke/tap/devcontainer
/usr/local/bin/container system stop
```

Stopping the compatibility service does not delete containers, images, networks, volumes, or state.

## Diagnostics and support

Create a privacy-redacted archive:

```console
devcontainer diagnostics \
  --container /usr/local/bin/container \
  --output "$PWD/devcontainer-diagnostics.tar.gz"
```

Review the printed manifest and archive before sharing them. The command bounds file count and log size, redacts home-directory paths and credential-like values, and records checksums for every payload file.

Useful status commands:

```console
devcontainer version --format json
devcontainer doctor --format json
devcontainer plugin status
container system version --format json
container system logs --last 5m
brew services info stephenlclarke/tap/devcontainer
```

## Troubleshooting

### The socket is unavailable

Run:

```console
brew services info stephenlclarke/tap/devcontainer
devcontainer doctor
```

Restart the compatibility service if the runtime is healthy:

```console
brew services restart stephenlclarke/tap/devcontainer
```

### Docker still connects to another engine

Re-evaluate the context in the current shell:

```console
eval "$(devcontainer context)"
printf '%s\n' "$DOCKER_HOST"
docker version
```

Do not set Docker’s global default context as a workaround.

### A published port resets or reports `No route to host`

Open **System Settings → Privacy & Security → Local Network**, enable the selected runtime’s `container-runtime-linux` helper, then restart that runtime:

```console
/usr/local/bin/container system stop
/usr/local/bin/container system start
```

Stock and optional custom runtimes can have separate Local Network entries.

### VS Code does not use the bridge

Quit VS Code completely, evaluate `devcontainer context`, and launch `code` from that same shell. Confirm `dev.containers.dockerComposePath` points to `/opt/homebrew/bin/devcontainer-compose`.

### A configuration uses an unsupported property

Check [CONFORMANCE.md](CONFORMANCE.md). Known unsupported decoded fields return a Docker-shaped error, but 1.0.1 does not yet reject every unknown Docker create/build member. Do not assume a successful create means an arbitrary `runArgs` option was enforced.

### The selected provider conflicts with existing resources

Bring the project down using its current owner. Confirm no project containers, networks, or volumes remain before running `devcontainer backend reset`. The tool deliberately does not migrate or delete ambiguous resources automatically.

## Upgrade and uninstall

Upgrade:

```console
brew upgrade stephenlclarke/tap/devcontainer
```

Unregister the optional Apple CLI plug-in before uninstalling:

```console
devcontainer plugin unregister --container /usr/local/bin/container
brew uninstall --formula stephenlclarke/tap/devcontainer
```

Uninstalling the formula does not remove Apple `container`, Docker clients, `container-compose`, runtime resources, or unrelated user data. See [INSTALL.md](INSTALL.md) for channel switching and complete removal behavior.

## Command summary

| Command | Purpose |
| --- | --- |
| `devcontainer version` | Show version and immutable build provenance |
| `devcontainer doctor` | Validate the selected runtime and compatibility endpoint |
| `devcontainer configure` | Write backend, Compose provider, socket, and strictness configuration |
| `devcontainer context` | Print the explicit `DOCKER_HOST` selection |
| `devcontainer backend show/set/reset` | Manage durable project ownership |
| `devcontainer diagnostics` | Create a bounded, redacted support archive |
| `devcontainer plugin register/unregister/status` | Manage the optional Apple CLI plug-in link |
| `devcontainer-compose` | Dispatch upstream Docker Compose or the optional provider |
| `devcontainer-engine` | Run the local Docker Engine compatibility endpoint |

Use `devcontainer SUBCOMMAND --help` for the authoritative option list.

## Further reading

- [Installation and package integrity](INSTALL.md)
- [Compatibility contract](COMPATIBILITY.md)
- [Standards conformance audit](CONFORMANCE.md)
- [Parity timing analysis](PERFORMANCE.md)
- [Testing strategy](TESTING.md)
- [Software design](DESIGN.md)
- [Dev Containers Specification](https://github.com/devcontainers/spec)
- [Dev Containers CLI](https://github.com/devcontainers/cli)
- [VS Code Dev Containers documentation](https://code.visualstudio.com/docs/devcontainers/containers)
