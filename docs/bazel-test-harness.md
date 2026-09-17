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

### First released-service integration target

`make bazel-engine-case CAMPAIGN=<explicit-id> LANE=apple-stock` selects the manual, local, uncached `released_engine_negotiation` target. The enhanced alternative is `LANE=container-compose`. Both use only preprepared release payloads; missing or changed assets fail admission without downloads, extraction or builds. The first adapter explicitly supports the reviewed devcontainer 1.0.1 command interface and does not pass newer service flags to an older binary. This is E01 metadata/protocol integration, not a full guest-runtime campaign or a substitute for the Docker oracle. Docker-lane admission and the other 18 adapters remain unfinished.

The case binds the complete release lock, extracted inventories, actual command versions, OS/architecture, harness and expected observations. Setup and cleanup have separate timings from the conformance operation. The process receives an isolated SSD home/temp directory and a minimal environment. Whole-phase deadlines supplement socket timeouts. Bazel SIGTERM enters cleanup; an interrupted process creation is recorded as uncertain, never reported as no child. The internally retained case seal binds both the result and the exact name/digest inventory of attached diagnostics and ownership records, so missing or changed attachments cannot be reused as a passing result.

Stock Apple Container 1.4.1 connects to the fixed per-user `com.apple.container.apiserver` service; changing `HOME` does not isolate that XPC service. Admission therefore checks that launchd has a running API server at the exact selected prepared-release executable, fingerprints its PID, and rechecks the same identity at cleanup. An unrelated Homebrew service, missing service or stopped service is rejected before launching the test. Only those selected identity fields are retained, never launchd's full environment. This read-only check does not provision the service or establish the complete guest/runtime closure. Transactional service selection and restoration remain required before unattended live cutover.

Each protocol request retains its method, route, status or safe exception class, and monotonic duration in `requests.json`, bound into the case seal. Request/response bodies and exception text are not included. This identifies the exact failing endpoint without rerunning a failed case or exposing operator credentials.

The adapter acquires the existing per-user Compose runtime lock. Its established `/private/tmp/container-compose-runtime-<uid>.lock` inode remains the shared IPC coordination point; payloads and disposable work stay on SSD. Before launching a service, the adapter writes an internal `runtime-admission.json` ownership marker. It clears that marker only after verified process cleanup, evidence retention, input revalidation and owned-root removal. A surviving descendant, uncertain spawn, hard-killed worker or other unverified cleanup leaves the marker and blocks all later admissions through the new adapter, even with a different campaign ID. Do not delete the marker or invent a fresh campaign to bypass it.

Automatic hard-kill/resource reconciliation is not yet implemented. Legacy Compose/runtime entry points share the lock but do **not** enforce the new quarantine marker: while it exists, keep those entry points suspended too until the owned process/resources are reconciled. Family-wide guard integration is required before unattended live cutover. An ordinary terminal result can be reopened unchanged within its original campaign; failed results cannot be overwritten by a retry.

### Full harness cutover

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

### First live attempt and diagnostic outcome

Follow-up development-worktree validation passed 53 case/socket/process/JUnit tests and 14 release-preparation tests (Bazel invocation `0ddc3afb-a7ee-4d62-8ed6-aebc2d22f8f4`; preparation reused its unchanged passing result). The 87 workflow-helper tests and focused core Swift target passed, including SSD symlink rejection and the corrected legacy host-test opt-in (core invocation `7614812a-ec74-4a5a-b7ea-c5b484f5dc5c`). These are scoped implementation checks, not full product coverage or parity. Independent complete-source review found no additional actionable findings; exact-head hosted quality and broader cutover gates remain required.

The first released E01 attempt (`released-e01-20260917a`, Bazel invocation `592dbc57-f4a2-4930-b76e-011e514c1164`) timed out after 5.003 seconds in the operation phase; setup took 0.531 seconds and verified cleanup took 0.254 seconds. Its immutable failure remains retained and is not stock parity evidence. Diagnosis found that the fixed API service pointed to the existing Homebrew enhanced runtime, not the prepared stock release; launchd reported exit 1 and repeated provider-binding/key mismatch errors. A bounded direct `system version` diagnostic also timed out after 10 seconds. This motivated the exact-service admission check above. No provider keys or existing state were removed, no service was replaced, and neither a new campaign nor a longer timeout is used to hide this failure.

## Upstream-first scenario reuse

The replacement should import version-pinned upstream scenarios and assertions wherever possible, rather than inventing an independent definition of Docker or Dev Containers behaviour. The 17 September inspection identified these concrete sources:

| Source | Reuse boundary | Necessary adaptation |
| --- | --- | --- |
| [Docker Compose scenario framework](https://github.com/docker/compose/blob/34d0f701846c9b0d9b7e0903de43255260277311/pkg/e2e/SCENARIO.md) | Fixture projects, step intent, state checks, bounded polling, step timings and diagnostics; `TestRestart` checks that restart preserves container identity | Build the test harness with Bazel, not the product; supply pinned published executables. Replace command dispatch and inspection for native candidate lanes. Do not inherit or print the operator's Docker configuration or contexts. |
| [Moby ping integration tests](https://github.com/moby/moby/blob/568f755ebeb1ac9c6a8febbda6cd371ea0a9630b/integration/system/ping_test.go) | HTTP GET/HEAD, API-version and cache-header assertions at the existing Docker Engine oracle revision | Reuse API assertions without the internal helper that starts a fresh dockerd. Keep daemon-internal/Swarm scenarios explicitly accounted for; do not count an upstream skip as a pass. |
| [Dev Containers reference tests](https://github.com/devcontainers/cli/tree/f683c29f64a20109b4453e5149807e390ff65133/src/test) | Configuration, Features, lifecycle, user/environment and Compose scenarios at the pinned reference CLI revision | Prefetch the released CLI and fixture inputs. Replace source-tarball installation and hardcoded Docker cleanup with the lane adapter; preserve assertions. |

These are inspected reuse candidates, not imported or passing suites yet. Before adoption, record upstream test IDs, immutable revisions, licence attribution and any operational adapter patch. A newer scenario-source revision does not silently upgrade the pinned reference binary. Keep ordinary Go/Python/TypeScript test bodies under Bazel; retain only the project-specific artifact admission, host isolation, recovery and evidence layer. Candidate execution must not acquire a Docker runtime dependency through a copied upstream framework.
