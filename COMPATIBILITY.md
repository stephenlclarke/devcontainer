# Compatibility contract

## Current status

> [!IMPORTANT]
> Version 1.0.1 remains the latest immutable stable compatibility baseline. In
> its exact tag run, real Docker, stock Apple `container` 1.1.0, and the
> separate `container-compose` 0.10.1 provider passed all 18 CLI fixtures and
> the real VS Code end-to-end fixture with zero normalized semantic
> differences. The largest CLI ratios were 2.876x for stock Apple and 4.509x
> for `container-compose`; the corresponding VS Code ratios were 1.232x and
> 1.311x. The release fingerprints below remain the only certified runtime
> matrix. Current source dependency profiles are recorded separately and do
> not retroactively change the 1.0.1 tag evidence. Repeated-run statistics are
> in [PERFORMANCE.md](PERFORMANCE.md).

The project's north-star goal is 100% behavioural parity with Docker-based Development Containers and comparable or better performance. This document remains the narrower current compatibility contract; [`PARITY-ROADMAP.md`](PARITY-ROADMAP.md) defines the work and evidence required to reach the north star.

This document is the support and claim ledger for `devcontainer`. A stable
release may claim only the exact combinations and behaviors that have passed
the three-lane differential suite in
[`Tests/Parity/manifest.json`](Tests/Parity/manifest.json). Version proximity,
successful compilation, unit tests, or an apparently successful container
startup are not compatibility evidence.

## Claim vocabulary

Unreleased D06 port work is `candidate`, not `supported`. The bounded frontend accepts explicit canonical `IPv4:host-port:container-port[/tcp]` with nonzero fixed ports through `-p`/`--publish`. Dynamic ports, ranges, IPv6 and UDP syntax fail before mutation. The pinned Docker reference passes the original loopback fixture; native live qualification remains required. These limits describe the new frontend, not the entire Engine API, and do not change the stable matrix.

Unreleased Bazel D02 work is `candidate`, not `supported`: the Docker reference and stock Apple candidate pass their unchanged observations and cleanup, while enhanced prerequisites and release qualification remain open. Stock evidence is `3530b684-a77e-42fb-b1a0-11e4c507e401` at `106144a`. The native build frontend currently accepts local contexts, explicit Dockerfile/tag/target/build-argument forms and bounded streamed progress. Existing `.dockerignore` files, remote/stdin contexts, implicit environment arguments, oversized inputs and other unimplemented flags fail explicitly. This does not widen the stable compatibility matrix or certify full Docker-build semantics. Native image-layer inspection is additive; older provider snapshots lacking layer data remain decodable and do not fabricate ancestry.

| State | Meaning |
| --- | --- |
| `planned` | Design and fixture exist, but there is no functional support claim. |
| `candidate` | Implemented on a development branch and under test; not a release claim. |
| `supported` | The exact recorded component fingerprint passed every required parity, quality, sanitizer, and recovery gate. |
| `unsupported` | Outside the bounded compatibility surface or failed a required semantic assertion. |

No row may move to `supported` while a required fixture is skipped, retried
until it passes, normalized to hide a difference, or waived.

This release vocabulary applies to the fixture claim. The broader standards
audit also uses `delegated`, `partial`, and `unverified`; see
[CONFORMANCE.md](CONFORMANCE.md).

## Provider lanes

| Lane | Runtime and Compose path | Installation boundary | Status |
| --- | --- | --- | --- |
| `docker` | Official `@devcontainers/cli` and Docker Compose against a real Docker Engine | Independent behavioral oracle | `supported` |
| `apple-stock` | Official `@devcontainers/cli` and upstream Docker Compose against this project's Docker Engine bridge, then an unmodified tagged `apple/container` runtime | Required runtime lane; no Stephen fork or `container-compose` package dependency | `supported` |
| `container-compose` | The same Docker inspection, exec, copy, attach, and event bridge, with Stephen Clarke's `container compose` selected for Compose planning and lifecycle | Optional external executable; separately installed and provenance-checked; not supplied by Apple | `supported` |

The stock lane must remain fully functional when `container compose` is not
installed. The optional provider is nevertheless a first-class release lane:
all Compose-backed Dev Container fixtures must pass through both
`apple-stock` and `container-compose` before a stable release.

`container-compose` is not a Swift package dependency of the runtime-neutral
core, is not copied into this product, and is not a service startup
prerequisite. Its adapter discovers and launches an explicitly configured
executable using argv-based process creation. It must not import `ComposeCore`
or another implementation module.

## Pinned 1.0.1 release provenance

Current development source separates Compose frontend choice from runtime ownership and rejects missing frontend executables before claiming state. Component tests cover both frontend choices with both runtime backends and reject cross-runtime claim migration. These tests do not certify additional frontend/runtime combinations; the immutable 1.0.1 matrix below remains the release claim.

The candidate native-create path also distinguishes failed local preparation from uncertain runtime submission. Mount/kernel failures do not create pending container intent, while failures after journalling retain it. Component tests do not substitute for live creation/recovery qualification.

The candidate image-build path preserves completed builder progress and emits an in-band Docker build error only after recording failed reconciliation. Preflight, cancellation and abandonment remain failures. Stock/enhanced component checks do not replace live E04 builder/failure-phase qualification or change the historical release matrix below.

These pins define the immutable 1.0.1 compatibility matrix and match the
`Tests/Parity/manifest.json` stored at the 1.0.1 tag. Release-bound evidence
also records the signing identity where applicable, platform triple, and each
component's machine-readable version output.

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
| VS Code | `1.131.0`, arm64 stable | Commit `e4c7e7b1d6d060162f4aa7f8225271b67ce1df75`; official archive SHA-256 `796c3ae1cd28d45b3fb8450c0f8661cf2f43632e3a0f38f5025f0c49675bcf99`; application identifier `com.microsoft.VSCode`; Microsoft team `UBF8T346G9` | End-to-end client |
| VS Code Dev Containers extension | `0.467.0` | Official Marketplace VSIX SHA-256 `b3bd40702da5dd7d1a99aac697da5c437f28deeec899d0bb6e78dd76a5c1b012`; embedded CLI `0.88.0` at `f683c29f64a20109b4453e5149807e390ff65133`, SHA-256 `ff3934cb098a78e2ed59a2199c225be2f79a8c79636d45682685e85fb3d6e5ca` | End-to-end reference integration |
| Release host | macOS `26.5.2` (`25F84`), Xcode `26.6` (`17F113`), Swift `6.3.3`, arm64 | Exact values enforced by the release parity preflight | Host and toolchain |

## Current source dependency profiles

The September 2026 Bazel requalification has found a specific gap with released devcontainer 1.0.1 and stock Apple Container 1.4.1: inspection by the original OCI configuration digest returns 404 before E02 creates its guest. Configuration-digest lookup and descriptor-bound native creation are implemented in the development branch with focused tests, not release certification. Live SDK transport, alias-safe image mutations and the complete parity cycle remain open. See the [retained attempt evidence](docs/bazel-test-harness.md#approved-helpers-and-first-stock-protocol-pass). Do not extend the historical 1.1.0 matrix above to this newer runtime combination.

Draft creation recovery uses schema 4 and requires `RuntimeCreationStore` for direct native creation. It retains uncertain operations and blocks launch/exec/archive/rename of the affected incarnation without unsafe automatic deletion. Schema-2/3 migration is supported; schema-3 binaries cannot open the upgraded state. Operator reconciliation and live interrupted-create proof are still outstanding, so this is not a completed recovery/parity claim. [Native creation recovery](DESIGN.md#native-creation-recovery) specifies the exact scope and rollback rule.

Current `main` builds two explicit dependency graphs. These are compile and
hosted-test inputs, not a new runtime-parity claim. The latest source-bearing
runtime workflow did not produce complete lane evidence, so the release
claim deliberately remains on the certified 1.0.1 matrix.

The working [`Tests/Parity/manifest.json`](Tests/Parity/manifest.json) is a
development input. It retains the 1.0.1 Dev Containers, Apple, Compose, VS
Code, and host pins but now selects Docker Engine 29.5.2, API 1.54, build
`568f755`, with executable SHA-256
`eb4bf018da78f7b9d01d69209d0944d1fe995869ac3caefa5c93e4552e181301`.
That post-release repin is not attributed to the 1.0.1 tag.

| Profile component | Exact current source provenance | Status boundary |
| --- | --- | --- |
| Stock `apple/container` | 1.4.1 at `9a8917ca2da5cd6ba059b9ba5ca5a74892e9bb7d` | Unmodified Apple dependency selected by `Package.stock.resolved`; compile/test evidence only |
| Stock `apple/containerization` | 0.45.0 at `9eacc197d7c3663eb29cbab6d51244ede6d1cd7d` | Inherited official Apple dependency in the stock graph |
| Stock `apple/swift-nio-ssl` | 2.37.4 at `03827c1a9fdb2b6b00a4e93ede8861520263af8c` | Official dependency in the stock graph |
| Enhanced `stephenlclarke/container` | `228897171d71975988ccdc690f1982e7433952af` | Exact revision selected by `Package.resolved`; no stock-Apple claim |
| Enhanced `stephenlclarke/containerization` | `b404e03bb914904107a6a9305ba1f0e44c79a59c` | Exact revision selected by `Package.resolved`; no stock-Apple claim |
| Enhanced `stephenlclarke/swift-nio-ssl` | `3e13ce5f6dd5b7e89fff9ab55ab7caed39fe7285` | Exact revision selected by `Package.resolved`; no stock-Apple claim |
| Shared `stephenlclarke/container-engine-api` | `f32e1829d0f0293bd68a69a7a6f93f67953c31e9` | Selected by both profiles; draft [PR 45](https://github.com/stephenlclarke/container-engine-api/pull/45) adds bounded cancellable full-duplex transport concurrent-client fixes and diagnostic-only HTTP error bodies. Development revision, not a released dependency or full-stack certification |

`Package.resolved` and `Package.stock.resolved` are authoritative for source
builds. The parity manifest becomes authoritative for a newer runtime claim
only when its exact clients, runtimes, host, and fixture evidence are updated
and the complete release gate succeeds.

Moving branch heads are never stable compatibility claims and are not inputs
to the 1.0.1 release matrix. The machine-readable manifest stored at a release
tag is authoritative for that release; the working manifest is authoritative
only for the next candidate gate that consumes it.

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
- pinned oracle version: `1.54`, from Docker Engine `29.5.2`;
- versions above `1.53`: out of scope until separately pinned and tested;
- versions below `1.44`: out of scope for the initial release.

Version 1.0.1 advertises the contiguous, tested subset `1.44...1.53`.
Unversioned routes and every advertised version prefix must negotiate and
return Docker-compatible status codes, JSON fields, headers, event ordering,
and stream framing. Advertising a version means every endpoint needed by the
pinned Dev Containers and Compose clients works at that version; it does not
mean the bridge is a general-purpose Docker daemon.

The shared gateway owns the public version and route envelope. In normal mode, `devcontainer-engine` starts one internal mode-`0600`, fingerprint-bound provider session and routes its public socket through `ContainerEngineGatewayResponder`; `devcontainer-engine --provider-socket PATH` exposes only the private session for a separately managed `container-engine` process. There is no legacy direct-listener path and therefore no second public route authority.

The bounded endpoint surface is:

- ping, version, information, and API negotiation;
- container list, create, inspect, start, stop, kill, wait, remove, logs, raw
  attach, and binary WebSocket attach; stock running-container resize is
  deliberately unadvertised because Apple Containerization does not expose the
  exact active init-process handle;
- exec create, start, resize, inspect, cancellation, and TTY/non-TTY streams;
- archive upload/download and path metadata;
- image list, inspect, pull, build, tag, and remove;
- network and volume lifecycle needed by the fixtures, with network attachment
  fixed at container creation;
- label-filtered, ordered, reconnectable events.

Modelled runtime-affecting container, exec, network, and volume request objects
use strict nested decoding. Unknown fields fail before side effects with a
Docker-shaped `400`; known but unenforceable non-default fields fail with
`501`. Complete schema-derived coverage for every Docker endpoint is still
open, so arbitrary `runArgs` are not a blanket support surface. This boundary
and its remediation priority are recorded in [CONFORMANCE.md](CONFORMANCE.md).
Buildx is reported only after its session and stream behavior passes the
Feature and build-context fixtures.

### Stock Apple 1.1.0 create-time boundary

The unmodified Apple 1.1.0 `container create` command does not expose `--hostname`, `--security-opt`, or `--privileged`. Its public `ContainerConfiguration` also has no hostname or security-option transport field. The stock adapter therefore:

- accepts the normal Dev Containers path where Docker sends an empty hostname and no security options;
- treats `seccomp=unconfined` as the already-native state because Apple containers do not install Docker’s default seccomp profile;
- rejects Docker privileged mode before creation rather than approximating it with `--cap-add ALL`;
- rejects a non-empty Docker `Hostname` or any remaining `SecurityOpt` before creating managed volumes, containers, or other runtime resources;
- reports a Docker-shaped unsupported-capability response rather than claiming a weakened security approximation.

The complete implementation design for these and every other current `501` capability path is in [`UNSUPPORTED-CAPABILITIES.md`](UNSUPPORTED-CAPABILITIES.md). It identifies which gaps can use an existing tagged Apple API, which require a new upstream runtime primitive, and the Docker/stock/provider evidence required before the compatibility claim expands.

The separately fingerprinted enhanced runtime used by the optional Compose lane exposes native `--hostname`, `--security-opt`, and `--privileged` flags. The adapter probes the selected executable’s actual `create --help` surface and uses those flags only when advertised. These enhanced semantics are not attributed to stock Apple. The exact stock boundary follows Apple’s pinned [`Flags.Management`](https://github.com/apple/container/blob/1.1.0/Sources/Services/ContainerAPIService/Client/Flags.swift) and [`ContainerConfiguration`](https://github.com/apple/container/blob/1.1.0/Sources/ContainerResource/Container/ContainerConfiguration.swift) sources.

## Certified fixture ledger

Every row is bounded by the assertions in its implemented parity fixture and
release-bound evidence. A row does not certify every option in that broad
technology area; [CONFORMANCE.md](CONFORMANCE.md) records the untested and
non-conformant forms.

| Area | Required behavior | Status |
| --- | --- | --- |
| Engine negotiation | Ping, version negotiation, versioned paths, errors | `supported` |
| Container lifecycle | Create through remove, inspect, wait, idempotent cleanup | `supported` |
| Exec and streams | TTY/non-TTY output, user/environment/workdir, 4 MiB stdin/stdout, stderr, exit status | `supported` |
| Images and builds | Public arm64 pull/inspect, checked-in Dockerfile context/arguments/target, failed-build stream | `supported` |
| Archive | Copy in/out, content, mode, symlink, long path, 1 MiB file | `supported` |
| Networks and volumes | Checked-in lifecycle, bind and named volumes, read-only and tmpfs behavior | `supported` |
| Image Dev Container | Workspace, environment, selected user, post-create command | `supported` |
| Dockerfile Dev Container | Dockerfile/context path, target, build argument, workspace, post-create command | `supported` |
| Users and environment | Checked-in container/remote users, environment expansion, and explicit no-UID-update path | `supported` |
| Lifecycle hooks | Checked-in string-valued create/update/post-create/start/attach ordering | `supported` |
| Features | Two public OCI Features, generated build, lockfile, frozen-lock rejection | `supported` |
| Ports | TCP publishing, forward metadata, real VS Code forwarding, collision rejection, host/container connectivity | `supported` |
| Reuse and cleanup | Same-config reuse, forced replacement, hook counts, volume/container cleanup | `supported` |
| Compose service | Selected service, generated overrides, workspace projection | `supported` |
| Compose dependencies | `runServices`, health gates, service DNS | `supported` |
| Compose resources | Named volumes, networks, aliases, environment files | `supported` |
| Compose lifecycle | Recreation, shutdown, signals, restart, discovery labels | `supported` |
| Fault recovery | Missing backend, bounded empty events, signal exit, concurrent start/remove convergence | `supported` |
| VS Code | Open, attach, server install, terminal, ports, rebuild, reopen, cleanup | `supported` |

The machine-readable source of these rows is
[`Tests/Parity/manifest.json`](Tests/Parity/manifest.json). Documentation must
not mark a row supported before its manifest fixture is `implemented` and
every required release-bound parity and release gate has passed.

## Standards claim

The fixture ledger is not a full Development Containers Specification claim.
Version 1.0.1 has confirmed gaps in arbitrary `runArgs`, GPU requests, exact
Docker privileged behavior, stock security options and hostname, advanced
mount fields, image-anonymous volume semantics, and post-create network
changes. It also has properties delegated to the official CLI or VS Code that
are not independently certified. The complete property-by-property audit and
known non-conformance IDs are maintained in
[CONFORMANCE.md](CONFORMANCE.md).

## Parity definition

The `docker` lane is the behavioral oracle. For a fixture to pass,
`apple-stock` and `container-compose` must have zero semantic differences from the
oracle within the claimed surface.

Each lane records monotonic fixture wall time in its JSON and JUnit evidence. The aggregate matrix reports candidate/Docker ratios for each matching fixture. Comparable or better performance (`<=1.00x` Docker) is the objective. A completed result above `2.50x` Docker requires further investigation but does not, by itself, alter functional parity. A timeout, other non-completion, or missing or invalid timing evidence fails the gate and is never retried or normalized away. The complete performance objective and investigation policy are in [`PARITY-ROADMAP.md`](PARITY-ROADMAP.md).

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

1. Before the first resource-changing Compose command, the dispatcher consumes every supported global option, fails closed on an option it cannot classify, acquires a project-scoped lease, and records either `stock` or `container-compose`.
2. The key is based on the local user and canonical Compose project name. An explicit `-p` or `COMPOSE_PROJECT_NAME` is validated directly; otherwise the selected provider's `config --format json` resolves file, top-level `name:`, project-directory, and current-directory precedence. The invocation project directory is retained as diagnostic metadata, not as an independent ownership domain.
3. Every later mutation must present the recorded provider; a conflicting
   selection fails before the provider process runs.
4. An unavailable selected provider produces an explicit failure; it never
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

A provider handoff may carry stopped-container identity and lifecycle state
only after an atomic quiescence check. Canonical names, Docker identifiers,
immutable Apple bundle keys, and the selected-provider fingerprint are
preserved. Running containers, active execs, starts, and concurrent lifecycle
mutations reject the export. The stock poller cannot prove historical events,
so the handoff reports no event history instead of manufacturing it.

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
