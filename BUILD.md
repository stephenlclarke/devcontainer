# Build and development design

## Current status

> [!IMPORTANT]
> The repository currently contains a bootstrap Swift package and parity
> manifest, not the planned runtime. There is no Makefile, Docker Engine
> service, Apple adapter, Compose dispatcher, packaged plug-in, sanitizer
> workflow, or coverage gate yet. Commands and targets below define the local
> development contract that implementation must provide.

Today, only the bootstrap package and manifest tooling can be invoked directly:

```console
swift package resolve
swift build
swift test
python3 Tools/parity/validate_manifest.py
python3 -m unittest discover -s Tools/parity -p 'test_*.py'
```

`python3 Tools/parity/validate_manifest.py --release` is expected to fail
while fixtures remain `planned`.

## Supported development hosts

| Work | Minimum host | Notes |
| --- | --- | --- |
| Swift build, unit tests, documentation, manifest checks | macOS 15, Xcode 26, Swift 6.2, Python 3, `make` | The package currently declares macOS 15 as its compilation floor. |
| ASan and TSan release gates | Apple-silicon macOS with the release Xcode/Swift toolchain | Separate clean build directories and jobs are required. |
| Apple runtime integration and parity | Physical Apple-silicon Mac, exact macOS 26 build, Xcode 26, Swift 6.2 or newer | Hosted workers and virtualized macOS are not runtime evidence. |
| VS Code end-to-end parity | Physical Apple-silicon Mac with a logged-in GUI session | Pin the VS Code application and Dev Containers VSIX exactly. |

Runtime hosts need hardware virtualization, a dedicated test user, and enough
memory/disk for multiple Apple container VMs, images, build contexts, raw
logs, and retained artifacts. A practical runner baseline is 32 GB RAM and
100 GB free disk; the measured suite may raise this requirement.

Public pull requests run only hosted, non-runtime checks. Physical runtime
runners accept trusted exact commits after hosted checks pass and must not
hold unrelated developer credentials.

## Source and dependency layout

The planned package graph preserves a hard provider boundary:

```text
DevContainerModel
  -> DevContainerRuntimeSPI
  -> DevContainerCore
     -> DevContainerState
     -> DevContainerDockerAPI
     -> DevContainerAppleRuntime
  -> DevContainerService

DockerComposeDispatcher
  -> control API client
  -> pinned upstream Docker Compose executable
  -> optional external `container compose` process

DevContainerCLI
  -> control API client
  -> pinned official @devcontainers/cli launcher
```

Only `DevContainerAppleRuntime` may import Apple runtime products.
`container-compose` remains an external executable and must not appear in
`Package.swift`, `Package.resolved`, linked products, or service startup
requirements. The runtime-neutral core must not import `ComposeCore`.

For a stock Apple release, the project pins an exact tagged
`apple/container` revision and accepts the exact `containerization` version
selected by that release. It must not independently override
`containerization`. The optional Compose provider records its own executable
commit and matched fork revisions at probe time. The two dependency graphs
are never resolved into one Swift build.

If development exposes a defect that must be fixed in
`stephenlclarke/container`, `stephenlclarke/containerization`, or
`stephenlclarke/container-compose`, the fix belongs in that owning repository.
It must include a regression test and be submitted as a focused pull request.
Only after review does `devcontainer` update its exact pin and provenance
ledger and rerun the affected parity matrix. Never patch a vendored or copied
upstream source tree in this repository. No such upstream pull request is part
of the current bootstrap work.

The complete initial provenance ledger is in
[`COMPATIBILITY.md`](COMPATIBILITY.md). Release builds must use
`Package.resolved`, verify that it is unchanged after resolution, and record:

- repository URL, version, commit, and tree or artifact digest;
- Xcode, Swift, SDK, macOS product, and macOS build versions;
- target architecture and build configuration;
- signing identity and notarization result where applicable;
- Docker, Dev Containers, Compose, Apple runtime, and provider fingerprints.

## Planned Make interface

The Makefile will be an orchestration layer over checked-in scripts. Targets
must be non-interactive, deterministic, and safe to rerun.

| Target | Planned behavior |
| --- | --- |
| `make bootstrap` | Verify Xcode, Swift, Python, formatting/lint tools, and required local utilities without installing or replacing an Apple runtime. |
| `make resolve` | Resolve exact Swift dependencies and fail if the lock file changes unexpectedly. |
| `make build` | Build all first-party libraries and executables in debug mode. |
| `make build-release` | Build optimized release artifacts with reproducible version/provenance inputs. |
| `make test` | Run the hosted-safe unit and contract test aggregate. |
| `make test-unit` | Run Swift and Python unit tests only. |
| `make test-contract` | Run Docker DTO, HTTP, stream, error, state, provider-boundary, and recorded-traffic contract tests using fakes. |
| `make test-integration` | Run local host-side integration tests that do not require the Apple VM runtime. |
| `make test-asan` | Clean, rebuild, and run the sanitizer-eligible Swift suite with Address Sanitizer. |
| `make test-tsan` | Clean, rebuild, and run the sanitizer-eligible concurrency suite with Thread Sanitizer. |
| `make coverage` | Run first-party Swift tests with coverage and write machine-readable plus HTML reports. |
| `make coverage-check` | Fail below 90% first-party Swift line coverage after documented exclusions. |
| `make format` | Apply the repository's Swift and documentation formatters. |
| `make format-check` | Verify formatting without modifying files. |
| `make lint` | Run Swift lint/static analysis, Python checks, Markdown checks, and forbidden-dependency checks. |
| `make docs` | Build DocC and validate Markdown links and Mermaid blocks. |
| `make serve-docs` | Serve the local DocC output for review. |
| `make parity-manifest` | Validate the parity manifest in development mode. |
| `make parity-docker` | Run the pinned real-Docker oracle lane and capture raw/normalized evidence. |
| `make parity-apple-stock` | Run the stock Apple lane after exact provenance and clean-host checks. |
| `make parity-apple-compose` | Run the optional provider lane after exact provider/fork checks. |
| `make parity` | Compare all three lanes and fail on any semantic difference. |
| `make parity-vscode` | Run pinned VS Code/VSIX open, attach, terminal, ports, rebuild, reopen, and cleanup flows. |
| `make parity-release` | Require every manifest fixture to be implemented and all release evidence complete. |
| `make check` | Hosted-safe aggregate: resolve verification, format check, lint, unit/contract tests, coverage check, docs, and development manifest validation. |
| `make runtime-check` | Trusted physical-runner aggregate: Apple integration, fault recovery, ASan/TSan where host instrumentation applies, and parity. |
| `make package` | Assemble signed plug-in/service/CLI artifacts, notices, SBOM, checksums, and provenance without installing them. |
| `make release-check` | Run `check`, sanitizer gates, runtime/parity gates, packaging verification, and the release manifest validator. |
| `make clean` | Remove only repository-owned build and report directories after validating their exact paths. |

`make check` must never require `container compose`, Docker Engine, an Apple
runtime, privileged access, or network access after dependencies and pinned
fixtures are available. Provider-specific targets may skip only when invoked
outside a release aggregate; `make release-check` treats an unavailable
required provider or runner as a failure.

## Swift build profiles

Use distinct SwiftPM scratch paths so instrumentation and cached objects cannot
cross-contaminate jobs:

```console
swift build --scratch-path .build/debug
swift test --scratch-path .build/test
swift test --scratch-path .build/asan --sanitize=address
swift test --scratch-path .build/tsan --sanitize=thread
swift test --scratch-path .build/coverage --enable-code-coverage
```

The final scripts may add strict warning and frontend settings after verifying
them with the pinned Swift compiler. Release artifacts are built separately
from sanitizer and coverage objects.

Address Sanitizer and Thread Sanitizer are mutually exclusive jobs. A clean
ASan result does not substitute for TSan, and vice versa. A sanitizer crash,
race report, leak attributable to first-party code, unexpected test
termination, or suppression not reviewed into the repository fails the gate.

### Address Sanitizer scope

ASan covers all first-party Swift unit and contract tests, Docker HTTP parsing,
stream framing, tar/archive validation, state migration, process management,
and host-side adapter integration that can run under instrumentation.
Malformed body, archive, label, and multiplexed-stream fuzz/regression corpora
must run in this job.

Prebuilt Apple services and guest processes cannot be assumed to be
instrumented. Their live parity tests remain mandatory as a separate physical
runner gate; the lack of instrumentation inside an upstream binary is not an
ASan waiver for bridge code.

### Thread Sanitizer scope

TSan covers project and resource actor coordination, lease renewal, event
broadcast/reconnect, exec cancellation, concurrent HTTP requests, SQLite
access, process termination, and startup reconciliation. The concurrency
stress suite must use deterministic barriers where possible and repeat
contention scenarios sufficiently to expose scheduling-sensitive defects.

Runtime tests that cannot execute reliably under TSan still run normally on
the physical lane, while their provider-neutral coordinator and recovery
logic must have an instrumented fake-runtime equivalent. Any race in
first-party host code blocks release.

## Coverage policy

The minimum is **90% line coverage for aggregate first-party Swift source**.
Coverage is measured from tests, not preview/example execution.

Included:

- every first-party Swift library and executable target;
- error and recovery paths;
- provider selection and no-split-brain enforcement;
- Docker request/response and stream handling;
- state schema, migration, reconciliation, and cancellation logic;
- stock and optional-provider adapters through fakes or recorded contracts.

Excluded:

- generated Swift/protobuf sources;
- vendored dependencies;
- generated DocC and packaging output;
- fixture source intended to compile inside a guest;
- platform-unreachable defensive traps, but only with a documented
  source-level exclusion reviewed in the same change.

The coverage script should use `swift test --enable-code-coverage`, discover
the produced profile with the pinned toolchain, and export LCOV plus an HTML
report with `llvm-cov`. `make coverage-check` parses the machine-readable
report and fails when aggregate line coverage is below 90%. New code may not
reduce the release branch below the threshold. Generated-file filtering and
every explicit exclusion are checked into source control.

Coverage does not replace semantic parity. Code can meet 90% and still fail a
Docker, Dev Containers, Compose, recovery, or VS Code release gate.

## Local development flow

Once the planned Make interface exists, a normal source change should use:

```console
make bootstrap
make check
make test-asan
make test-tsan
```

Changes to runtime translation, provider selection, Compose labels, Docker
wire behavior, state/recovery, or lifecycle semantics additionally require
the relevant physical-runner parity fixtures before merge to a release
branch.

Local runtime work uses a dedicated Docker context or explicit `DOCKER_HOST`
pointing to the user-owned compatibility socket. Scripts must not replace the
user's default context, bind a TCP listener, overwrite an existing Docker
Compose plug-in, or install Stephen's runtime fork implicitly.

## Physical runner topology

Use three isolated profiles:

1. **Docker oracle:** pinned real Docker Engine, Docker CLI, Docker Compose,
   and official Dev Containers CLI.
2. **Stock Apple:** unmodified tagged `apple/container` with its exact
   `containerization` resolution; upstream Docker Compose talks to the
   compatibility socket.
3. **Apple Compose:** exact `container-compose` executable with its declared
   Stephen fork commits; the provider is selected explicitly.

Separate hosts are preferred for the two Apple profiles. If hardware is
shared, jobs are serialized and a signed preparation step must prove:

- no runtime or provider processes from the previous profile;
- no project-labelled containers, networks, volumes, sockets, or leases;
- exact binary and source fingerprints for the next profile;
- a clean user state directory and bounded evidence directory;
- enough free disk and memory for the complete fixture.

Each job retains raw output before normalization, backend capability reports,
runtime logs, state/reconciliation diagnostics, resource inventories before
and after cleanup, sanitizer/coverage reports where applicable, and the exact
commit under test. A failed cleanup quarantines the runner.

## Parity and release sequence

A trusted release candidate runs:

```console
make check
make test-asan
make test-tsan
make parity-docker
make parity-apple-stock
make parity-apple-compose
make parity
make parity-vscode
make package
make release-check
```

The three parity lanes use the same fixture revision and official
`@devcontainers/cli` pin. Comparison may normalize only the fields allowed by
[`COMPATIBILITY.md`](COMPATIBILITY.md) and the parity manifest. Required
differences are never waived.

Stable artifacts are eligible for publication only when:

- all parity fixtures are `implemented`;
- both Apple provider lanes equal the real-Docker oracle within the claimed
  surface;
- VS Code end-to-end behavior passes;
- Swift line coverage is at least 90%;
- ASan and TSan are clean;
- failure-injection and restart reconciliation pass;
- package contents, signatures, notices, SBOM, checksums, and provenance
  verify from a clean checkout;
- the release validator accepts the exact evidence manifest.

Until those conditions are met, all compatibility remains pre-implementation
or candidate status regardless of successful local experiments.
