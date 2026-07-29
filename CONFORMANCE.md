# Development Containers standards conformance

<!-- markdownlint-disable MD013 -->

This document states exactly where `devcontainer` 1.0.0 conforms to, delegates, partially implements, does not implement, or has not verified the Development Containers standards. It is intentionally broader than the release fixture ledger in [COMPATIBILITY.md](COMPATIBILITY.md).

## Audit basis

The audit was completed on 29 July 2026 against:

- [`devcontainers/spec` commit `c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421`](https://github.com/devcontainers/spec/tree/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421), including the [`devcontainer.json` property reference](https://github.com/devcontainers/spec/blob/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421/docs/specs/devcontainerjson-reference.md), [base schema](https://github.com/devcontainers/spec/blob/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421/schemas/devContainer.base.schema.json), [Feature specification](https://github.com/devcontainers/spec/blob/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421/docs/specs/devcontainer-features.md), [image metadata](https://github.com/devcontainers/spec/blob/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421/docs/specs/image-metadata.md), [lockfiles](https://github.com/devcontainers/spec/blob/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421/docs/specs/devcontainer-lockfile.md), [declarative secrets](https://github.com/devcontainers/spec/blob/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421/docs/specs/declarative-secrets.md), and [GPU host requirements](https://github.com/devcontainers/spec/blob/c95ffeed1d059abfe9ffbe79762dc2fa4e7c2421/docs/specs/gpu-host-requirement.md);
- official [`@devcontainers/cli` 0.88.0 at commit `f683c29f64a20109b4453e5149807e390ff65133`](https://github.com/devcontainers/cli/tree/f683c29f64a20109b4453e5149807e390ff65133), which is the unmodified parser and lifecycle implementation used by the 1.0.0 release;
- the complete [Dev Containers GitHub organization](https://github.com/devcontainers), including [Features](https://github.com/devcontainers/features), [Templates](https://github.com/devcontainers/templates), [Images](https://github.com/devcontainers/images), and [CI tooling](https://github.com/devcontainers/ci);
- stock [`apple/container` 1.1.0](https://github.com/apple/container/tree/1.1.0) and its resolved [`apple/containerization` 0.35.0](https://github.com/apple/containerization/tree/0.35.0);
- the three release lanes and fixtures defined by [`Tests/Parity/manifest.json`](Tests/Parity/manifest.json).

The specification is a moving upstream branch. This audit is reproducible because it names an exact specification commit and an exact reference CLI commit.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `certified` | The exact behavior is in a release-bound real-Docker, stock-Apple, and optional-provider parity fixture with zero semantic differences. |
| `delegated` | The official CLI, VS Code, or another supporting tool owns the behavior; the bridge is not the parser or user-interface implementation. |
| `partial` | A useful subset works, but at least one standard form is unsupported or behavior differs. |
| `unsupported` | Version 1.0.0 cannot provide the required behavior. |
| `unverified` | Code may pass the request, or the official client may own it, but no release-bound fixture proves the behavior. No support claim is made. |

`delegated` does not automatically mean `certified`. A delegated client feature can still depend on Docker runtime behavior that the bridge does not implement.

## Executive conclusion

Version 1.0.0 is conformant only for the bounded configurations exercised by its 18 CLI fixtures and real VS Code fixture. It is not a complete implementation of every `devcontainer.json` property, every Feature requirement, every Docker Compose configuration, or every Docker `run`/`build` option allowed by the standard.

The official CLI provides standards parsing, metadata merging, variable expansion, Feature resolution, and lifecycle orchestration. This project provides a Docker-compatible transport and Apple runtime adapter for a tested subset. The distinction matters: accepting a JSON property in the official CLI does not prove that every Docker request produced by that property is enforced by stock Apple `container`.

## Known 1.0.0 non-conformances

These are confirmed implementation differences, not merely missing tests.

| ID | Standard or required behavior | 1.0.0 behavior | Impact and workaround |
| --- | --- | --- | --- |
| NC-001 | `runArgs` accepts Docker CLI run arguments | The bridge decodes an allowlisted subset of Docker create fields. Other Docker request members are ignored by Swift decoding instead of being rejected, so an unsupported argument can appear to succeed without being enforced. | Do not use arbitrary `runArgs`. Restrict configurations to the certified fields below and verify security/resource behavior inside the container. Future work must add strict unknown-field validation and explicit translations. |
| NC-002 | `hostRequirements.gpu` causes the reference CLI to request a GPU, normally through Docker `--gpus all` | Docker sends `HostConfig.DeviceRequests`; 1.0.0 does not decode or transport it. Stock Apple `container` 1.1.0 also has no certified GPU path. | GPU-required configurations and GPU-dependent Features are unsupported. There is no stock workaround in 1.0.0. |
| NC-003 | `privileged: true` means Docker `--privileged` semantics | Stock Apple 1.1.0 has no `--privileged`. The adapter maps the request to `--cap-add ALL` inside the per-container VM. That is not full Docker privileged mode and does not supply arbitrary host devices, host namespaces, or Docker-in-Docker semantics. | Treat stock privileged mode as partial. Do not use it for Docker-in-Docker, host-device access, or a Feature that requires exact Docker privileged behavior. |
| NC-004 | `securityOpt` values must be applied | `seccomp=unconfined` is a semantic no-op because Apple containers do not install Docker’s default seccomp profile. Other supported Docker forms, including `no-new-privileges`, cannot be transported by stock Apple 1.1.0 and are rejected. Unrecognized forms are rejected by the router. | The common debugger setting `seccomp=unconfined` works as the already-native state. Other security options require a separately fingerprinted runtime that advertises and enforces them; they are outside the stock claim. |
| NC-005 | Docker run arguments and Compose may set an explicit container hostname | Stock Apple `container` 1.1.0 exposes no hostname field. The bridge rejects non-empty Docker `Hostname` before runtime creation. | Remove `runArgs: ["--hostname", ...]` and Compose `hostname` from stock configurations. Network service aliases used by the certified Compose fixtures remain supported. |
| NC-006 | String-valued `mounts` and `workspaceMount` accept Docker `--mount` fields | 1.0.0 represents type, source, target, and read-only state. It does not represent bind propagation, consistency modes, volume `nocopy`, tmpfs size/mode, or other nested Docker mount options. Unknown nested fields are ignored. | Use basic bind, volume, tmpfs, and read-only forms only. Advanced mount semantics are unsupported even when the create request succeeds. |
| NC-007 | Image-declared anonymous `VOLUME` entries have Docker anonymous-volume lifecycle and storage semantics | The adapter projects anonymous volumes in inspect data but leaves their content on Apple’s native writable root filesystem instead of allocating a separate managed volume. | Do not rely on Docker anonymous-volume persistence, sharing, or cleanup semantics for image-declared `VOLUME` paths. Declare a named volume explicitly when persistence matters. |
| NC-008 | A Docker-compatible runtime can attach or detach a running container from a network when the client requires it | Stock Apple requires networks and aliases at create time. The bridge rejects `network connect` and `network disconnect` after creation. | The certified Dev Container and Compose paths supply networks at creation. Configurations or Compose flows that dynamically change attachments are unsupported. |
| NC-009 | Resource, namespace, device, DNS, host mapping, restart, and similar Docker run options supplied through `runArgs` are applied | Fields such as memory/CPU constraints, shared-memory size, devices, device cgroup rules, extra hosts, DNS settings, PID/IPC/UTS/user namespaces, read-only root, sysctls, restart policy, and stop policy are not part of the 1.0.0 create DTO even where a selected Apple runtime may expose a related flag. | These options are outside the 1.0.0 claim and can be silently omitted because of NC-001. There is no blanket pass-through. Add an explicit parity fixture and translation before treating any one of them as supported. |

Features contribute `privileged`, `capAdd`, `securityOpt`, mounts, lifecycle commands, and other metadata to the merged configuration. A Feature inherits every applicable non-conformance above; a successful installation of the two certified public Features is not a blanket claim for the Feature catalog.

## Platform support boundary

Version 1.0.0 supports Linux `arm64` containers on Apple silicon with macOS
Tahoe. Windows containers, Intel Macs, Linux hosts, remote engines, and
cross-architecture execution are outside the implementation. This is an
explicit product support limitation, not a violation of the Development
Containers Specification: the standard permits products to declare supported
hosts and image platforms.

## Complete `devcontainer.json` property ledger

The following ledger covers every root property in the audited base schema. Related fields are grouped where they share an implementation boundary.

| Property | Status | 1.0.0 evidence and boundary |
| --- | --- | --- |
| `$schema`, `additionalProperties` | `delegated` | JSONC parsing, schema selection, comments, and tool-specific extra properties are owned by the official CLI or supporting tool. The bridge never reads `devcontainer.json`. |
| `name` | `delegated` | Display metadata is owned by the official CLI/VS Code. Names exist in certified fixtures but are not a runtime semantic claim. |
| `image` | `certified` | Public Linux `arm64` images, digest/tag resolution, pull, inspect, create, and reuse are covered. Private-registry credential flows and cross-architecture images are `unverified`. |
| `build.dockerfile`, `build.context`, `build.args`, `build.target` | `certified` | D02 covers the Dockerfile path, external context, argument, target, build stream, and resulting runtime behavior. |
| Legacy `dockerFile`, root `context`, and sibling `build` options | `unverified` | The official CLI still accepts the legacy schema form. The release fixture uses the current object-valued `build` form, so the legacy spelling and merge behavior are not certified. |
| `build.cacheFrom`, `build.options` | `unverified` | The official CLI passes these to Buildx. The bridge’s Buildx path works for certified Feature and Dockerfile builds, but no release fixture covers cache imports or arbitrary build options. The legacy `/build` translation does not model these fields itself. |
| `containerEnv`, `remoteEnv` | `certified` | D01/D03 cover static container environment, remote environment, and `${containerEnv:...}` expansion. |
| `containerUser`, `remoteUser` | `certified` | D03 and the VS Code fixture cover the selected non-root/root execution paths. Arbitrary UID/GID and group combinations are not exhaustive. |
| `updateRemoteUserUID` | `partial` | Explicit `false` is certified. Automatic UID/GID rewrite with `true` is not independently certified. |
| `userEnvProbe` | `partial` | Default probing and explicit `none` occur in release fixtures. Every shell mode and shell family is not certified. |
| `workspaceMount`, non-Compose `workspaceFolder` | `certified` | Basic bind workspace projection and selected folders are covered. Advanced mount fields inherit NC-006. |
| Compose `workspaceFolder` | `certified` | All four Compose CLI fixtures and the VS Code fixture attach to their declared folder. |
| `overrideCommand` | `certified` | Image, Dockerfile, and both `true`/`false` Compose paths are represented in the release fixtures. |
| `shutdownAction` | `partial` | Explicit `stopCompose`, project down, cleanup, and VS Code reopen flows are covered. Explicit `stopContainer` and `none` are not independently certified. |
| `appPort` | `certified` | D06 covers explicit TCP host publishing and collision behavior. Other protocols/forms are not exhaustively certified. |
| `forwardPorts` | `certified` | D06 and real VS Code cover a primary-container TCP forward. Host-qualified service forwards, ranges, and every protocol are not exhaustive. |
| `portsAttributes`, `otherPortsAttributes` | `delegated` | VS Code owns labels, notifications, browser/preview actions, elevation, and local-port policy. The release fixture covers a label and `onAutoForward: silent`; all other UI behavior is `unverified`. |
| `mounts` | `partial` | D07 certifies a string-valued named volume with persistence and cleanup. Basic bind workspace mounts are certified separately, and E06 proves the bridge’s basic bind, volume, tmpfs, and read-only Docker request handling. NC-006 and NC-007 apply to advanced and image-anonymous forms. |
| `init` | `unverified` | The bridge decodes Docker `HostConfig.Init` and maps it to Apple `--init`, with unit coverage. No release parity fixture proves exact signal/reaping behavior. |
| `privileged` | `partial` | NC-003 applies. Enhanced runtime behavior is separately fingerprinted and not attributed to stock Apple. |
| `capAdd` | `partial` | Docker `CapAdd`/`CapDrop` are decoded and mapped to Apple flags with unit coverage. Representative real-runtime capability behavior is not release-certified, and not every Linux capability is available in Apple’s VM. |
| `securityOpt` | `partial` | NC-004 applies. |
| `runArgs` | `partial` | The bridge can enforce an allowlisted subset when the official CLI produces the corresponding Docker create members, but no release fixture sets arbitrary `runArgs` directly. NC-001 and NC-009 apply; user, environment, command, entrypoint, basic mounts, ports, network-at-create, init, capability, and the documented partial security/privileged fields are the only modeled families. |
| `features` | `certified` | D05 covers OCI resolution and installation of two public Features, generated BuildKit context, lockfile use, and frozen-lock failure. Other Features inherit runtime gaps. |
| `overrideFeatureInstallOrder` | `unverified` | Ordering is owned by the official CLI, but no release fixture sets an explicit override. |
| `initializeCommand` | `certified` | D04 proves a string-valued host command in the lifecycle sequence. Array/object forms and repeated-host invocation combinations are not exhaustive. |
| `onCreateCommand`, `updateContentCommand`, `postCreateCommand`, `postStartCommand`, `postAttachCommand` | `partial` | D04 and VS Code certify string-valued ordering and create/start/attach execution; D07 certifies selected reuse behavior. Array forms, object-valued parallel commands, and failure branches are not independently certified. |
| `waitFor` | `unverified` | The default official-CLI behavior is exercised indirectly. No release fixture sets a non-default value. |
| `hostRequirements.cpus`, `hostRequirements.memory`, `hostRequirements.storage` | `delegated` | These are advisory host-selection/warning metadata owned by the official CLI or hosting product. This local bridge does not provision a different Mac or resize Apple’s runtime from these fields. |
| `hostRequirements.gpu` | `unsupported` | NC-002 applies. |
| `secrets` | `delegated` | The declarative property describes desired secret names and optional metadata; the specification does not require one injection mechanism. This project supplies no acquisition UI or secret store. The official CLI’s separate `--secrets-file` injection path is not release-certified through this bridge. |
| `customizations` | `delegated` | Tool-specific configuration such as VS Code extensions/settings is owned by VS Code. Real VS Code activation is certified, but arbitrary customizations are not. |
| `dockerComposeFile`, `service`, `runServices` | `certified` | C01-C04 and V01 cover one/multiple declared services, dependency health, selected primary service, files, environment files, networks, volumes, and lifecycle. The entire Docker Compose Specification is not claimed. |

## Variables and metadata merging

| Standard area | Status | Boundary |
| --- | --- | --- |
| `${localWorkspaceFolder}` | `certified` | D01-D07 use it in the workspace mount source and verify the resulting workspace. |
| `${localEnv:...}`, `${localWorkspaceFolderBasename}`, `${containerWorkspaceFolder}`, `${containerWorkspaceFolderBasename}` | `unverified` | The official CLI owns these substitutions, but the release fixtures do not assert them. |
| `${containerEnv:...}` | `certified` | D03 proves expansion into `remoteEnv`. |
| `${devcontainerId}` | `unverified` | The official CLI owns generation and substitution; no release fixture asserts stability across rebuilds. |
| `devcontainer.metadata` image label merge | `unverified` | The official CLI performs the merge and the bridge preserves labels needed by tested paths. No release fixture independently proves every precedence/merge rule. |
| Feature metadata merge and lifecycle contribution | `partial` | The public Feature fixture installs successfully; contributed privileged/security/mount requirements inherit NC-003, NC-004, and NC-006. |
| `devcontainer-lock.json` | `certified` | D05 proves a resolved lock and rejection when `--frozen-lockfile` cannot use it. |

## Ports, networks, and Compose boundary

The standard deliberately uses Docker Compose as the multi-container orchestrator. The 1.0.0 claim therefore names a tested Compose subset, not the complete Compose Specification.

Certified:

- primary service selection and generated Dev Container overrides;
- `runServices`;
- dependency health gating;
- service DNS and aliases known at create time;
- environment files;
- named networks and volumes;
- bind workspaces;
- recreate, restart, signal, down, discovery labels, and cleanup.

Not certified or unsupported:

- arbitrary Compose build, deploy, device, namespace, security, resource, secret, config, logging, and platform combinations;
- dynamic network connect/disconnect after create (NC-008);
- explicit service hostname on stock Apple (NC-005);
- full Docker privileged/device behavior (NC-002/NC-003);
- arbitrary advanced volume and mount options (NC-006);
- Compose commands outside those used by Dev Containers and the release fixtures.

The optional provider has its own Compose implementation and exact matched runtime. Passing the provider lane does not expand the stock Apple claim.

## Feature, Template, Image, and CI standards

This project is a consumer runtime bridge:

| Upstream standard area | Project responsibility |
| --- | --- |
| Feature distribution and OCI packaging | Delegated to the official CLI and registries. The project does not publish a Feature collection. |
| Template distribution | Not implemented; the project does not publish Templates. Templates that resolve to a certified configuration can still be consumed by the official toolchain. |
| Dev Container Images | Not implemented as an image publisher. Public compatible Linux `arm64` images can be consumed. |
| Dev Container CI | Not reimplemented. Users may use official `devcontainers/ci` only with an available compatible runtime and the documented socket selection; no release fixture certifies that service. |
| Prebuild behavior | Not implemented as a hosting/prebuild service. The local official CLI build path is the supported boundary. |

These are scope boundaries, not claims that upstream projects are incompatible.

## Docker compatibility limits that affect standards behavior

The local service advertises Docker API 1.44 through 1.53 only for endpoints required by the pinned clients and fixtures. It is not a general Docker Engine.

The most important standards risk is unknown-field handling: Swift `Decodable` ignores create/build members that are not present in the DTO. That is why NC-001 and NC-009 explicitly warn that success is not enforcement. The existing design requirement that unsupported fields fail before side effects is true only for fields the router currently decodes and validates, such as a stock hostname, unsupported mount type, recognized-but-unavailable security option, and dynamic network change.

Private registries, credential helpers, registry mirrors, content trust, BuildKit cache exporters/importers, multi-platform manifests, arbitrary Buildx options, swarm, plugins, and general Docker administration are outside the release claim.

## Release parity versus full-standard parity

All release fixtures passed with zero normalized semantic differences. That proves exact parity for their assertions:

- 18 CLI fixtures;
- one real VS Code fixture;
- real Docker oracle;
- stock Apple lane;
- separate `container-compose` provider lane.

It does not prove configurations that the manifest does not contain. The [timing analysis](PERFORMANCE.md) likewise reports only those same fixtures.

## Remediation priorities

| Priority | Work | Required acceptance evidence |
| --- | --- | --- |
| P0 | Reject unknown Docker create/build fields before side effects | DTO unknown-key tests plus real CLI fixtures proving unsupported `runArgs` fail explicitly |
| P0 | Decode and either implement or reject GPU `DeviceRequests` | Required/optional GPU fixtures against Docker and every Apple lane |
| P1 | Separate the stock privileged claim from full Docker privileged semantics | Docker-in-Docker/device/capability parity fixtures or an explicit hard rejection |
| P1 | Expand or hard-reject resource, namespace, device, DNS, host, restart, and stop options | One focused differential fixture per supported field family |
| P1 | Represent advanced mount options or reject them | Bind propagation, consistency, volume `nocopy`, tmpfs size/mode, and cleanup fixtures |
| P2 | Add explicit conformance fixtures for `init`, UID update, lifecycle object/array forms, `waitFor`, feature-order override, metadata merge, secrets file, private registry, cacheFrom, and build options | Zero-difference three-lane recordings for each claim |
| P2 | Profile and reduce the measured runtime overhead | Repeat the exact timing protocol in [PERFORMANCE.md](PERFORMANCE.md) with no semantic changes |

No row should move from `partial`, `unsupported`, or `unverified` to `certified` solely because a unit test passes. Release certification requires the same real-runtime differential evidence as the existing fixture matrix.

## Reporting another gap

When a configuration differs from Docker:

1. capture `devcontainer diagnostics`;
2. record the exact `devcontainer.json`, image digest, official CLI version, runtime distribution, and runtime commit;
3. reduce the behavior to a new parity fixture;
4. classify the owner as this repository, Apple upstream, or the optional provider repository;
5. do not normalize, waive, or retry away the semantic difference.

See [SECURITY.md](SECURITY.md) for private vulnerability reporting.
