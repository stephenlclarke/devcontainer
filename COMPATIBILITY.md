# Compatibility contract

## Current status

> [!IMPORTANT]
> Version 1.0.2 is the current source candidate, not an immutable stable
> compatibility baseline. The latest published stable release remains 1.0.1.
> The candidate must pass its release-bound real Docker, stock Apple
> `container` 1.4.1, separate `container-compose` 0.15.1, and real VS Code
> gates before this section may state a 1.0.2 release result. Historical
> measurements and the required new-run protocol are in
> [PERFORMANCE.md](PERFORMANCE.md).

The project's north-star goal is 100% behavioural parity across the audited,
Docker-independent Development Containers surface and comparable or better
performance than the Docker oracle. Host-daemon and daemon-socket-dependent
configurations are intentionally excluded. This document remains the narrower
current compatibility contract; [`PARITY-ROADMAP.md`](PARITY-ROADMAP.md)
defines the work and evidence required to reach the north star.

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

This release vocabulary applies to the fixture claim. The broader standards
audit also uses `delegated`, `partial`, and `unverified`; see
[CONFORMANCE.md](CONFORMANCE.md).

## Provider lanes

| Lane | Runtime and Compose path | Installation boundary | Status |
| --- | --- | --- | --- |
| `docker` | Official `@devcontainers/cli` and Docker Compose against a real Docker Engine | Independent behavioral oracle | `supported` |
| `apple-stock` | Official `@devcontainers/cli` through this project's adapters, native `container-compose`, then an unmodified tagged `apple/container` runtime | Required runtime lane; no Stephen Container fork and no Docker software dependency | `supported` |
| `container-compose` | The same inspection, exec, copy, attach, and event bridge, with the exact native Compose provider selected for planning and lifecycle | Stock-profile provider is bundled; an enhanced external provider is optional, explicit, and separately provenance-checked; neither is supplied by Apple | `supported` |

The stock lane must remain fully functional when `container compose` is not
installed. The optional provider is nevertheless a first-class release lane:
all Compose-backed Dev Container fixtures must pass through both
`apple-stock` and `container-compose` before a stable release.

`container-compose` is not a Swift package dependency of the runtime-neutral
core. Packaging builds its exact stock profile as a private process-isolated
artifact, records its provenance and SBOM entry, and launches it with argv-based
process creation. Its payload also contains the exact provider SwiftPM and
vendored Go inventories, full third-party legal texts, and a provider SPDX
document. An external enhanced build requires an explicit override. The
core must not import `ComposeCore` or another implementation module.

## Pinned candidate provenance

These pins define the current source candidate's compatibility matrix.
Release-bound evidence also records the signing identity where applicable,
platform triple, and each component's machine-readable version output. The
immutable matrix is also embedded in that tag's parity evidence.

| Component | Version or ref | Exact source provenance | Role |
| --- | --- | --- | --- |
| `@devcontainers/cli` | `0.89.0` | Official `v0.89.0` tag commit `5dc7533314b5ba7ec3875c30143dfe1aec644870`; npm SHA-512 SRI `sha512-LzaoOGKQ/Zql6PsiZ4hVIYVZagzWkD65aG/1ou5/Kly5Y1PtjLg1yn7qu+LZCzVoAl6DZZ/pbz8qOO4RLNlqMg==` | Unmodified reference client |
| Docker CLI | `29.8.0` | Executable SHA-256 `b1ca8cf8e294fd128ef4a5f5fb2531a059dd981548dfa810fe19d5d6c695e486`; Homebrew bottle SHA-256 `998293c4bd31551c89a433e216ae890e4e93c9835b5ac6a822455655367bef89` | Quarantined reference-oracle client; never packaged or installed |
| Docker Engine | `29.2.1`, API `1.53`, build `6bc6209` | Executable SHA-256 `e70ffe2700ffeffa099decd1111816c475e59972945ac0a48b508b3ee306bad2` | Quarantined behavioral oracle; never a product backend |
| Docker Compose | `5.5.1` | Executable SHA-256 `120182a4826df10312a47f18407a843f55494c2606126376bfacff0f47f75028`; Homebrew bottle SHA-256 `e0a4eb648b704910aaf8cb2239a6337aa74ef598aa3bc7274be3b6328be8e41e` | Quarantined reference-oracle client; never packaged or installed |
| `apple/container` stable | `1.4.1` | Source commit `9a8917ca2da5cd6ba059b9ba5ca5a74892e9bb7d` | Required unmodified stock runtime lane |
| `apple/containerization` for `container` 1.4.1 | `0.45.0` | Source commit `9eacc197d7c3663eb29cbab6d51244ede6d1cd7d` | Inherited from the Apple `container` resolution |
| `stephenlclarke/container-engine-api` | Exact revision | Source commit `48e44d74d738ca3d24351ba02c4869be1a3e6998` | Shared executable, generated 107-operation API 1.44 through 1.53 ledger, bounded raw/WebSocket Unix listener, schema-2 private provider session, gateway, terminal-resize contract, and provider-owned state-root identity |
| `container-compose` stable | `0.15.1` | Verified annotated tag object `4aca8f6ab4174522af294051a02b22c9dd86b3d9`; source commit `81a2263adf30127a3cf774ffdaf56bd23e2f81c1` | Optional native provider; bundled in the Docker-less package using its stock Apple profile |
| Stable provider's `stephenlclarke/container` | Revision | `780a86b995ac4cb0985db97f38875fdc6e33d16b` | Exact enhanced-runtime dependency declared by `container-compose` 0.15.1 |
| Stable provider's `stephenlclarke/containerization` | Revision | `7e066a3101bc84fa0f7231daf6a03aa9ef62a567` | Exact enhanced-runtime dependency declared by `container-compose` 0.15.1 |
| VS Code | `1.137.0`, arm64 stable | Commit `645f29cc3176500b4b5762ba887cf2a7f0ffdf2c`; official archive SHA-256 `16ee5cddb1ea19234e1f2516da07d57e07d7cab6ab45a5515dab077656cbc65e`; application identifier `com.microsoft.VSCode`; Microsoft team `UBF8T346G9` | End-to-end client |
| VS Code Dev Containers extension | `0.470.0` | Official Marketplace VSIX SHA-256 `66300dd37ec86e709df46acf4c294821db94248ee89cff7fb32888271b5069a1`; embedded CLI `0.89.0` at `5dc7533314b5ba7ec3875c30143dfe1aec644870`, SHA-256 `e2051ce3598a26b11d29048a6dd3252ae8d3b056b413c0d53ba1fd3a56ec1b74` | End-to-end reference integration |
| Release host | macOS `26.6.2` (`25G83`), Xcode `27.0` (`27A266a`), Swift `6.4`, arm64 | Exact values enforced by the release parity preflight | Host and toolchain |

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

Version 1.0.2 advertises the contiguous, tested subset `1.44...1.53`.
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

### Stock Apple 1.4.1 create-time boundary

The unmodified Apple 1.4.1 `container create` command does not expose `--hostname`, `--security-opt`, or `--privileged`. Its public `ContainerConfiguration` also has no hostname or security-option transport field. The stock adapter therefore:

- accepts the normal Dev Containers path where Docker sends an empty hostname and no security options;
- treats `seccomp=unconfined` as the already-native state because Apple containers do not install Docker’s default seccomp profile;
- rejects Docker privileged mode before creation rather than approximating it with `--cap-add ALL`;
- rejects a non-empty Docker `Hostname` or any remaining `SecurityOpt` before creating managed volumes, containers, or other runtime resources;
- reports a Docker-shaped unsupported-capability response rather than claiming a weakened security approximation.

The complete implementation design for these and every other current `501` capability path is in [`UNSUPPORTED-CAPABILITIES.md`](UNSUPPORTED-CAPABILITIES.md). It identifies which gaps can use an existing tagged Apple API, which require a new upstream runtime primitive, and the Docker/stock/provider evidence required before the compatibility claim expands.

The separately fingerprinted enhanced runtime used by the optional Compose lane exposes native `--hostname`, `--security-opt`, and `--privileged` flags. The adapter probes the selected executable’s actual `create --help` surface and uses those flags only when advertised. These enhanced semantics are not attributed to stock Apple. The exact stock boundary follows Apple’s pinned [`Flags.Management`](https://github.com/apple/container/blob/1.4.1/Sources/Services/ContainerAPIService/Client/Flags.swift) and [`ContainerConfiguration`](https://github.com/apple/container/blob/1.4.1/Sources/ContainerResource/Container/ContainerConfiguration.swift) sources.

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
Version 1.0.2 has confirmed gaps in arbitrary `runArgs`, GPU requests, exact
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

Each lane records monotonic fixture wall time in its JSON and JUnit evidence. The aggregate matrix reports candidate/Docker ratios for each matching fixture. Comparable or better performance (`<=1.00x` Docker) is the objective. A completed result above `2.50x` Docker requires further investigation but does not, by itself, alter functional parity. A candidate at or above `10.00x` its matching Docker fixture, a timeout, other non-completion, or missing or invalid timing evidence fails the gate and is never retried or normalized away. The complete performance objective and investigation policy are in [`PARITY-ROADMAP.md`](PARITY-ROADMAP.md).

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

The native `container-compose` provider's compatibility labels happen to use
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
