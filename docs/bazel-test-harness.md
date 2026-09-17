# Bazel test-harness replacement

Status: implementation in progress, not release qualification. The existing harnesses remain until their replacement covers their complete scenario inventory. The operator explicitly authorised replacing them on 17 September 2026. The new implementation must be simpler, faster, recoverable and at least as comprehensive; wrapping the existing lane-wide runner is not the destination.

## Execution boundaries

| Layer | Owner | Cache and recovery boundary |
| --- | --- | --- |
| Product unit tests | Native Bazel Swift test targets | Declared sources, profile and toolchain; retain the original XML and coverage before output reuse |
| Harness unit and socket-component tests | Native Bazel test targets | Deterministic fixtures; no Container, Docker, credentials, downloads or product builds |
| Process/service integration | Explicit native Bazel integration targets | Fresh execution with a host lease; retain process outcome, cleanup and coverage from that execution |
| Runtime parity | One case, lane and explicit campaign identity | Published release inputs only; never compile a missing reference or candidate; successful independent cases can resume within the same campaign |
| Performance campaign | Fresh uncached live cases under a quiet-host lease | Prefetch first; record all setup, operation and cleanup durations and invalidated samples; do not confuse cached test durations with fresh execution |

Bazel owns dependency scheduling, action reuse, test selection and reports. Small test probes and evidence helpers do not schedule builds, silently retry cases or implement another build coordinator. The first direct Engine probe uses standard-library Python solely as a test client and has no Docker CLI dependency, including in candidate lanes.

## Current replacement components

`Tools/testing/case_evidence.py` seals each case in a transactional SQLite record. Its identity binds the explicit campaign, fixture, lane, contract, harness, release set and runtime fingerprints. Reopening a completed case returns the same authenticated result, including failures. A changed result cannot overwrite it. An unfinished case requires worker/resource reconciliation rather than an automatic restart; other independent case identities remain available. Production admission, artifact fingerprinting, host leases and resource reconciliation must be connected before this evidence component can qualify live campaigns.

Results require separate monotonic setup/operation/cleanup durations, exact observations, explicit errors and cleanup status/resource inventory. Missing observations or uncertain cleanup cannot produce a passing result. The comparator rejects absent or duplicate lanes and mixed campaigns/contracts/releases. It reports ordinary duration ratios separately from functional parity; quiet-host paired performance qualification and its 10x/timeout gate remain to be connected. This component does not claim an arbitrary raw timing observation passed the performance gate.

`Tools/testing/engine_probe.py` replaces E01's Docker-CLI-based HTTP access with direct Unix HTTP. It preserves the five checked-in E01 observations: ping, prefixed API, HEAD ping, unknown-route error envelope and malformed-JSON error envelope. Socket component tests exercise actual HTTP traffic, invalid versions, negative responses, oversized replies, missing endpoints and timeouts. They are tests of the harness, not proof against the three real runtimes.

Run the focused native harness checks with `make bazel-harness`. Individual tests and monotonic durations appear in Bazel's JUnit evidence, retained by the existing launcher. Scratch remains on the enrolled SSD. No Swift product compilation is in this target's dependency graph.

`make bazel-prepare-releases` connects the reviewed release-asset lock to executable payloads. Archives remain on internal storage, while expanded payloads live on the enrolled SSD. The existing reference-store lease covers acquisition and preparation. Preparation rejects archive traversal, links, duplicate entries, oversized payloads and missing executables. Apple installer packages are expanded with `pkgutil`, never installed; installer scripts are not executed. Every extracted file and its mode is recorded in an internal inventory before the SSD tree is published, and both the SSD receipt and complete tree are checked against that retained inventory before reuse. Missing SSD trees can be reconstructed from the retained release without compilation; unknown or modified residue is never overwritten. Package expansion does not establish signing/notarization acceptance or prove that the complete runtime closure is ready.

## Cutover requirements

The existing inventory contains 19 fixtures, each required in all three lanes: E01-E06 Engine semantics, D01-D07 devcontainer configuration/lifecycle/features, C01-C04 Compose projection, F01 failure recovery and V01 real VS Code. None may disappear because an adapter is incomplete. The release result must enumerate every expected fixture and assertion and reject omissions, skips, duplicate results or retry-only passes.

Before removing old entry points:

1. Connect the complete verified release/runtime closure, including guest images, Docker VM/oracle, official CLI and VS Code pins. Download GitHub releases; do not invoke SwiftPM or another product build in a parity case.
2. Add strict preparation and one-case adapters for all 19 fixtures, porting useful assertions without retaining lane-wide rebuilds or destructive evidence resets.
3. Connect the shared runtime lease and marker-protected SSD roots; journal owned resources before creating them. Recover an interrupted case only after confirming worker identity and reconciling its owned resources.
4. Retain case evidence internally before cleaning SSD scratch. Generate JSON, JUnit and the human comparison matrix from those same records.
5. Exercise cancellation, deadlines, crash-before/after-seal, corrupt receipts, changed artifacts, cleanup failures and repeated no-change unit requests. Prove completed cases are not unnecessarily re-executed and failed attempts remain visible.
6. Run exact-inventory differential parity against real Docker, stock Apple Container and the enhanced stack. Run at least five quiet paired timing samples; preserve timeout and 10x regressions. Retire the old harness only when the replacement meets these gates.

The present work does not change a support claim, lower the 90% coverage target, qualify a stable release or replace protected CI checks.

## Focused implementation evidence, 17 September 2026

Bazel invocation `f28f40cb-ef90-4bd2-815d-0613ca69ea52` passed both harness targets: 26 case/socket/JUnit tests and 13 release-preparation tests, with zero outer-suite failures, errors or skips. The helper suite passed 84 tests. These are development-worktree results, not immutable-head release qualification. Focused standard-library trace coverage measured 90% for `prepare_releases.py`; it is diagnostic evidence, not a SonarQube result or a substitute for integrating coverage into the new quality gate.

All five real locked assets prepared offline and then reused with identical inventory hashes. Direct version checks reported devcontainer 1.0.1, stock Apple Container 1.4.1, container-compose 0.15.1 and Docker Compose v5.3.1. The enhanced Container shipped inside the Compose 0.15.1 release reports `homebrew-main-352-780a86b995ac`, Container commit `780a86b` and Containerization commit `7e066a3101bc84fa0f7231daf6a03aa9ef62a567`; its embedded runtime revision is not the enclosing Compose release commit. Live admission must bind those actual component identities. No services were started and these version checks are not runtime parity evidence.

## Upstream-first scenario reuse

The replacement should import version-pinned upstream scenarios and assertions wherever possible, rather than inventing an independent definition of Docker or Dev Containers behaviour. The 17 September inspection identified these concrete sources:

| Source | Reuse boundary | Necessary adaptation |
| --- | --- | --- |
| [Docker Compose scenario framework](https://github.com/docker/compose/blob/34d0f701846c9b0d9b7e0903de43255260277311/pkg/e2e/SCENARIO.md) | Fixture projects, step intent, state checks, bounded polling, step timings and diagnostics; `TestRestart` checks that restart preserves container identity | Build the test harness with Bazel, not the product; supply pinned published executables. Replace command dispatch and inspection for native candidate lanes. Do not inherit or print the operator's Docker configuration or contexts. |
| [Moby ping integration tests](https://github.com/moby/moby/blob/568f755ebeb1ac9c6a8febbda6cd371ea0a9630b/integration/system/ping_test.go) | HTTP GET/HEAD, API-version and cache-header assertions at the existing Docker Engine oracle revision | Reuse API assertions without the internal helper that starts a fresh dockerd. Keep daemon-internal/Swarm scenarios explicitly accounted for; do not count an upstream skip as a pass. |
| [Dev Containers reference tests](https://github.com/devcontainers/cli/tree/f683c29f64a20109b4453e5149807e390ff65133/src/test) | Configuration, Features, lifecycle, user/environment and Compose scenarios at the pinned reference CLI revision | Prefetch the released CLI and fixture inputs. Replace source-tarball installation and hardcoded Docker cleanup with the lane adapter; preserve assertions. |

These are inspected reuse candidates, not imported or passing suites yet. Before adoption, record upstream test IDs, immutable revisions, licence attribution and any operational adapter patch. A newer scenario-source revision does not silently upgrade the pinned reference binary. Keep ordinary Go/Python/TypeScript test bodies under Bazel; retain only the project-specific artifact admission, host isolation, recovery and evidence layer. Candidate execution must not acquire a Docker runtime dependency through a copied upstream framework.
