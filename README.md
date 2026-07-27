# devcontainer

<!-- markdownlint-disable MD033 -->
<p>
  <img align="left" hspace="20" src="docs/images/devcontainer-icon.png" width="147" alt="devcontainer icon: a blue glass cube in front of three frosted container trays" />
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Quality Gate Status" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=alert_status" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Bugs" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=bugs" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Code Smells" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=code_smells" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Coverage" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=coverage" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Duplicated Lines (%)" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=duplicated_lines_density" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Lines of Code" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=ncloc" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Reliability Rating" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=reliability_rating" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Security Hotspots" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=security_hotspots" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Security Rating" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=security_rating" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Technical Debt" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=sqale_index" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Maintainability Rating" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=sqale_rating" /></a>
  <a href="https://sonarcloud.io/summary/new_code?id=stephenlclarke_devcontainer"><img alt="Vulnerabilities" src="https://sonarcloud.io/api/project_badges/measure?project=stephenlclarke_devcontainer&metric=vulnerabilities" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/codeql.yml?query=branch%3Amain"><img alt="CodeQL" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/codeql.yml/badge.svg?branch=main" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/ci.yml?query=branch%3Amain"><img alt="CI" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/ci.yml/badge.svg?branch=main" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/actions/workflows/docs.yml?query=branch%3Amain"><img alt="Documentation" src="https://github.com/stephenlclarke/devcontainer/actions/workflows/docs.yml/badge.svg?branch=main" /></a>
  <a href="https://github.com/stephenlclarke/devcontainer/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/stephenlclarke/devcontainer?label=release" /></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" /></a>
  <img alt="Repo Visitors" src="https://visitor-badge.laobi.icu/badge?page_id=stephenlclarke.devcontainer" />
</p>
<br clear="left" />
<br>
<!-- markdownlint-enable MD033 -->

Run VS Code-compatible Development Containers on Apple silicon through stock [`apple/container`](https://github.com/apple/container), with first-class support for [`container-compose`](https://github.com/stephenlclarke/container-compose).

> [!IMPORTANT]
> This repository contains a functional development candidate, not a stable
> release. The Docker Engine bridge, stock Apple runtime adapter, optional
> `container-compose` provider, package builder, Homebrew formula generator,
> DocC site, and automated quality gates are implemented. Real Docker, stock
> Apple `container`, and the custom Apple Compose lane pass all 18 checked-in
> CLI parity fixtures locally with zero normalized differences. The pinned real
> VS Code/Dev Containers attach, rebuild, reopen, lifecycle, terminal, extension,
> port-forwarding, and cleanup fixture also passes in all three lanes.

## Design promise

The project keeps the official [Dev Containers](https://github.com/devcontainers) toolchain above a local Docker Engine compatibility service. VS Code and the reference [`@devcontainers/cli`](https://github.com/devcontainers/cli) remain unmodified; the service translates their tested Docker API subset into Apple-native runtime operations.

```mermaid
flowchart LR
    VS["VS Code Dev Containers"] --> DC["Official @devcontainers/cli"]
    DC --> Docker["Unmodified Docker CLI"]
    Docker --> API["Local Docker Engine compatibility socket"]
    API --> Core["Provider-neutral runtime core"]
    Core --> Stock["Stock apple/container"]
    DC --> ComposeChoice{"Compose provider"}
    ComposeChoice --> DockerCompose["Docker Compose over the bridge"]
    ComposeChoice --> ContainerCompose["container-compose adapter"]
    DockerCompose --> API
    ContainerCompose --> Stock
```

The `container-compose` integration is first-class but independently installed. The core does not import `ComposeCore`, and installing this project must never silently replace stock Apple `container` with my matched fork stack.

## Compatibility target

| Lane | Purpose | Stable-release requirement |
| --- | --- | --- |
| Real Docker | Behavioral oracle using pinned Docker Engine, Docker Compose, and `@devcontainers/cli` | Complete raw and normalized evidence |
| Stock Apple | Official `apple/container` only; Docker Compose uses the compatibility API | Zero semantic differences in every claimed fixture |
| Apple Compose | `container-compose` selected as the Compose provider, with its exact runtime provenance recorded | Zero semantic differences in every claimed fixture |

The test plan covers image, Dockerfile, Features, users, environment, lifecycle hooks, workspace mounts, ports, reuse, Compose services, networks, volumes, failure recovery, and real VS Code attach/rebuild behavior. See [TESTING.md](TESTING.md) and [COMPATIBILITY.md](COMPATIBILITY.md).

Stock `apple/container` 1.1.0 does not expose create-time hostname or Docker security-option fields. Requests containing those fields fail before container or mount side effects; they are not silently weakened and are outside the stock 1.1.0 compatibility claim. A separately fingerprinted enhanced runtime may advertise and enforce them through native `--hostname` and `--security-opt` flags.

## Project layout

| Path | Purpose |
| --- | --- |
| [DESIGN.md](DESIGN.md) | Detailed architecture, data flow, runtime boundaries, security, and delivery phases |
| [TESTING.md](TESTING.md) | Three-lane Docker/Apple/Compose differential test harness |
| [QUALITY.md](QUALITY.md) | Software-quality analysis, measurable gates, and supply-chain controls |
| [BUILD.md](BUILD.md) | Current local build, test, coverage, sanitizer, parity, and package commands |
| [INSTALL.md](INSTALL.md) | Source, prebuilt, Homebrew, provider, and uninstall contract |
| [RELEASE.md](RELEASE.md) | CI/CD, GitHub Pages, release authority, and Homebrew tap design |
| [COMPATIBILITY.md](COMPATIBILITY.md) | Compatibility contract and explicit claim policy |
| [SECURITY.md](SECURITY.md) | Private vulnerability reporting and supported-version policy |
| [Tests/Parity](Tests/Parity) | Machine-readable parity manifest and executable differential fixtures |
| `Sources/DevContainerDockerAPI` | Docker Engine API compatibility router |
| `Sources/DevContainerAppleRuntime` | Stock Apple runtime adapter and process/port/archive support |
| `Sources/DevContainerService` | Unix-socket compatibility engine |
| `Sources/DevContainerComposeProvider` | Optional external `container-compose` dispatcher |

## Development

Requirements are Xcode 26, Swift 6.2 or newer, Python 3, and `make`.
Runtime parity additionally requires a physical Apple-silicon Mac on macOS 26,
stock Apple `container`, real Docker, the pinned Dev Container CLI, and the
selected Compose provider.

The stock multi-service path uses the upstream `docker-compose` client over
this project's compatibility socket. The separately selected
`container-compose` provider remains optional and independently installed.

```console
make check
make test
make docs
make serve-docs
DEVCONTAINER_VSCODE_LIVE=1 make parity-vscode-docker
```

Use `devcontainer diagnostics --output devcontainer-diagnostics.tar.gz` to
create a bounded, privacy-redacted support archive whose JSON manifest is
printed before the archive is written.

Live runtime tests are deliberately not run on public pull-request code or GitHub-hosted macOS. They execute on an isolated physical runner only after a trusted exact commit has passed hosted checks.

## Documentation

The generated [DocC site](https://stephenlclarke.github.io/devcontainer/)
contains the public Swift API reference and architecture articles. GitHub Pages
publishes it from the exact `main` commit that passes the documentation
workflow.

## Primary upstream references

The design follows the maintained sources in the [Dev Containers GitHub organization](https://github.com/devcontainers):

- [Development Containers Specification](https://github.com/devcontainers/spec)
- [Dev Container CLI reference implementation](https://github.com/devcontainers/cli)
- [Dev Container Features](https://github.com/devcontainers/features)
- [Dev Container Templates](https://github.com/devcontainers/templates)
- [Dev Container Images](https://github.com/devcontainers/images)
- [Dev Container CI](https://github.com/devcontainers/ci)
- [containers.dev specification and supporting tools](https://containers.dev/)

Runtime references are [Apple container](https://github.com/apple/container), [Apple containerization](https://github.com/apple/containerization), and the [Apple container API documentation](https://apple.github.io/container/documentation/). VS Code behavior is documented in [Developing inside a Container](https://code.visualstudio.com/docs/devcontainers/containers).

## Installation

There is no supported package yet. Development archives and formulae can be
built with `make homebrew-formula`. The intended stable installation is:

```console
brew tap stephenlclarke/tap
brew trust --formula stephenlclarke/tap/devcontainer
brew install stephenlclarke/tap/devcontainer
devcontainer plugin register
container devcontainer doctor
```

The stable formula installs this project with upstream Docker CLI and Docker
Compose protocol-client dependencies; it does not install a container runtime.
Plug-in registration is an explicit, reversible symlink into the active
runtime's reported install root, and it never replaces a foreign registration.
`container-compose` remains an explicit optional installation and provider choice. See
[INSTALL.md](INSTALL.md) for registration, verification, and migration.

## Independence and trademarks

This is an independent open-source project. It is not affiliated with or endorsed by Apple, Docker, Microsoft, or the Dev Containers maintainers. Apple, Docker, Visual Studio Code, and other marks belong to their respective owners.

## License

Licensed under [Apache License 2.0](LICENSE), matching `apple/container` and
`apple/containerization`. The package builder includes third-party notices,
deterministic build metadata, checksums, and an SPDX 2.3 SBOM.
