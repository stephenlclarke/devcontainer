# Build and development guide

## Current status

The repository contains the implemented Swift package, Docker Engine
compatibility service, stock Apple runtime adapter, optional external
`container-compose` provider, command-line tools, tests, parity harness, DocC
site, and package/Homebrew tooling.

Hosted-safe development is fully automated. Live runtime parity remains a
separate trusted-runner activity: building or testing this package never
starts, stops, installs, or replaces a developer's Apple container runtime.

## Development host

| Work | Required host |
| --- | --- |
| Build, tests, coverage, sanitizers, and DocC | Apple-silicon macOS 15 or later, Xcode 26/Swift 6.2, Python 3, GNU Make |
| Live stock Apple parity | Physical Apple-silicon macOS 26 host with the pinned stock `container` release |
| Live Compose parity | Reserved physical Apple-silicon host with pinned `container-compose` and its declared matched stack; the workflow serializes this lane when sharing the stock/Docker host |
| VS Code end-to-end parity | Physical Apple-silicon host with a logged-in GUI session and the pinned VS Code/VSIX artifacts |

Run the non-mutating prerequisite probe first:

```console
make bootstrap
```

It requires `swift`, `python3`, `make`, and `git`, and reports missing optional
quality tools. The full local quality aggregate also expects `actionlint`,
`markdownlint`, `shellcheck`, `swiftformat`, and `swiftlint`.

## Package structure

The package targets enforce the provider boundary described in
[DESIGN.md](DESIGN.md):

```text
DevContainerVersionGenerator -> GenerateDevContainerVersion -> DevContainerModel
DevContainerModel -> DevContainerRuntimeSPI
DevContainerRuntimeSPI -> DevContainerState -> DevContainerCore
DevContainerCore -> DevContainerDockerAPI
DevContainerRuntimeSPI -> DevContainerAppleRuntime
DevContainerRuntimeSPI -> DevContainerComposeProvider
DevContainerDockerAPI -> DevContainerTestSupport
```

The three executable products are:

| Product | Purpose |
| --- | --- |
| `devcontainer` | Configure, diagnose, and inspect the local compatibility installation and durable provider claims |
| `devcontainer-engine` | Serve the tested Docker Engine API subset on a user-owned Unix socket and translate it to stock Apple runtime operations |
| `devcontainer-compose` | Docker Compose-compatible dispatcher that selects upstream Docker Compose or an explicitly configured external `container-compose` executable |

Only `DevContainerAppleRuntime` links Apple runtime products. The Compose
provider invokes an executable and has no `ComposeCore` or custom Apple-stack
source dependency.

`Package.resolved` is authoritative. CI copies it, resolves the package, and
fails if resolution changes the copy. Use:

```console
make resolve
git diff --exit-code -- Package.resolved
```

## Build commands

```console
make build
make build-release
```

`make build` uses exact resolved dependencies. `make build-release` injects the
current commit and release lane through the `GenerateDevContainerVersion`
SwiftPM build-tool plug-in. The generator declares `Makefile` as an input, so a
version change invalidates SwiftPM's generated-source cache.
`DEVCONTAINER_VERSION` in `Makefile` is the only editable product version.

Useful direct commands are:

```console
swift build --disable-automatic-resolution
swift run --disable-automatic-resolution devcontainer version --format json
swift run --disable-automatic-resolution devcontainer-engine --help
```

## Hosted-safe test workflow

The normal development loop is:

```console
make format-check
make lint
make test
make coverage-check
make docs
make parity-manifest
```

Or run the aggregate:

```console
make check
```

`make check` is deliberately safe for a workstation that is also running
unrelated Apple containers. It uses in-memory or process fakes and temporary,
user-owned sockets; it does not invoke a live `container system` operation.

The Swift test harness writes complete output to `.build/swift-test.log`,
retries only the identified SwiftPM helper signal-13 failure, and refuses that
fallback during coverage. The current suite covers model, state, core,
Docker-wire, Apple-adapter, Compose-provider, service-process, fault, and
concurrency behavior.

Host-process integration tests are opt-in:

```console
make test-integration
```

They start only repository-built processes with isolated temporary paths.

## Coverage

```console
make coverage-check
```

The coverage pipeline:

1. runs tests in `.build/coverage` with code coverage enabled;
2. builds and exercises both CLI products as instrumented executables;
3. merges every non-empty profile with `llvm-profdata`;
4. exports LLVM JSON;
5. writes `coverage.lcov` and Sonar generic `coverage.xml` from the same unique
   executable-line map;
6. fails below 90% aggregate first-party line coverage.

CI additionally supplies `SWIFT_COVERAGE_BASE`, so the same command fails below
90% coverage on executable lines changed since the pull-request merge base or
the protected-main comparison commit:

```console
make coverage-check SWIFT_COVERAGE_BASE=origin/main
```

Uncovered changed lines are printed as paths and line numbers and become GitHub
source annotations. Percentages are compared before display rounding.

## Memory and concurrency checks

The sanitizer interface intentionally matches `container-compose`:

```console
make test-asan
make test-tsan
```

AddressSanitizer uses `.build/asan`; ThreadSanitizer uses `.build/tsan`. Both
run the complete Swift suite without test parallelism and retain full logs in
`.build/swift-asan.log` and `.build/swift-tsan.log`. A sanitizer diagnostic,
test failure, empty run, or unaccepted helper termination fails the target.

These checks cover first-party host code. They do not claim that an upstream
Apple service or guest binary was sanitizer-instrumented.

## CLI development

Build first, then use an isolated configuration and state root:

```console
export DEVCONTAINER_CONFIG="$PWD/.build/manual/config.toml"
export DEVCONTAINER_STATE="$PWD/.build/manual/state.sqlite"
export DEVCONTAINER_SOCKET="$PWD/.build/manual/docker.sock"

.build/debug/devcontainer configure \
  --backend stock \
  --compose-provider docker \
  --socket "$DEVCONTAINER_SOCKET"
.build/debug/devcontainer context --format shell
.build/debug/devcontainer doctor --format json
.build/debug/devcontainer diagnostics \
  --output "$PWD/.build/manual/devcontainer-diagnostics.tar.gz"
```

The `context` command prints an explicit `DOCKER_HOST`; it does not change the
user's default Docker context. `backend show`, `backend set`, and
`backend reset` operate on a project-scoped durable claim. Reset fails while
the state database still owns resources.

To test dispatch without a real Compose installation, set
`DEVCONTAINER_DOCKER_BIN` or `DEVCONTAINER_COMPOSE_BIN` to a deterministic
fixture executable. The selected external command receives a scrubbed
environment and an explicit compatibility socket.

## Parity harness

Manifest and harness tests are hosted-safe:

```console
make parity-manifest
python3 -m unittest discover Tools/parity
```

Live lane targets are intentionally explicit:

```console
make parity-docker
make parity-apple-stock
make parity-apple-compose
make parity
make parity-vscode
```

Each lane requires its dedicated runner label, exact pin preflight, isolated
application root, socket, state database, workspace, evidence directory, and
resource prefix. Do not run a live lane on a host being used by another
container project unless its owner has reserved the runner for that lane.

The aggregate stores raw and normalized observations under
`.build/parity`. Comparison permits only the nondeterminism defined in the
manifest. Cleanup proof is part of a passing result.

`make parity-release` additionally requires every release-scoped fixture and
recording. It fails when required physical evidence is absent.

## Documentation

```console
make docs
make serve-docs
```

DocC output is written to `_site` with the configured GitHub Pages base path.
The workflow builds from exact resolved dependencies, uploads the static site,
and deploys it to
[stephenlclarke.github.io/devcontainer](https://stephenlclarke.github.io/devcontainer/).

## Packaging and Homebrew

Create and verify an unsigned development archive without installing it:

```console
make package
```

The arm64 archive in `dist` contains all three executables, the
`container-devcontainer` plug-in entry point, launchd template, Apache license,
complete reviewed legal texts for every exact SwiftPM dependency, build
metadata, and an SPDX 2.3 SBOM. The checked-in dependency-license ledger must
match `Package.resolved` exactly. The packaging script writes a SHA-256
checksum and machine-readable verification result. Archive entries are sorted
and normalize ownership plus timestamps to the source commit epoch; two
packages from identical staged bytes are byte-for-byte identical.

Render stable and Current formula candidates:

```console
make homebrew-formula
make homebrew-formula-current DEVCONTAINER_PACKAGE_RUN_NUMBER=1
```

Stable packaging requires Developer ID signing and an accepted notarization
record:

```console
make package-release
```

Run `package-release` only on a reserved trusted release host: its prerequisite
is the complete live parity and sanitizer release gate. The command fails
closed unless the required runtime evidence, release identity, and notary
profile are available. It does not install or replace `container` or
`container-compose`.

## Version changes

The version selectors are shared with the Compose project:

```console
make release-version VERSION_SELECTOR=--+
make prepare-release VERSION_SELECTOR=--+
```

`--+`, `-+-`, and `+--` select patch, minor, and major increments. The first
command previews; the second updates the authoritative Makefile value.

## Cleaning

```console
make clean
```

Cleaning is implemented by `Tools/ci/safe-clean.py`, which accepts only the
checked-in repository-owned build and report paths. It never removes a home
directory, runtime application root, external checkout, or live container
resource.

## Upstream fixes

If live evidence identifies a defect in
`stephenlclarke/container`, `stephenlclarke/containerization`, or
`stephenlclarke/container-compose`, fix it in the owning repository with a
focused regression test and pull request. This repository then updates the
reviewed pin and reruns every affected lane. Do not vendor or patch a copied
upstream tree here.
