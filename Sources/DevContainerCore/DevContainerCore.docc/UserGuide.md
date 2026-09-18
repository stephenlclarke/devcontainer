# User Guide

Use the official Dev Containers CLI and VS Code extension with stock Apple
`container` through a local Docker Engine compatibility socket.

## Install

The unreleased Bazel development branch now registers `up`, `build`, `exec`, `read-configuration` and `run-user-commands` as pass-through lifecycle commands. They require an installation-private Node/CLI bundle and project-owned frontend executables; missing assets fail without npm or Docker fallback. Bundle packaging and live candidate qualification are still pending, so the stable installation instructions below do not yet provide these new commands. Component forwarding tests use a stand-in child, not the full reference runtime.

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

The unreleased candidate preserves stored settings when their `configure` options are omitted. This includes strict compatibility: use `--strict` or `--no-strict` to change it explicitly; newly created configurations remain strict by default. Backend and frontend choices are independent.

```console
devcontainer configure \
  --backend stock \
  --compose-provider docker \
  --container /usr/local/bin/container
eval "$(devcontainer context)"
```

The engine, context, doctor, diagnostics, and Compose commands resolve this
same configuration. Command options take precedence over environment
variables, which take precedence over the file. The context command changes
only the current shell. It does not replace Docker's global context.

In current development source, Compose claims the resolved runtime backend independently of frontend selection. A conflicting existing claim is rejected, and a missing frontend fails before creating project state without falling back to Docker. Component coverage of these choices does not expand the certified release matrix or remove the Docker client dependency.

The candidate native-create path completes mount and kernel checks before recording possible submission. Repairing a failed prerequisite permits retry without clearing database records. Once create may have been submitted, failure retains its recovery record; never remove that record merely to force a retry. Live recovery qualification remains separate from component tests.

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

Current-source support-archive probes have a five-second maximum. An individual probe failure is recorded and collection continues, but cancellation or an expired enclosing request aborts and removes staging after subprocess cleanup. Published 1.0.1 does not include this change.

In current source, the Compose bridge bounds project-name and remaining-volume probes to 30 seconds without extending an earlier request deadline, and rejects truncated output above 1 MiB per stream. Failed volume discovery preserves project ownership; cancelled discovery propagates cancellation after owned-child cleanup. Actual Compose operations keep their existing lifetime. This is not yet in the published release.

Current source also bounds `plugin register`, `plugin status` and `plugin unregister` installation discovery to five seconds, preserving any earlier request deadline and reaping the owned probe. Use `--install-root` to skip discovery for an offline installation. This change is not in the published 1.0.1 binary.

In the unreleased candidate, `doctor` validates output format before running any probe and gives each runtime/Compose command a five-second deadline with owned-process cleanup. Socket metadata checks are not HTTP health checks; an absent socket is a warning. Candidate service cleanup also covers startup/waiter failure and cancellation. These component-tested changes do not expand the certified release matrix.

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
