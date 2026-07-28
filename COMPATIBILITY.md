# Compatibility contract

## Current status

> [!IMPORTANT]
> Version 1.0.0 is a release candidate for the exact component fingerprints
> below. The Docker lane passes all 18 CLI fixtures. The latest stock Apple and
> separate `container-compose` candidate runs pass 17 of 18; published-port
> host connectivity is blocked until Local Network access is enabled for
> each selected runtime's `container-runtime-linux` helper on the physical
> runner.
> No stable compatibility claim exists until all CLI and VS Code fixtures pass
> with zero normalized semantic differences.

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
| `container-compose` | The same Docker inspection, exec, copy, attach, and event bridge, with Stephen Clarke's `container compose` selected for Compose planning and lifecycle | Optional external executable; separately installed and provenance-checked; not supplied by Apple | `candidate` |

The stock lane must remain fully functional when `container compose` is not
installed. The optional provider is nevertheless a first-class release lane:
all Compose-backed Dev Container fixtures must pass through both
`apple-stock` and `container-compose` before a stable release.

`container-compose` is not a Swift package dependency of the runtime-neutral
core, is not copied into this product, and is not a service startup
prerequisite. Its adapter discovers and launches an explicitly configured
executable using argv-based process creation. It must not import `ComposeCore`
or another implementation module.

## Pinned design-time provenance

These pins define the 1.0.0 compatibility matrix. Candidate-bound release
evidence also records the signing identity where applicable, platform triple,
and each component's machine-readable version output.

| Component | Version or ref | Exact source provenance | Role |
| --- | --- | --- | --- |
| `@devcontainers/cli` | `0.88.0` | Official `v0.88.0` tag commit `f683c29f64a20109b4453e5149807e390ff65133`; npm SHA-512 SRI `sha512-sMkruPy/icfov20mdQh2EjFYZogxvMEZptDEvg5/eMBIUOr2xr+8wlsI7nvDR6EJxoBjqoasXqgRGbiMqbaJ1w==` | Unmodified reference client |
| Docker CLI | `29.6.2` | Executable SHA-256 `eade1c3a5dda47534dc776f2f534c99cc94cfcf9ce07c4bf09e98258d13e7d7a`; Homebrew bottle SHA-256 `b05a401b661f2d0c3b54b10fd1e0c4adb26b479dcfb953d86febfdfb57dd9821` | Unmodified client used by the reference CLI |
| Docker Engine | `29.2.1`, API `1.53`, build `6bc6209` | Executable SHA-256 `e70ffe2700ffeffa099decd1111816c475e59972945ac0a48b508b3ee306bad2` | Behavioral oracle |
| Docker Compose | `5.3.1` | Executable SHA-256 `6c4a20e62f3a776dc7ee603dc296ec63c7194b46067c6461be9208d191c922b3`; Homebrew bottle SHA-256 `9df565543164437312a50347eb2785b59b0f35e9fc1c044aaea5b6fa78952608` | Oracle and stock Compose client |
| `apple/container` stable | `1.1.0` | Annotated tag object `82fc9a5ba73c34c478ce15958bb75dbb45c67e3b`; source commit `5973b9cc626a3e7a499bb316a958237ebe14e2ed` | Initial stable stock lane |
| `apple/containerization` for `container` 1.1.0 | `0.35.0` | Apple resolution/tag object `44bec8b9933bc491d0cbf44abac90a1f6aaebf6b`; source commit `0334a3e790bbed50420de71cd0d706191bdf84d1` | Must be inherited from the Apple `container` resolution |
| `container-compose` stable | `0.10.1` | Annotated tag object `5be84c712176d745b4736e82f97b7458813cb7ec`; source commit `77d2191a75f3a15092bbead1991b0d6a37fafa91` | Optional provider |
| Stable provider's `stephenlclarke/container` | Revision | `367430446959e3048da37f5f64d3c10e1293d3de` | Exact fork dependency declared by `container-compose` 0.10.1 |
| Stable provider's `stephenlclarke/containerization` | Revision | `043193efa5f1a2e21a240041d6edd71d7673739e` | Exact fork dependency declared by `container-compose` 0.10.1 |
| VS Code | `1.130.0`, arm64 stable | Commit `1b6a188127eeaf9194f945eb6eb89a657e93c54c`; official archive SHA-256 `6e16ccb1caac394daec788b65d285d30a8093cdf2db96552c53cc9d0252f24d3`; application identifier `com.microsoft.VSCode`; Microsoft team `UBF8T346G9` | End-to-end client |
| VS Code Dev Containers extension | `0.467.0` | Official Marketplace VSIX SHA-256 `b3bd40702da5dd7d1a99aac697da5c437f28deeec899d0bb6e78dd76a5c1b012`; embedded CLI `0.88.0` at `f683c29f64a20109b4453e5149807e390ff65133`, SHA-256 `ff3934cb098a78e2ed59a2199c225be2f79a8c79636d45682685e85fb3d6e5ca` | End-to-end reference integration |
| Release host | macOS `26.5.2` (`25F84`), Xcode `26.6` (`17F113`), Swift `6.3.3`, arm64 | Exact values enforced by the release parity preflight | Host and toolchain |

Moving branch heads are never stable compatibility claims and are not inputs
to this release matrix. The machine-readable identities in
[`Tests/Parity/manifest.json`](Tests/Parity/manifest.json) are authoritative.

The Apple adapter must take `containerization` from the selected
`apple/container` release resolution. Depending on a second independently
selected `containerization` revision would create an untested ABI/source
combination and is forbidden.

### Upstream fix ownership

Any fix required in `stephenlclarke/container`,
`stephenlclarke/containerization`, or
`stephenlclarke/container-compose` must be implemented in its owning
repository, protected by a regression test there, and delivered as a focused
pull request for review. After that change is reviewed, this repository may
pin and record the exact accepted commit and rerun the complete affected
parity lane.

This repository must never carry a patched vendor directory, copied upstream
source, ad hoc diff, or release-only binary replacement for those projects.

## Docker Engine API bounds

The service advertises the bounded API envelope:

- minimum implemented version: `1.44`;
- maximum implemented version: `1.53`;
- pinned oracle version: `1.53`, from Docker Engine `29.2.1`;
- versions above `1.53`: out of scope until separately pinned and tested;
- versions below `1.44`: out of scope for the initial release.

Version 1.0.0 advertises the contiguous, tested subset `1.44...1.53`.
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

### Stock Apple 1.1.0 create-time boundary

The unmodified Apple 1.1.0 `container create` command does not expose `--hostname`, `--security-opt`, or `--privileged`. Its public `ContainerConfiguration` also has no hostname or security-option transport field. The stock adapter therefore:

- accepts the normal Dev Containers path where Docker sends an empty hostname and no security options;
- maps Docker privileged mode to Apple’s native `--cap-add ALL` model, retaining per-container VM isolation;
- rejects a non-empty Docker `Hostname` or any `SecurityOpt` before creating managed volumes, containers, or other runtime resources;
- reports a Docker-shaped unsupported-capability response rather than claiming a weakened security approximation.

The separately fingerprinted enhanced runtime used by the optional Compose lane exposes native `--hostname`, `--security-opt`, and `--privileged` flags. The adapter probes the selected executable’s actual `create --help` surface and uses those flags only when advertised. These enhanced semantics are not attributed to stock Apple. The exact stock boundary follows Apple’s pinned [`Flags.Management`](https://github.com/apple/container/blob/1.1.0/Sources/Services/ContainerAPIService/Client/Flags.swift) and [`ContainerConfiguration`](https://github.com/apple/container/blob/1.1.0/Sources/ContainerResource/Container/ContainerConfiguration.swift) sources.

## Functional support ledger

Every row has an implemented parity fixture and candidate-bound evidence.

| Area | Required behavior | Status |
| --- | --- | --- |
| Engine negotiation | Ping, version negotiation, versioned paths, errors | `candidate` |
| Container lifecycle | Create through remove, inspect, wait, idempotent cleanup | `candidate` |
| Exec and streams | TTY and multiplexed streams, resize, cancellation, exit status | `candidate` |
| Images and builds | Pull, inspect, Dockerfile options, target, failed-build stream | `candidate` |
| Archive | Copy in/out, modes, ownership, symlinks, long paths, large files | `candidate` |
| Networks and volumes | Lifecycle, bind and named volumes, read-only and tmpfs behavior | `candidate` |
| Image Dev Container | Workspace, labels, keepalive, attach, reopen | `candidate` |
| Dockerfile Dev Container | Context, target, build arguments, entrypoint/CMD, rebuild | `candidate` |
| Users and environment | Container/remote users, UID update, environment probing | `candidate` |
| Lifecycle hooks | Normative ordering, parallel object commands, failure gating | `candidate` |
| Features | OCI resolution, ordering, installation, locks, frozen locks | `candidate` |
| Ports | Publish, forward, collision handling, host/service connectivity | `candidate` |
| Reuse and recovery | Reopen, rebuild, shutdown, crash recovery, leak-free cleanup | `candidate` |
| Compose service | Selected service, generated overrides, workspace projection | `candidate` |
| Compose dependencies | `runServices`, health gates, service DNS | `candidate` |
| Compose resources | Named volumes, networks, aliases, environment files | `candidate` |
| Compose lifecycle | Recreation, shutdown, signals, restart, discovery labels | `candidate` |
| Fault recovery | Socket/backend failure, deadlines, signals, lifecycle races | `candidate` |
| VS Code | Open, attach, server install, terminal, ports, rebuild, reopen, cleanup | `candidate` |

The machine-readable source of these rows is
[`Tests/Parity/manifest.json`](Tests/Parity/manifest.json). Documentation must
not mark a row supported before its manifest fixture is `implemented` and
every required candidate-bound parity and release gate has passed.

## Parity definition

The `docker` lane is the behavioral oracle. For a fixture to pass,
`apple-stock` and `container-compose` must have zero semantic differences from the
oracle within the claimed surface.

Each lane records monotonic fixture wall time in its JSON and JUnit evidence. The aggregate matrix reports candidate/Docker ratios for each matching fixture. A completed slowdown below `10x` is informational and does not alter semantic parity. A timeout, other non-completion, missing duration, or candidate duration of at least `10x` the Docker fixture is a parity failure; performance failures are never retried or normalized away.

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

The optional `container-compose` provider's compatibility labels happen to use
the `com.apple.container.compose.*` namespace; they do not identify an
Apple-authored Compose product. They are projected to the
`com.docker.compose.*` labels consumed by Dev Containers. Docker label filters
are translated in the other direction. Conflicting native and projected values
are reconciliation errors and are never overwritten.

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

The implemented build, verification, parity, packaging, and runner commands
are documented in [`BUILD.md`](BUILD.md) and exposed through the checked-in
`Makefile`.
