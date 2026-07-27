# Compatibility contract

## Current status

> [!IMPORTANT]
> This repository is a functional development candidate, not a supported
> release. It contains the Docker Engine compatibility service, stock Apple
> runtime adapter, and optional Compose dispatcher. All checked-in fixtures
> pass locally against real Docker and the custom Apple Compose stack. A clean
> isolated run against the exact stock Apple package and live VS Code
> attach/rebuild evidence are still release blockers, so no row below is yet
> marked `supported`.

This document is the support and claim ledger for `devcontainer`. A stable
release may claim only the exact combinations and behaviors that have passed
the three-lane differential suite in
[`Tests/Parity/manifest.json`](Tests/Parity/manifest.json). Version proximity,
successful compilation, unit tests, or an apparently successful container
startup are not compatibility evidence.

## Claim vocabulary

| State | Meaning |
| --- | --- |
| `planned` | Design and fixture exist, but there is no functional support claim. |
| `candidate` | Implemented on a development branch and under test; not a release claim. |
| `supported` | The exact recorded component fingerprint passed every required parity, quality, sanitizer, and recovery gate. |
| `unsupported` | Outside the bounded compatibility surface or failed a required semantic assertion. |

No row may move to `supported` while a required fixture is skipped, retried
until it passes, normalized to hide a difference, or waived.

## Provider lanes

| Lane | Runtime and Compose path | Installation boundary | Status |
| --- | --- | --- | --- |
| `docker` | Official `@devcontainers/cli` and Docker Compose against a real Docker Engine | Independent behavioral oracle | `candidate` |
| `apple-stock` | Official `@devcontainers/cli` and upstream Docker Compose against this project's Docker Engine bridge, then an unmodified tagged `apple/container` runtime | Required runtime lane; no Stephen fork or `container-compose` package dependency | `candidate` |
| `apple-compose` | The same Docker inspection, exec, copy, attach, and event bridge, with `container compose` selected for Compose planning and lifecycle | Optional external executable; separately installed and provenance-checked | `candidate` |

The stock lane must remain fully functional when `container compose` is not
installed. The optional provider is nevertheless a first-class release lane:
all Compose-backed Dev Container fixtures must pass through both
`apple-stock` and `apple-compose` before a stable release.

`container-compose` is not a Swift package dependency of the runtime-neutral
core, is not copied into this product, and is not a service startup
prerequisite. Its adapter discovers and launches an explicitly configured
executable using argv-based process creation. It must not import `ComposeCore`
or another implementation module.

## Pinned design-time provenance

These pins define the initial contract-capture matrix. They do not constitute
support until the release gates pass. Release evidence must additionally
record the downloaded binary digest, signing identity where applicable,
platform triple, and the output of each component's machine-readable version
command.

| Component | Version or ref | Exact source provenance | Role |
| --- | --- | --- | --- |
| `@devcontainers/cli` | `0.88.0` | `58be9705761d276b5076525438bbe73642f521d5` | Unmodified reference client |
| Docker CLI | `29.6.1` | Binary digest and package provenance are not yet recorded | Unmodified client used by the reference CLI |
| Docker Engine | `29.2.1`, API `1.53` | Binary/image digest is not yet recorded | Behavioral oracle |
| Docker Compose | `5.3.1` | Binary digest and source commit are not yet recorded | Oracle and stock Compose client |
| `apple/container` stable | `1.1.0` | Annotated tag object `82fc9a5ba73c34c478ce15958bb75dbb45c67e3b`; source commit `5973b9cc626a3e7a499bb316a958237ebe14e2ed` | Initial stable stock lane |
| `apple/containerization` for `container` 1.1.0 | `0.35.0` | Apple resolution/tag object `44bec8b9933bc491d0cbf44abac90a1f6aaebf6b`; source commit `0334a3e790bbed50420de71cd0d706191bdf84d1` | Must be inherited from the Apple `container` resolution |
| `apple/container` main evidence | No release claim | `d1d763530df3c6a326dbae7f0c0a59a335808045` | Forward-compatibility evidence only |
| `apple/containerization` for that main commit | `0.38.0` | Source commit `d9868bb657fac3b55ed5dcec97c8eb8a08e78bf5` | Exact dependency selected by that Apple commit |
| `apple/containerization` research head | No release claim | `74ace148ded72f7bb3c878b142e4962ae668adf4` | API research only; not mixed into an Apple release lane |
| `container-compose` stable | `0.10.0` | Annotated tag object `1b414c36d9a66c25d86466661d738015891ab20e`; source commit `42b737dcda830f79b3f0993212e97fefe179f427` | Initial optional provider |
| Stable provider's `stephenlclarke/container` | Revision | `ea20b242e763eb3e64d412c3dc2bbaa69639d2f4` | Exact fork dependency declared by `container-compose` 0.10.0 |
| Stable provider's `stephenlclarke/containerization` | Revision | `6aa6e803539c59ce754c55628e5417356216b297` | Exact fork dependency declared by `container-compose` 0.10.0 |
| `container-compose` main evidence | No release claim | `517be1f08abfc4f48849c78071d428c5b03f9b8d` | Forward provider evidence only |
| Main provider's `stephenlclarke/container` | Revision | `221fafc24ebd19502f4553e0b5d38c14be3f2b22` | Exact fork dependency at the recorded provider commit |
| Main provider's `stephenlclarke/containerization` | Revision | `164088e02e16ed80e536d0c59822b09931d213df` | Exact fork dependency at the recorded provider commit |
| VS Code | Pending exact pin | Version, commit/build, distribution digest, and signing identity are not yet recorded | End-to-end client |
| VS Code Dev Containers extension | Pending exact pin | Marketplace version, VSIX digest, and embedded CLI identity are not yet recorded | End-to-end reference integration |
| macOS and Xcode | Pending exact release image | Product/build versions must be captured in the evidence manifest | Host and toolchain |

Missing digests and pending VS Code, extension, macOS, and Xcode pins are
release blockers. Moving branch heads are never stable compatibility claims.
Evidence against a `main` commit is maintained separately from the stable
matrix and cannot replace it.

The Apple adapter must take `containerization` from the selected
`apple/container` release resolution. Depending on a second independently
selected `containerization` revision would create an untested ABI/source
combination and is forbidden.

### Upstream fix ownership

Any future fix required in `stephenlclarke/container`,
`stephenlclarke/containerization`, or
`stephenlclarke/container-compose` must be implemented in its owning
repository, protected by a regression test there, and delivered as a focused
pull request for review. After that change is reviewed, this repository may
pin and record the exact accepted commit and rerun the complete affected
parity lane.

This repository must never carry a patched vendor directory, copied upstream
source, ad hoc diff, or release-only binary replacement for those projects.
There is no upstream pull request associated with the current
pre-implementation documentation.

## Docker Engine API bounds

There is currently no implemented or advertised Docker API version.

The initial contract-capture envelope is:

- minimum version under investigation: `1.41`;
- maximum version under investigation: `1.53`;
- pinned oracle version: `1.53`, from Docker Engine `29.2.1`;
- versions above `1.53`: out of scope until separately pinned and tested;
- versions below `1.41`: out of scope for the initial release.

A release will advertise one contiguous, tested subset of `1.41...1.53`.
Unversioned routes and every advertised version prefix must negotiate and
return Docker-compatible status codes, JSON fields, headers, event ordering,
and stream framing. Advertising a version means every endpoint needed by the
pinned Dev Containers and Compose clients works at that version; it does not
mean the bridge is a general-purpose Docker daemon.

The bounded endpoint surface is:

- ping, version, information, and API negotiation;
- container list, create, inspect, start, stop, kill, wait, remove, logs, and
  attach;
- exec create, start, resize, inspect, cancellation, and TTY/non-TTY streams;
- archive upload/download and path metadata;
- image list, inspect, pull, build, tag, and remove;
- network and volume lifecycle needed by the fixtures;
- label-filtered, ordered, reconnectable events.

Unsupported endpoints or request fields must fail before side effects with a
Docker-shaped error. They must not return a successful approximation.
Buildx is reported only after its session and stream behavior passes the
Feature, build-context, and UID-update fixtures.

## Functional support ledger

All entries are currently `planned`.

| Area | Required behavior | Status |
| --- | --- | --- |
| Engine negotiation | Ping, version negotiation, versioned paths, errors | `planned` |
| Container lifecycle | Create through remove, inspect, wait, idempotent cleanup | `planned` |
| Exec and streams | TTY and multiplexed streams, resize, cancellation, exit status | `planned` |
| Images and builds | Pull, inspect, Dockerfile options, target, failed-build stream | `planned` |
| Archive | Copy in/out, modes, ownership, symlinks, long paths, large files | `planned` |
| Networks and volumes | Lifecycle, bind and named volumes, read-only and tmpfs behavior | `planned` |
| Image Dev Container | Workspace, labels, keepalive, attach, reopen | `planned` |
| Dockerfile Dev Container | Context, target, build arguments, entrypoint/CMD, rebuild | `planned` |
| Users and environment | Container/remote users, UID update, environment probing | `planned` |
| Lifecycle hooks | Normative ordering, parallel object commands, failure gating | `planned` |
| Features | OCI resolution, ordering, installation, locks, frozen locks | `planned` |
| Ports | Publish, forward, collision handling, host/service connectivity | `planned` |
| Reuse and recovery | Reopen, rebuild, shutdown, crash recovery, leak-free cleanup | `planned` |
| Compose service | Selected service, generated overrides, workspace projection | `planned` |
| Compose dependencies | `runServices`, health gates, service DNS | `planned` |
| Compose resources | Named volumes, networks, aliases, environment files | `planned` |
| Compose lifecycle | Recreation, shutdown, signals, restart, discovery labels | `planned` |
| Fault recovery | Socket/backend failure, deadlines, signals, lifecycle races | `planned` |
| VS Code | Open, attach, server install, terminal, ports, rebuild, reopen, cleanup | `planned` |

The machine-readable source of these rows is
[`Tests/Parity/manifest.json`](Tests/Parity/manifest.json). Documentation must
not mark a row supported before its manifest fixture is `implemented` and its
evidence is attached to a release candidate.

## Parity definition

The `docker` lane is the behavioral oracle. For a fixture to pass,
`apple-stock` and `apple-compose` must have zero semantic differences from the
oracle within the claimed surface.

The harness may normalize only:

- stable symbolic resource identifiers;
- temporary workspace and runtime roots;
- timestamps and durations while preserving ordering and deadlines;
- dynamic ports and IP addresses while preserving connectivity;
- ANSI progress/spinner frames;
- ordering that the upstream contract explicitly defines as unordered.

It may not normalize or waive:

- process exit codes, stderr, or warnings;
- lifecycle and event ordering;
- file content, ownership, modes, or symlink behavior;
- missing resources;
- mount, DNS, alias, network, or volume semantics;
- security behavior;
- a result that passes only after retrying.

Parity evidence includes raw client output, normalized observations, backend
fingerprints, event streams, cleanup proofs, JUnit output, and a human-readable
matrix. The exact official `@devcontainers/cli` is exercised directly with
`devcontainer up`, `exec`, `run-user-commands`, rebuild, and removal flows.
The same fixtures are driven through an unmodified Docker CLI. A real VS Code
and Dev Containers extension must additionally pass open, attach, integrated
terminal, port forwarding, rebuild, reopen, and cleanup smoke tests.

## Provider ownership and split-brain prevention

Compose provider selection is durable project state:

1. Before the first mutating Compose command, the service acquires a
   project-scoped lease and records either `stock` or `container-compose`.
2. The key is based on the user/runtime scope and normalized Compose project
   name. Project directory, ordered files, and configuration hashes are
   recorded as fingerprints, not as independent ownership domains.
3. All later mutations use the recorded provider.
4. An unavailable claimed provider produces an explicit failure; it never
   falls back to the other provider.
5. Provider reset requires explicit `down`, reconciliation proving zero live
   project resources, and an explicit reset or migration command.
6. Resources from both provider namespaces place the project in conflict.
   Recovery is explicit and non-destructive.

The Apple runtime is authoritative for live resources. The compatibility
database stores provider claims, leases, Docker aliases, event cursors, and
operation recovery metadata; it is not a second Compose lifecycle database.
Out-of-band `container compose` resources may be adopted only when their
project identity and provider are unambiguous.

The optional provider's `com.apple.container.compose.*` labels are projected
to the `com.docker.compose.*` labels consumed by Dev Containers. Docker label
filters are translated in the other direction. Conflicting native and
projected values are reconciliation errors and are never overwritten.

## Runner and evidence requirements

Unit, contract, documentation, and manifest validation can run on ordinary
hosted macOS workers. Runtime compatibility claims require isolated physical
Apple-silicon Macs with:

- the exact macOS 26 product/build under test;
- Xcode 26 and Swift 6.2 or newer, recorded by full version output;
- hardware virtualization enabled;
- sufficient memory and disk for several per-container VMs and retained
  evidence;
- a dedicated non-administrator test user and user-owned Docker socket;
- no unrelated Apple container resources;
- a verified clean state before and after every fixture.

Prefer separate physical runner profiles for stock Apple and Stephen's matched
fork stack. If one host is reused, installation changes must be serialized,
the previous stack must be removed through its supported uninstaller, and the
next job must verify exact runtime provenance before executing. Public
pull-request code must never run on these trusted runtime hosts.

Every stable candidate also requires:

- at least 90% first-party Swift line coverage under the documented exclusions;
- clean Swift Address Sanitizer and Thread Sanitizer jobs;
- fault-injection recovery tests;
- all three parity lanes with no required fixture skipped;
- a signed evidence manifest bound to the release commit and binary checksums.

Build and runner commands are defined as a planned interface in
[`BUILD.md`](BUILD.md).
