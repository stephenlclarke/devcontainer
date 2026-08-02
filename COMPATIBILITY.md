# Compatibility contract

## Current status

> [!IMPORTANT]
> Version 1.0.1 remains the latest immutable stable compatibility baseline. In
> its exact tag run, real Docker, stock Apple `container` 1.1.0, and the
> separate `container-compose` 0.10.1 provider passed all 18 CLI fixtures and
> the real VS Code end-to-end fixture with zero normalized semantic
> differences. The largest CLI ratios were 2.876x for stock Apple and 4.509x
> for `container-compose`; the corresponding VS Code ratios were 1.232x and
> 1.311x. The current source candidate is bound to the newer exact fingerprints
> below; they do not retroactively change the 1.0.1 tag evidence. Repeated-run
> statistics are in [PERFORMANCE.md](PERFORMANCE.md).

The project's north-star goal is 100% behavioural parity with Docker-based Development Containers and comparable or better performance. This document remains the narrower current compatibility contract; [`PARITY-ROADMAP.md`](PARITY-ROADMAP.md) defines the work and evidence required to reach the north star.

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

## Pinned candidate provenance

These pins define the current source candidate's compatibility matrix.
Release-bound evidence also records the signing identity where applicable,
platform triple, and each component's machine-readable version output. The
immutable 1.0.1 matrix remains available from that tag.

| Component | Version or ref | Exact source provenance | Role |
| --- | --- | --- | --- |
| `@devcontainers/cli` | `0.88.0` | Official `v0.88.0` tag commit `f683c29f64a20109b4453e5149807e390ff65133`; npm SHA-512 SRI `sha512-sMkruPy/icfov20mdQh2EjFYZogxvMEZptDEvg5/eMBIUOr2xr+8wlsI7nvDR6EJxoBjqoasXqgRGbiMqbaJ1w==` | Unmodified reference client |
| Docker CLI | `29.6.2` | Executable SHA-256 `eade1c3a5dda47534dc776f2f534c99cc94cfcf9ce07c4bf09e98258d13e7d7a`; Homebrew bottle SHA-256 `b05a401b661f2d0c3b54b10fd1e0c4adb26b479dcfb953d86febfdfb57dd9821` | Unmodified client used by the reference CLI |
| Docker Engine | `29.5.2`, API `1.54`, build `568f755` | Executable SHA-256 `eb4bf018da78f7b9d01d69209d0944d1fe995869ac3caefa5c93e4552e181301` | Behavioral oracle |
| Docker Compose | `5.3.1` | Executable SHA-256 `6c4a20e62f3a776dc7ee603dc296ec63c7194b46067c6461be9208d191c922b3`; Homebrew bottle SHA-256 `9df565543164437312a50347eb2785b59b0f35e9fc1c044aaea5b6fa78952608` | Oracle and stock Compose client |
| `apple/container` stable | `1.1.0` | Annotated tag object `82fc9a5ba73c34c478ce15958bb75dbb45c67e3b`; source commit `5973b9cc626a3e7a499bb316a958237ebe14e2ed` | Initial stable stock lane |
| `apple/containerization` for `container` 1.1.0 | `0.35.0` | Apple resolution/tag object `44bec8b9933bc491d0cbf44abac90a1f6aaebf6b`; source commit `0334a3e790bbed50420de71cd0d706191bdf84d1` | Must be inherited from the Apple `container` resolution |
| `container-compose` stable | `0.10.1` | Annotated tag object `5be84c712176d745b4736e82f97b7458813cb7ec`; source commit `77d2191a75f3a15092bbead1991b0d6a37fafa91` | Optional provider |
| Stable provider's `stephenlclarke/container` | Revision | `367430446959e3048da37f5f64d3c10e1293d3de` | Exact fork dependency declared by `container-compose` 0.10.1 |
| Stable provider's `stephenlclarke/containerization` | Revision | `043193efa5f1a2e21a240041d6edd71d7673739e` | Exact fork dependency declared by `container-compose` 0.10.1 |
| VS Code | `1.131.0`, arm64 stable | Commit `e4c7e7b1d6d060162f4aa7f8225271b67ce1df75`; official archive SHA-256 `796c3ae1cd28d45b3fb8450c0f8661cf2f43632e3a0f38f5025f0c49675bcf99`; application identifier `com.microsoft.VSCode`; Microsoft team `UBF8T346G9` | End-to-end client |
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
- pinned oracle version: `1.54`, from Docker Engine `29.5.2`;
- versions above `1.53`: out of scope until separately pinned and tested;
- versions below `1.44`: out of scope for the initial release.

Version 1.0.1 advertises the contiguous, tested subset `1.44...1.53`.
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
