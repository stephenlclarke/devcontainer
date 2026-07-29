# User Guide

Use the official Dev Containers CLI and VS Code extension with stock Apple
`container` through a local Docker Engine compatibility socket.

## Install

Install and verify Apple `container` 1.1.0 separately, then install the stable
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
  --compose-provider docker
eval "$(devcontainer context)"
```

The context command changes only the current shell. It does not replace
Docker's global context.

## Run the official CLI

```console
npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder /path/to/project

npx --yes @devcontainers/cli@0.88.0 exec \
  --workspace-folder /path/to/project \
  /bin/sh -c 'uname -a'
```

## Run VS Code

Configure the Compose wrapper:

```json
{
  "dev.containers.dockerComposePath": "/opt/homebrew/bin/devcontainer-compose"
}
```

Launch VS Code from the configured shell:

```console
eval "$(devcontainer context)"
code /path/to/project
```

Then run **Dev Containers: Reopen in Container**.

## Runtime boundary

The default path uses upstream Docker Compose over the compatibility socket.
Apple does not supply a Compose plug-in. The separately installed
`container-compose` provider is optional, independently maintained, and uses
its exact matched custom runtime stack.

Version 1.0.1 certifies the checked-in image, Dockerfile, Feature, user,
environment, lifecycle, port, reuse, Compose, engine, fault, and real VS Code
fixtures. It does not certify every standard property or arbitrary Docker
argument. Read <doc:Conformance> before using GPU, privileged, security,
device, resource, hostname, or advanced mount behavior.

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
