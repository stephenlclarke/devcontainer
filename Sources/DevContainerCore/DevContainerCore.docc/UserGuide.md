# User Guide

Use the official Dev Containers CLI and VS Code extension with stock Apple
`container` through a local Apple-backed Engine API socket. This is a
project-owned Unix socket that implements the required Docker-shaped protocol,
not a Docker daemon or proxy. Its default filename is `engine.sock`; the Docker-shaped names exposed
to VS Code are compatibility vocabulary only. Before sending a workload
request, the adapter verifies that the socket is served by the project-owned
Apple runtime engine and rejects any foreign Docker-compatible endpoint.

## Install

Install and verify Apple `container` 1.4.1 separately, then install the stable
formula:

```console
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
devcontainer doctor --container /usr/local/bin/container
```

When macOS displays the Local Network prompt for the selected runtime's
`container-runtime-linux` helper, choose **Allow** so published host ports can
reach the container VM.

## Configure stock mode

```console
devcontainer configure \
  --backend stock \
  --compose-provider container-compose \
  --container /usr/local/bin/container
```

The engine, context, doctor, diagnostics, and Compose commands resolve this
same configuration. Command options take precedence over environment
variables, which take precedence over the file. The context command only
prints an optional shell export for diagnostic compatibility and never changes
global system configuration.

## Run the official CLI

```console
devcontainer up --workspace-folder /path/to/project

devcontainer exec \
  --workspace-folder /path/to/project \
  /bin/sh -c 'uname -a'
```

## Run VS Code

Configure both Apple-backed compatibility adapters:

```json
{
  "dev.containers.dockerPath": "/opt/homebrew/bin/devcontainer-docker",
  "dev.containers.dockerComposePath": "/opt/homebrew/bin/devcontainer-compose"
}
```

Launch VS Code normally:

```console
code /path/to/project
```

Then run **Dev Containers: Reopen in Container**.

## Runtime boundary

Apple does not supply a Compose plug-in. Every Compose-backed project uses the
pinned stock-profile `container-compose` executable bundled inside the signed
devcontainer archive. The `devcontainer-compose` dispatcher never launches
Docker Compose. The same Engine-socket boundary supports stock Apple
`container` and Stephen Clarke's optional enhanced Container distribution.
An explicitly selected external Compose executable must identify its source as
`stephenlclarke/container-compose`, report a semantic version and exact source
commit, and pass that probe before it receives a project command. The selected
runtime must identify stock `apple/container` or the explicit
`stephenlclarke/container` distribution. Foreign custom distributions and
Docker-named backend or Compose-provider values fail before project work.

The 1.0.2 release candidate includes checked-in image, Dockerfile, Feature,
user, environment, lifecycle, port, reuse, Compose, engine, fault, and real VS
Code fixtures. Those fixtures become a stable certification claim only after
the release-bound parity gate passes. They do not cover every standard
property or arbitrary Docker argument. Read <doc:Conformance> before using
GPU, privileged, security, device, resource, hostname, or advanced mount
behavior.

Bind sources resolving to `docker.sock` or `docker.raw.sock` are rejected
before creation. Selecting either name as the compatibility endpoint is also
rejected, including through a symlink. The product has no host-daemon proxy
mode, and its selected runtime path must resolve to an executable named
`container`.

## Diagnostics

```console
devcontainer diagnostics \
  --container /usr/local/bin/container \
  --output "$PWD/devcontainer-diagnostics.tar.gz"
```

Review the printed manifest and archive before sharing them.

The complete task-oriented manual, including Dockerfile and Compose examples,
provider switching, backend claims, service operation, troubleshooting,
upgrade, and uninstall instructions, is the repository
[user guide](https://github.com/stephenlclarke/devcontainer/blob/main/USER_GUIDE.md).
