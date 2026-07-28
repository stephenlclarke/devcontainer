# Installing devcontainer

<!-- markdownlint-disable MD013 -->

> Version 1.0.0 is the stable release. Its immutable GitHub archive is
> Developer ID-signed, notarized, parity-certified, checksummed, and published
> through the stable Homebrew formula. The Apple runtime remains a separate
> installation.

`devcontainer` provides Dev Containers compatibility for Apple's stock
`container` runtime on Apple-silicon Macs running macOS Tahoe. It installs as a
standalone command and does not install, replace, relink, start, stop, or modify
an Apple container runtime. Its separate Homebrew service starts and stops only
the `devcontainer-engine` compatibility endpoint.

## Installation Contract

The supported installation preserves these boundaries:

- Apple's stock `container` runtime is installed separately from Apple.
- `devcontainer` uses the `container` executable selected by explicit configuration or `PATH`.
- The Docker CLI and upstream Docker Compose client are protocol clients for
  VS Code and the stock multi-service path; they do not install or select a
  Docker engine.
- `container-compose` is optional and used only when the user explicitly selects or enables its provider.
- Installing `devcontainer` never installs `stephenlclarke/container`.
- Installing `devcontainer` never removes or replaces Apple's `container`.
- Installing `devcontainer` never installs `container-compose`.
- Installing `devcontainer` never links a Compose plugin into Apple's install root.
- The formula does not register itself under Apple's install root. An explicit
  `devcontainer plugin register` command owns one reversible symlink and refuses
  to replace a foreign registration.
- Missing optional providers produce an actionable capability error, not an automatic installation or runtime replacement.

## Requirements

The prebuilt and Homebrew packages require:

- Apple silicon (`arm64`).
- macOS Tahoe 26 or later.
- Apple's stock `container` 1.1.0 runtime for the stock Apple backend.
- Local Network permission for the selected runtime's
  `container-runtime-linux` helper so published host ports can reach the
  container VM.
- The Docker CLI and upstream Docker Compose client used by VS Code.
- A supported Xcode or Command Line Tools installation when required by Apple's runtime.

Optional integrations:

- A Docker engine for Docker-oracle comparison or separately selected
  Docker-backed execution.
- An explicitly installed `container-compose` executable for multi-service provider experiments.

The release notes and `devcontainer version --format json` identify the exact
versions used for release validation.

## Verify The Runtime Before Installation

The supported installer does not change runtime state. Install Apple's signed
[`container` 1.1.0 package](https://github.com/apple/container/releases/tag/1.1.0),
then verify it before installing this project:

```sh
command -v container
container --version
container system version --format json
```

The stock-Apple backend must reject a runtime whose provenance identifies a custom distribution when strict stock mode is requested.

Do not remove Apple's package to install `devcontainer`. Do not install a custom runtime as a workaround for an installation check. Runtime compatibility gaps belong in the project's status ledger and issue template.

The first published-port operation can display a macOS Local Network privacy
prompt for the selected runtime's `container-runtime-linux` helper. Choose
**Allow**. Stock mode uses Apple's Developer-ID-signed helper; the optional
provider stack uses a separately installed helper. macOS can list them as
distinct entries. This is runtime permission, not permission for
`devcontainer` to scan the LAN.

## Homebrew Installation

The tap provides two explicit channels:

| Formula | Channel | Version form | Intended use |
| --- | --- | --- | --- |
| `devcontainer` | Stable | `MAJOR.MINOR.PATCH` | Default immutable release |
| `devcontainer-current` | Current | `current.RUN.SHA12` | Opt-in release-candidate build, when published |

Install the stable release:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
devcontainer doctor --container /usr/local/bin/container
```

When a Current candidate is published, install it with:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer-current
```

Stable and Current conflict because both install the same `devcontainer` command. Switch channels explicitly rather than mixing files:

```sh
brew uninstall --formula stephenlclarke/tap/devcontainer-current
brew install --formula stephenlclarke/tap/devcontainer
```

or:

```sh
brew uninstall --formula stephenlclarke/tap/devcontainer
brew install --formula stephenlclarke/tap/devcontainer-current
```

Neither formula may declare a dependency on a custom `container` runtime or `container-compose`.

## Package Layout

The release archive contains:

```text
devcontainer-MAJOR.MINOR.PATCH/
devcontainer-MAJOR.MINOR.PATCH/bin/devcontainer
devcontainer-MAJOR.MINOR.PATCH/bin/devcontainer-compose
devcontainer-MAJOR.MINOR.PATCH/bin/devcontainer-engine
devcontainer-MAJOR.MINOR.PATCH/libexec/container/plugins/devcontainer/config.toml
devcontainer-MAJOR.MINOR.PATCH/libexec/container/plugins/devcontainer/bin/devcontainer
devcontainer-MAJOR.MINOR.PATCH/share/devcontainer/build-info.json
devcontainer-MAJOR.MINOR.PATCH/share/devcontainer/devcontainer.spdx.json
devcontainer-MAJOR.MINOR.PATCH/share/devcontainer/LICENSE
devcontainer-MAJOR.MINOR.PATCH/share/devcontainer/NOTICE.md
devcontainer-MAJOR.MINOR.PATCH/share/devcontainer/README.md
devcontainer-MAJOR.MINOR.PATCH/share/devcontainer/THIRD-PARTY-NOTICES.txt
devcontainer-MAJOR.MINOR.PATCH/share/devcontainer/com.github.stephenlclarke.devcontainer.plist.in
```

The packaged `README.md` points repository files, directories, and images at
the archive's exact source commit. Package verification rejects relative or
mismatched source links, so installed documentation cannot silently drift with
`main`.

Homebrew installs only the package payload under its own prefix and exposes
`bin/devcontainer`. It does not write under Apple's package prefix or
`/usr/local/libexec/container-plugins`. Register the packaged Apple CLI plug-in
only after selecting and starting the intended runtime:

```sh
devcontainer plugin register
container devcontainer version
container devcontainer doctor
```

The command obtains `installRoot` from `container system status --format json`.
Use `--container` to select a container executable or `--install-root` for an
offline installation. If the runtime root is protected, rerun this single
registration command with the permissions required for that root. Remove only
the symlink owned by this package with:

```sh
devcontainer plugin unregister
```

## Use With The Official CLI And VS Code

The compatibility engine listens only on its user-owned Unix socket. Select it
for the current shell without changing Docker's default context:

```sh
eval "$(devcontainer context)"
npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder /path/to/project
```

For VS Code, set the Compose wrapper and launch the workspace from the same
configured shell:

```json
{
  "dev.containers.dockerComposePath": "/opt/homebrew/bin/devcontainer-compose"
}
```

```sh
eval "$(devcontainer context)"
code /path/to/project
```

The wrapper selects upstream Docker Compose over the same socket by default.
Users who intentionally select `container-compose` configure that independent
provider as described below.

## Verify The Installation

Verify the selected binary and embedded provenance:

```sh
command -v devcontainer
devcontainer version
devcontainer version --format json
devcontainer --help
devcontainer plugin status
container devcontainer version
```

Then verify that the Apple runtime remains the runtime the user installed:

```sh
command -v container
container system version --format json
```

The `devcontainer` output should show:

- Product version.
- Stable or Current lane.
- Source repository and exact commit.
- Release build type.
- `arm64` architecture.
- Build-time container distribution and provider identity.

`devcontainer doctor --format json` separately reports the configured socket,
selected Apple executable, detected runtime version, and optional Compose
provider probe. Static version output does not start or inspect a runtime.

Create a reviewable, privacy-redacted support archive with:

```sh
devcontainer diagnostics \
  --container /absolute/path/to/container \
  --compose /absolute/path/to/container-compose \
  --output "$PWD/devcontainer-diagnostics.tar.gz"
```

The command prints its JSON manifest to standard output before writing the
archive. The manifest lists every payload file, byte count, SHA-256 digest,
warning, and build identity. The archive contains bounded runtime probes,
configuration and state summaries, recent events, and bounded log tails.
Paths below the current home directory and credential-like values are
redacted. Inspect the manifest and archive before sharing either one.

## Verify Release Integrity

Every published channel includes an archive, portable SHA-256 sidecar, SPDX
SBOM, build-info file, package-verification result, and GitHub build-provenance
attestation. Packaging rejects unsafe archive paths, special or privileged
files, missing executables, metadata/SBOM mismatches, checksum errors, and
missing required notarization evidence before publication.

For a downloaded stable archive:

```sh
shasum -a 256 -c devcontainer-release-arm64.tar.gz.sha256
gh attestation verify devcontainer-release-arm64.tar.gz --repo stephenlclarke/devcontainer
tar -tzf devcontainer-release-arm64.tar.gz
```

Release packaging Developer ID signs every installed executable, verifies each
signature, submits a ZIP containing the exact staged payload through a keychain
profile, requires an `Accepted` response, and writes only the submission ID,
status, and archive digest into the final package. Verify an extracted binary:

```sh
codesign --verify --strict --verbose=2 devcontainer/bin/devcontainer
codesign --display --verbose=4 devcontainer/bin/devcontainer
```

The project does not claim that a standalone executable is stapled. Apple's
stapler does not support bare Mach-O command-line binaries. Release evidence
instead includes an accepted notary submission for a ZIP containing the exact
signed bytes distributed in the tarball.

## Backend Selection

The implemented user configuration uses explicit provider identities:

```sh
devcontainer configure \
  --backend stock \
  --compose-provider docker \
  --socket "$HOME/.local/state/devcontainer/docker.sock"

devcontainer configure \
  --backend container-compose \
  --compose-provider container-compose
```

`stock` selects this project's Apple runtime adapter. `container-compose`
identifies a project whose Compose lifecycle is owned by the separately
installed provider; it is never described as stock provenance. Docker Compose
is selected with `--compose-provider docker` and talks to this project's Unix
socket.

Executable paths are explicit command or environment inputs:

```sh
devcontainer-engine --container /absolute/path/to/container
devcontainer doctor \
  --container /absolute/path/to/container \
  --compose /absolute/path/to/container-compose

DEVCONTAINER_COMPOSE_BIN=/absolute/path/to/container-compose \
  devcontainer-compose up

DEVCONTAINER_DOCKER_COMPOSE_BIN=/absolute/path/to/docker-compose \
  devcontainer-compose up
```

The Compose dispatcher applies this implemented precedence:

1. `DEVCONTAINER_COMPOSE_PROVIDER`.
2. User configuration written by `devcontainer configure`.
3. The safe default, upstream Docker Compose over the compatibility socket.

For that default, the dispatcher prefers the standalone `docker-compose`
executable so it does not depend on per-user Docker CLI plug-in discovery. It
falls back to `docker compose` when no standalone executable is installed.

Project ownership is then recorded in the state database. `devcontainer
backend set`, `show`, and `reset` provide explicit project-scoped control and
prevent a provider change while owned resources remain.

## Optional container-compose Provider

`container-compose` is not part of the base install. To use it, install and
configure it separately and accept its runtime compatibility.

The supported `stephenlclarke/tap/container-compose` 0.10.1 formula depends on
a matched custom runtime. Installing that formula can add a custom
`/opt/homebrew/bin/container` alongside Apple's `/usr/local/bin/container`.
`devcontainer` continues to prefer Apple's executable by default and never
suggests or performs that installation as an automatic fix.

Stop the compatibility service and switch the separately installed Apple
runtime distribution before running against the Compose stack:

```sh
brew services stop stephenlclarke/tap/devcontainer
/usr/local/bin/container system stop
/opt/homebrew/bin/container system start
DEVCONTAINER_CONTAINER_BIN=/opt/homebrew/bin/container \
  /opt/homebrew/bin/devcontainer-engine
```

In another shell, select its socket and the Compose provider:

```sh
eval "$(devcontainer context)"
DEVCONTAINER_COMPOSE_PROVIDER=container-compose \
DEVCONTAINER_COMPOSE_BIN=/opt/homebrew/bin/container-compose \
  npx --yes @devcontainers/cli@0.88.0 up \
  --workspace-folder /path/to/project
```

This foreground form makes the non-stock runtime choice visible. Stop it with
Control-C, then restore the stock runtime and compatibility service with:

```sh
/opt/homebrew/bin/container system stop
/usr/local/bin/container system start
brew services start stephenlclarke/tap/devcontainer
```

Provider discovery verifies the configured executable with:

```sh
/explicit/path/to/container-compose version --format json
```

It also inspects the active runtime provenance. If the provider uses a custom
runtime, `devcontainer` reports that fact and does not describe the result as
stock-Apple compatibility.

When a Dev Container configuration requires Compose and no explicit provider is usable, the command should stop before side effects with:

- The missing capability.
- The provider path that was checked.
- The detected runtime distribution.
- A link to project compatibility documentation.
- No automatic installation command.

## Source Builds

The implemented source-build entry points are:

```sh
make check
make test
make build-release
make package-release
```

A source build does not require a custom runtime unless the developer explicitly invokes the optional Compose-provider parity target.

Local development binaries report lane `development`. They must not impersonate
stable or Current packages.

## Upgrading

Stable users upgrade with:

```sh
brew upgrade stephenlclarke/tap/devcontainer
```

Current users upgrade with:

```sh
brew upgrade stephenlclarke/tap/devcontainer-current
```

An upgrade changes only `devcontainer` files in the Homebrew prefix. It must not restart the Apple runtime, install a provider, or alter user container data.

## Uninstalling

Uninstall the stable package with:

```sh
brew uninstall --formula stephenlclarke/tap/devcontainer
```

or:

```sh
brew uninstall --formula stephenlclarke/tap/devcontainer-current
```

Uninstall removes only the formula-owned `devcontainer` payload. It must not remove:

- Apple's `container` package.
- Docker.
- `container-compose`.
- Images, containers, networks, volumes, caches, or Dev Container project data owned by another runtime.

## Troubleshooting

If an internally reachable container service resets its published host
connection, inspect Apple's system log:

```sh
container system logs --last 5m |
  grep -E 'forwarder|No route to host|connect failed'
```

`No route to host` from `container-runtime-linux` while the container VM
address is directly reachable means macOS denied the selected helper's Local
Network access. Open **System Settings → Privacy & Security → Local Network**
and enable the relevant `container` or `container-runtime-linux` entry, then
restart that runtime. If both stock and provider lanes are installed, authorize
each helper separately:

```sh
container system stop
container system start
```

Apple documents the privacy model in
[TN3179](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy)
and the expected `--publish` behavior in the
[`apple/container` how-to](https://github.com/apple/container/blob/main/docs/how-to.md#forward-traffic-from-localhost-to-your-container).

Before filing an issue, capture:

```sh
uname -m
sw_vers
command -v devcontainer
devcontainer version --format json
command -v container
container system version --format json
```

For an explicitly configured Compose provider, also capture:

```sh
/explicit/path/to/container-compose version --format json
```

Remove credentials, registry tokens, private registry names, proprietary configuration, SSH keys, certificates, and personal data before sharing logs.

Do not resolve a failed stock-runtime check by uninstalling Apple software or installing a custom fork. Report the mismatch so the compatibility boundary can be fixed or documented.
