# Installing devcontainer

<!-- markdownlint-disable MD013 -->

> Status: not yet available. This repository currently has no implementation, source package, signed binary, notarized archive, GitHub Release, or Homebrew formula. The commands and layouts below define the intended installation contract and must not be presented as working until the corresponding artifacts and verification workflows exist.

`devcontainer` will provide Dev Containers compatibility for Apple's stock `container` runtime on Apple-silicon Macs running macOS Tahoe. It will install as a standalone command and will not install, replace, relink, start, stop, or modify a container runtime.

## Installation Contract

The supported installation must preserve these boundaries:

- Apple's stock `container` runtime is installed separately from Apple.
- `devcontainer` uses the `container` executable selected by explicit configuration or `PATH`.
- Docker is optional and used for compatibility comparison or a separately selected backend.
- `container-compose` is optional and used only when the user explicitly selects or enables its provider.
- Installing `devcontainer` never installs `stephenlclarke/container`.
- Installing `devcontainer` never removes or replaces Apple's `container`.
- Installing `devcontainer` never installs `container-compose`.
- Installing `devcontainer` never links a Compose plugin into Apple's install root.
- Missing optional providers produce an actionable capability error, not an automatic installation or runtime replacement.

## Requirements

The planned prebuilt and Homebrew packages will require:

- Apple silicon (`arm64`).
- macOS Tahoe.
- Apple's stock `container` runtime for the Apple backend.
- A supported Xcode or Command Line Tools installation when required by Apple's runtime.

Optional integrations:

- Docker engine and the supported Docker Compose version for Docker comparison or Docker-backed execution.
- An explicitly installed `container-compose` executable for multi-service provider experiments.

The release notes and `devcontainer version --format json` will identify the exact versions used for release validation.

## Verify The Runtime Before Installation

The supported installer will not change runtime state. Users should verify their existing Apple installation first:

```sh
command -v container
container --version
container system version --format json
```

The stock-Apple backend must reject a runtime whose provenance identifies a custom distribution when strict stock mode is requested.

Do not remove Apple's package to install `devcontainer`. Do not install a custom runtime as a workaround for an installation check. Runtime compatibility gaps belong in the project's status ledger and issue template.

## Planned Homebrew Channels

The tap will publish two explicit channels:

| Formula | Channel | Version form | Intended use |
| --- | --- | --- | --- |
| `devcontainer` | Stable | `MAJOR.MINOR.PATCH` | Default immutable release |
| `devcontainer-current` | Current | `current.RUN.SHA12` | Opt-in newest validated `main` build |

The planned stable installation is:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer
```

The planned Current installation is:

```sh
brew tap stephenlclarke/tap
brew trust --tap stephenlclarke/tap
brew install --formula stephenlclarke/tap/devcontainer-current
```

These commands are documentation of the intended interface and will fail until the formulae are implemented and published.

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

## Planned Package Layout

The release archive will contain:

```text
devcontainer/
devcontainer/bin/
devcontainer/bin/devcontainer
devcontainer/resources/
devcontainer/resources/build-info.json
devcontainer/resources/devcontainer-sbom.spdx.json
```

Homebrew will install only the package payload under its own prefix and expose `bin/devcontainer`. It will not write under Apple's package prefix or `/usr/local/libexec/container-plugins`.

## Verify A Published Installation

When packages exist, verify the selected binary and embedded provenance:

```sh
command -v devcontainer
devcontainer version
devcontainer version --format json
devcontainer --help
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
- Selected backend.
- Detected Apple runtime version and distribution when an Apple command is executed.
- Optional Compose provider and its underlying runtime distribution when explicitly selected.

## Verify Release Integrity

Every published channel will include an archive, SHA-256 sidecar, SPDX SBOM, build-info file, and GitHub build-provenance attestation.

For a downloaded stable archive:

```sh
shasum -a 256 -c devcontainer-release-arm64.tar.gz.sha256
gh attestation verify devcontainer-release-arm64.tar.gz --repo stephenlclarke/devcontainer
tar -tzf devcontainer-release-arm64.tar.gz
```

The executable will be Developer ID signed and submitted to Apple's notary service before the archive checksum is calculated. Verify the extracted binary:

```sh
codesign --verify --strict --verbose=2 devcontainer/bin/devcontainer
codesign --display --verbose=4 devcontainer/bin/devcontainer
```

The project must not claim that a standalone executable is stapled. Apple's stapler does not support bare Mach-O command-line binaries. Release evidence will instead include an accepted notary submission for a ZIP containing the exact signed bytes distributed in the tarball.

## Backend Selection

The command-line interface will require explicit backend selection when more than one backend is available. The exact flags are not implemented yet; the intended behavior is:

```text
--backend apple
--backend docker
--compose-provider /absolute/path/to/container-compose
```

Configuration precedence should be:

1. Explicit command-line option.
2. Project configuration.
3. User configuration.
4. Safe capability detection.

Capability detection may select stock Apple only when its provenance is accepted. It must not download software, run Homebrew, change symlinks, edit an Apple install root, or switch a runtime service.

## Optional container-compose Provider

`container-compose` is not part of the base install. To use it, the user must install and configure it separately and accept its runtime compatibility.

The current supported `stephenlclarke/tap/container-compose` formula depends on a matched custom runtime. Installing that formula can replace the stock-Apple runtime selected by the shell. `devcontainer` must not suggest or execute that installation as an automatic fix.

Provider discovery will verify:

```sh
/explicit/path/to/container-compose version --format json
```

It will also inspect the active runtime provenance. If the provider uses a custom runtime, `devcontainer` will report that fact and will not describe the result as stock-Apple compatibility.

When a Dev Container configuration requires Compose and no explicit provider is usable, the command should stop before side effects with:

- The missing capability.
- The provider path that was checked.
- The detected runtime distribution.
- A link to project compatibility documentation.
- No automatic installation command.

## Source Builds

Source-build instructions will be added after the Swift package and Make targets exist. The intended entry points are:

```sh
make check
make test
make build-release
make package-release
```

A source build must not require a custom runtime unless the developer explicitly invokes the optional Compose-provider parity target.

Local development binaries will report lane `development`. They must not impersonate stable or Current packages.

## Upgrading

When the formulae exist, stable users will upgrade with:

```sh
brew upgrade stephenlclarke/tap/devcontainer
```

Current users will upgrade with:

```sh
brew upgrade stephenlclarke/tap/devcontainer-current
```

An upgrade changes only `devcontainer` files in the Homebrew prefix. It must not restart the Apple runtime, install a provider, or alter user container data.

## Uninstalling

The planned uninstall commands are:

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
