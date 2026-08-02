# Full parity and performance roadmap

## Decision

The project's north-star goal is 100% behavioural parity with Docker-based Development Containers on Apple silicon, with comparable or better user-visible performance.

This goal applies to the complete Development Containers surface, not to unrelated Docker Engine workloads. It includes the current Development Containers specification, the official CLI and VS Code extension, image and Dockerfile configurations, Features, lifecycle commands, users, mounts, ports, supported Compose configurations, failure behaviour, cancellation, cleanup, and the Docker API observations those clients consume.

Until that goal is proved, releases must continue to make bounded compatibility claims. A passing subset is useful evidence, but it is not 100% parity.

## Definition of success

### Functional parity

Full functional parity requires all of the following:

1. Every property in the pinned `devcontainer.json` schema has a machine-readable status and a differential fixture.
2. Every Docker request field emitted by the pinned official CLI is implemented exactly or rejected before side effects. Unknown runtime-affecting fields never fail open.
3. Every supported fixture records zero unexplained semantic differences against Docker in the stock Apple and `container-compose` lanes.
4. The real VS Code extension passes representative image, Dockerfile, Feature, Compose, rebuild, reopen, cancellation, and recovery journeys.
5. Resource lifecycle, event order, error class, identifier resolution, cleanup, and crash recovery match the Docker oracle.
6. Unsupported Apple primitives are tracked as blockers to the north-star goal. They are not normalised, waived, or presented as parity.

### Performance

Comparable or better means the candidate's measured user-visible duration is at most `1.00x` the matching Docker oracle. This is the objective, not a permissive upper limit.

Performance certification should use paired runs on the same host, with at least five cold and ten warm iterations per affected fixture and lane. Functional parity is judged separately.

| Measure | Required objective |
| --- | ---: |
| Warm CLI aggregate median | At most `1.00x` Docker |
| Real VS Code end-to-end median | At most `1.00x` Docker |
| Material per-fixture warm overhead | No statistically meaningful slowdown |
| Warm p90 | No statistically meaningful slowdown |
| Candidate regression against the previous certified Apple result | No regression |
| Peak resident memory and CPU time | At or below Docker |

Any completed candidate result above `2.50x` Docker requires further investigation. That threshold is a triage trigger, not a performance pass criterion and not a functional-parity failure. Results above `1.00x` miss the objective even when they do not trigger the investigation threshold. A timeout, other non-completion, or missing or invalid timing evidence remains a hard evidence failure.

Cold-start and warm-reuse results must be reported separately. An optimisation is accepted only when its median improvement exceeds baseline variation, its p90 does not worsen materially, resource use remains bounded, and semantic observations remain identical.

## Review basis

This review was completed on 30 July 2026 against `main` commit `b31e80b2b9c09ecc73bb3badf9cd5cf16550a538`.

The evidence included:

- the complete source, tests, documentation, package, workflows, release tooling, and parity fixtures in this repository;
- the pinned stock `apple/container` 1.1.0 and `apple/containerization` 0.35.0 sources;
- the separately installed `container-compose` 0.10.1 boundary and its current open work;
- `make check`, which passed 148 Swift tests, all Python harness tests, formatting, lint, documentation generation, parity-manifest validation, and above 91% first-party line coverage;
- successful hosted CI, AddressSanitizer, ThreadSanitizer, CodeQL, SonarCloud, documentation, Homebrew, dependency-review, and live runtime workflows on the exact reviewed commit;
- [live three-lane parity run 30522304399](https://github.com/stephenlclarke/devcontainer/actions/runs/30522304399), which recorded zero semantic differences across all 18 CLI fixtures and the real VS Code fixture;
- [draft performance PR 10](https://github.com/stephenlclarke/devcontainer/pull/10), reviewed as in-flight work and not as behaviour present on `main`;
- the pinned Moby implementation where exact Docker error or identifier behaviour needed confirmation.

SonarCloud reported 91.2% coverage, zero bugs, zero vulnerabilities, zero code smells, and 0.4% duplication. These automated results are valuable but do not disprove the behavioural and architectural findings below.

## Current baseline

### Functional

The current release evidence is strong within its declared boundary: 18 CLI fixtures and one real VS Code fixture pass with zero recorded semantic differences. [`CONFORMANCE.md`](CONFORMANCE.md) still records nine confirmed non-conformances and several partial or unverified Development Containers properties. The project therefore has bounded parity, not full parity.

### Current performance

The exact reviewed `main` run produced:

| Scope | Docker | Stock Apple | `container-compose` | Stock/Docker | Provider/Docker |
| --- | ---: | ---: | ---: | ---: | ---: |
| 18 CLI fixtures, aggregate | 83.072s | 111.408s | 115.412s | `1.341x` | `1.389x` |
| Real VS Code fixture | 36.353s | 40.182s | 51.618s | `1.105x` | `1.420x` |

The C01 Compose service fixture was `4.350x` Docker on stock Apple and `2.704x` on the provider. Both results exceed the current `2.50x` investigation trigger. The aggregate CLI and VS Code results do not cross that trigger, but all four candidate ratios above `1.00x` miss the comparable-or-better objective.

The largest current stock-Apple absolute overheads were C01 Compose service (+5.383s), D05 Features (+5.037s), D07 reuse and cleanup (+3.691s), C04 Compose lifecycle (+3.525s), and E06 network and volume handling (+2.907s). The provider's largest overheads were D05 (+6.708s), C04 (+3.717s), D07 (+3.577s), C02 Compose dependencies (+3.382s), and E06 (+3.055s).

The PR 10 performance work is integrated in the current candidate with two
correctness constraints added during review: enhanced distributions retain
their additive inventory schema instead of being decoded through stock 1.1.0
types, and managed-host caching is invalidated after every runtime bootstrap.
The branch's earlier E06 medians improved 16.6% on stock Apple and 17.7% on
the custom runtime, and stock VS Code reached `1.006x` Docker. Those historical
measurements do not certify the corrected integrated source.

The corrected integration worktree subsequently passed all 54 CLI
fixture-lane executions with zero semantic or cleanup differences. Its
single-run aggregate times were 163.599s for Docker, 158.780s for stock Apple,
and 154.025s for the provider. Cache state was not balanced across the three
sequential lanes, so those totals are functional evidence and raw timing
evidence, not performance certification. The full matrix and every result
above the `2.50x` investigation trigger are recorded in
[`PERFORMANCE.md`](PERFORMANCE.md). Exact-head hosted CLI and VS Code evidence
remains the merge gate.

The final source then passed a fresh 54-execution CLI matrix and the real VS
Code journey in all three lanes on `ULTUK2M30000`. The CLI aggregates were
76.807s for Docker, 168.097s for stock Apple, and 385.353s for the provider.
The VS Code times were 40.379s, 55.053s, and 110.625s respectively.
Behavioural parity passed with zero semantic or cleanup differences. The raw
aggregate ratios missed the comparable-or-better objective, 14 CLI fixtures
crossed the `2.50x` investigation trigger, and provider VS Code reached
`2.740x`. Cache state was not balanced, so the results are not performance
certification; [`PERFORMANCE.md`](PERFORMANCE.md) records the complete matrix
and limits.

That run includes the final E03 reliability correction. Non-terminal direct
exec uses a socket half-close so Apple descriptor copies cannot suppress stdin
EOF; terminal exec retains the PTY-backed path. Ten consecutive live 4 MiB
duplex stress runs and the complete matrix passed after this change.

## Implementation status

The implementation work below was applied to the current worktree on 30 July
2026. “Implemented” means production wiring, focused regression tests, and the
local three-lane CLI matrix exist. It does not replace exact-head hosted CLI
and VS Code evidence or the repeated performance protocol. “Partial”
identifies the remaining proof or primitive rather than normalising it.

| Programme item | Current status | Remaining boundary |
| --- | --- | --- |
| PAR-001 | Implemented for the currently modelled create, exec, network, and volume request objects | Generate and maintain complete endpoint and API-version coverage from the Docker schema |
| PAR-002 | Implemented fail-closed stock behaviour | A future tagged Apple API must preserve legal embedded `=` labels before stock support can be advertised |
| ARC-001 | Partial: production Docker and Compose mutations use keyed coordination, ownership labels, intent records, and explicit unfinished-operation recovery state | Deterministic phase-by-phase runtime reconciliation and safe automatic resume remain required |
| ARC-002 | Partial: correlation, deadline propagation, disconnect cancellation, hashed idempotency keys, request conflict detection, and replay are wired | Replay results must persist across service restart and be reconciled with native runtime state |
| ENG-001 | Partial: connection, pending-request, 512 MiB per-request, 1 GiB process-wide body, and write-buffer bounds are enforced; completed hijacks release the shared 64-connection budget | Build, image, and archive uploads still need private-file or byte-stream transfer and the 1 GiB streamed-context/RSS acceptance test |
| ENG-002 | Implemented with exactly-once direct session cancellation | Full live runtime process-tree evidence remains part of release certification |
| PROC-001 | Implemented through the shared `DevContainerProcess` supervisor | None locally; live provider and runtime certification remains |
| ENG-003 and ENG-004 | Implemented with awaited streamed writes, cancellation, byte accounting, and encoded Docker error envelopes | Slow-reader live evidence remains |
| PAR-003 to PAR-006 | Implemented with ambiguity detection, truthful `/info`, Docker default bind semantics, and immutable image IDs | Exact live IPv6 binding observations remain open |
| STATE-001 and STATE-002 | Implemented with explicit v2-to-v3 migration, future-version rejection, integrity checks, retention, and WAL checkpoints | None locally |
| OBS-001 | Partial: shared privacy redaction and structured request completion measurements are wired | Native-call phase spans, CPU/RSS, cache, and round-trip counters must be retained in parity artefacts |
| OPT-001 | Partial: reusable stock inventory, distribution-safe file and network clients, archive transfer, immediate event wakeups, restart-safe managed-host caching, and PTY-backed interactive exec passed the final local three-lane CLI and real VS Code matrices | The hosted workflow must retain exact-head evidence; the repeated performance protocol remains |
| OPT-002 | Blocked in this repository | Compose model caching belongs in `container-compose`, preserving the provider boundary |
| OPT-003 | Implemented with immediate owned-mutation wakeups plus bounded external-writer polling | Native runtime events should replace the residual poll when a tagged stable API exists |
| OPT-004 and OPT-005 | Partial | End-to-end upload streaming and parity-artifact resource measurements remain |
| TEST-001 | Implemented through `spec-coverage.json`, fail-closed validation, and scheduled upstream schema drift detection | Blocked rows must be closed before a full-parity claim |
| TEST-002 | Blocked | The real VS Code matrix still contains one representative workspace |
| TEST-003 | Implemented for checked fixtures: images use digests and Feature tags are bound by a checked integrity lock | Live preflight must continue to verify each resolved payload |
| TEST-004 | Partial: deterministic malformed-request and generated unknown-field corpora run in the Swift suite | Continuous hosted fuzzing and retained minimised crash reproducers remain |
| TEST-005 | Partial: exact wire/default/unknown/malformed DTO tests were broadened | Endpoint files should be split and behavioural DTO coverage must be measured above 80% |
| GOV-001 | Explicit exception | CodeQL is disabled until the owner requests re-enablement; live `main` protection currently requires `Validate` only |
| GOV-002 | Partial | Independent release review, project-age evidence, and Best Practices badge decision remain governance work |
| DOC-001 | Implemented for the changed production paths, final local CLI and VS Code matrices, and current blockers | Retain the exact-head hosted artefacts with the merge and release evidence |

## Priority model

| Priority | Meaning |
| --- | --- |
| P0 | Silent semantic loss, unsafe resource behaviour, leaked work, or an architectural control claimed by the design but absent from production |
| P1 | Material parity, reliability, security, performance, or release-control defect |
| P2 | Correctness or maintainability debt that should be resolved before full certification |
| P3 | Useful hardening or optimisation after the higher-priority contract is sound |

## P0 correctness and reliability findings

### PAR-001: Unknown Docker request members fail open

**Evidence:** `DockerCreateContainerRequest`, `DockerHostConfig`, `DockerMountRequest`, networking DTOs, and related build DTOs use allowlisted `CodingKeys`. Swift's synthesised decoding ignores every other key. This is already recorded as NC-001, NC-006, and NC-009. `DockerDTOs.swift` has only 24.53% line coverage in the reviewed coverage run.

**Impact:** A `runArgs`, Feature, or Compose option can appear to succeed even though memory, CPU, devices, DNS, namespace, mount, restart, security, or other requested behaviour was discarded. This is the most direct contradiction of the project's parity promise.

**Solution design:**

1. Introduce strict keyed decoding at every runtime-affecting object boundary, including nested `HostConfig`, `Mounts`, `NetworkingConfig`, endpoint, build, health, and device objects.
2. Maintain API-version and endpoint-specific allowlists rather than one global list.
3. Classify known client-only metadata separately from runtime-affecting fields.
4. Translate a supported field into a typed provider-neutral request, or return a Docker-shaped `400`/unsupported error before creating volumes, networks, containers, or metadata.
5. Generate coverage cases from the Docker Engine schema so newly introduced fields cannot silently bypass the check.

**Acceptance:** Unknown root and nested keys fail before side effects in raw HTTP tests; representative unsupported `runArgs`, Feature contributions, and Compose fields fail identically in all Apple lanes; every newly supported key has Docker-oracle evidence.

### PAR-002: Legal Docker label values containing `=` are silently omitted from the native container

**Evidence:** `AppleContainerRuntime.containerConfigurationArguments` drops a label when its key or value contains `=` because the Apple 1.1 CLI parser rejects it. The complete label is retained in SQLite metadata and then projected by Docker list and inspect.

**Impact:** Docker inspection claims the label exists while native runtime inventory does not. Cleanup, Compose identity, tooling, or user logic reading native labels can observe a different truth. Docker label values may legally contain `=`.

**Solution design:**

1. Prefer the typed Apple API for labels if it preserves arbitrary legal values.
2. If the stock API cannot represent the value, fix the upstream Apple CLI/API parser and consume the first tagged release containing that capability.
3. Until then, reject the create request before side effects. Do not project an unenforced label as native state.
4. Keep project-critical ownership labels within a separately validated safe subset.

**Acceptance:** A real Docker differential fixture covers empty values, Unicode, embedded `=`, long values, list/filter behaviour, native inventory, inspect, cleanup, and recreation. The native and Docker-visible label sets must match exactly.

### ARC-001: The mutation journal and crash-recovery coordinator are not wired into production

**Evidence:** `ProjectCoordinator.withMutation` is instantiated only by tests. Production Docker routes create a bare `RuntimeRequestContext`; production runtime mutations do not call `withMutation`, `recordResource`, or `appendEvent`. Compose claims a project lease but runs the child command outside `ProjectCoordinator`. The service opens the SQLite store but never replays `recoveryOperations`.

**Impact:** The design describes intent recording, generation changes, keyed mutation locks, resource reconciliation, and startup recovery that the running service does not perform. A crash between native side effects and metadata update can leak resources or leave a false clean state.

**Solution design:**

1. Route every mutating Docker endpoint and Compose command through one production mutation coordinator.
2. Derive the canonical project key from Dev Containers and Compose ownership labels, not a name heuristic.
3. Record intent and the request hash transactionally before the first native side effect.
4. Record each observed resource immediately after its side effect and mark the operation applied and committed only after final reconciliation.
5. Add project- and resource-keyed asynchronous locks shared by Docker and Compose paths.
6. On startup, inspect unfinished operations, reconcile runtime labels and state, resume idempotent work where safe, and emit an explicit failed/manual-recovery result otherwise.
7. Make cleanup authority require both the project identity and recorded invocation ownership.

**Acceptance:** Deterministic crash injection at each operation phase proves restart convergence, no duplicate side effects, and no leaked resources. Concurrent Docker and Compose mutations on one project serialize; unrelated projects remain concurrent.

### ARC-002: Request context, deadlines, idempotency, and correlation are effectively unused

**Evidence:** `DockerRouter` constructs `RuntimeRequestContext()` with random identifiers and no project, generation, or deadline. Runtime source does not read `context.deadline`, `operationID`, `correlationID`, `project`, or `generation`. The design states that every request contains and honours these controls.

**Impact:** A disconnected or timed-out client can leave work running. Retries cannot safely deduplicate. Errors and logs cannot be joined to the originating request. The mutation coordinator cannot enforce generation ownership.

**Solution design:**

1. Create the context once at the connection/router boundary with an explicit correlation identifier, deadline policy, backend fingerprint, project lease, operation identifier, and generation.
2. Propagate it through every runtime and provider call.
3. Check deadlines before and after native calls and use task cancellation to stop owned work.
4. Store the idempotency key and request hash in the mutation journal; return or reconcile the previous result for a safe replay.
5. Add the correlation identifier to Docker error envelopes and privacy-redacted structured logs.

**Acceptance:** Deadline, disconnect, retry, duplicate request, and cancellation tests prove bounded completion and one native side effect. Diagnostics can trace a request across engine, runtime, state, and provider records.

### ENG-001: Request buffering permits excessive process-wide memory use

**Evidence:** production limits allow a 512 MiB request, 512 MiB retained body
per connection, a 1 GiB process-wide retained-body budget, and 64 active
connections. Each body is accumulated in a `ByteBuffer`, converted to `Data`,
and passed through the router. Buildx exported a 100,132,864-byte Feature image
through `/images/load`; the previous 64 MiB limit rejected that valid request.

**Impact:** Concurrent build contexts or image loads can exhaust memory. Large contexts incur extra copies and delay processing until the entire body arrives. A local user process can use the user-owned socket to create severe memory pressure.

**Solution design:**

1. Set small per-route JSON limits and incrementally decode JSON bodies.
2. Stream build contexts, image archives, and container archives into private bounded temporary files or asynchronous byte streams.
3. Enforce a process-wide body-memory budget and active-connection limit.
4. Use NIO channel read backpressure with high- and low-water marks.
5. Delete temporary data on success, error, cancellation, disconnect, and restart.
6. Record request bytes, peak buffered bytes, and rejected requests locally.

**Acceptance:** Slow-client, oversized-body, 1 GiB streamed-context, concurrent-upload, cancellation, and restart tests remain within an explicit RSS budget and leave no temporary files or runtime resources.

### ENG-002: Disconnecting a hijacked stream does not reliably cancel the owned runtime session

**Status:** Closed in the current source candidate by adopting `container-engine-api` 0.1.0.

**Evidence:** The shared `DockerRawStreamHandler.channelInactive` now owns one `DockerHijackCancellation`, calls the session's cancellation operation directly and exactly once on abnormal closure, and leaves the ordered input pump responsible only for input ordering. Devcontainer's ten shared-listener integration tests retain the early-half-close, completed-hijack, disconnect, and connection-budget coverage.

**Impact:** Closing VS Code, Docker exec, attach, or a terminal connection can leave the guest or Apple CLI process running and retain pipes or other resources.

**Solution design:**

1. Give the raw stream handler explicit ownership of a single session-cancellation operation.
2. On abnormal channel closure, call `session.cancel()` directly and exactly once, then await bounded termination.
3. Make the input pump responsible only for ordered input and EOF, not implicit process ownership.
4. Preserve the normal path where a completed runtime session closes the channel without a cancellation signal.

**Acceptance:** Tests close the client during idle, active stdin, active stdout, and a TERM-ignoring process tree. The runtime session records exactly one cancellation, all descendants terminate within the bound, and every pipe reaches EOF.

### PROC-001: Child-process ownership and cancellation are incomplete and duplicated

**Evidence:** `AppleProcessSession`, `AppleCommandRunner`, `ExecutableComposeProvider`, `DevContainerComposeCommand`, `DoctorCommand`, and `DiagnosticsCommand` contain separate Foundation `Process` implementations. Cancellation usually calls `process.terminate()` on the leader only, with no process group, escalation, or guaranteed reap. `AppleCommandRunner.run` has no cancellation handler around its captured command.

**Impact:** Cancelled builds, pulls, provider calls, diagnostics, or runtime helpers can outlive their Swift task, retain pipes, block completion, or mutate runtime state after the client has gone.

**Solution design:**

1. Introduce one internal process supervisor with captured, inherited, and streaming I/O modes.
2. Start each owned command in its own process group.
3. Treat pre-launch cancellation, launch/cancel races, exit, pipe EOF, and completion as one locked state machine.
4. On cancellation, send `SIGTERM` to the group, wait a bounded grace period, escalate to `SIGKILL`, drain pipes, reap the leader, and return `CancellationError`.
5. Keep terminal job-control rules explicit for inherited I/O.
6. Reuse the proven process-ownership model already implemented in `container-compose` issue 156 without importing `ComposeCore` into this project's core.

**Acceptance:** Repeated tests cover pre-launch cancellation, TERM-ignoring leaders and descendants, large stdout/stderr, closed stdin, terminal foreground restoration, late callbacks, and zero surviving PIDs.

## P1 protocol, state, and operational findings

### ENG-003: Generic streamed responses ignore socket backpressure and outlive disconnects

**Evidence:** The adopted `ContainerUnixHTTPServer.streamBody` retains and cancels the legacy stream task but still schedules each chunk with `eventLoop.execute` and `writeAndFlush` without awaiting the write. Pull-based `managedStream` responses and raw hijacks do await each channel write, so provider-push legacy streams remain the evidence-backed gap.

**Impact:** Fast image-build, pull, log, or event producers can queue data faster than the client drains it. A disconnected client can leave the producer consuming work and attempting writes.

**Solution design:** retain one response-stream task per connection; await NIO writes; suspend when the channel is not writable; batch small frames; cancel the producer on disconnect; and finish the HTTP response exactly once.

**Acceptance:** A slow reader and a mid-stream disconnect keep queued bytes under a fixed bound, cancel the source promptly, and preserve Docker chunk framing and terminal error behaviour.

### ENG-004: Server-generated Docker error JSON is not safely encoded

**Status:** Closed in the current source candidate by adopting `container-engine-api` 0.1.0.

**Evidence:** `ContainerUnixHTTPServer.writeError` uses the shared `DockerErrorEnvelope` and deterministic `DockerJSON` encoder. Devcontainer retains a regression test with quotes, Unicode, backslashes, control characters, and newlines.

**Impact:** Quotes, backslashes, control characters, or newlines in an internal error can produce invalid JSON and a non-Docker error response.

**Solution design:** use the same `DockerErrorEnvelope` and encoder as router errors, with a single server/router error writer and a guaranteed JSON fallback.

**Acceptance:** Injected errors containing quotes, Unicode, backslashes, control characters, and newlines decode as one valid Docker error envelope.

### PAR-003: Ambiguous container ID prefixes are reported as missing

**Evidence:** `resolvedContainerSnapshot` throws `notFound` whenever the prefix match count is not exactly one. Moby's [`GetByPrefix`](https://github.com/moby/moby/blob/3ed37aa574ce8a5d23cd3456b5a51064074dc42c/daemon/container/view.go#L85-L113) returns an invalid-parameter error for multiple IDs and not-found only for zero matches.

**Impact:** Clients receive `404` instead of Docker's invalid-request response and cannot distinguish a typo from an ambiguous short identifier.

**Solution design:** return the exact match first; return not-found for zero prefix matches; return invalid-request for more than one; and use the same resolver for inspect, start, stop, exec, archive, remove, and network operations.

**Acceptance:** Raw Docker-oracle tests create colliding prefixes and compare status, error envelope, exact-ID precedence, and behaviour across all identifier-taking endpoints.

### PAR-004: `/info` advertises memory-limit support that the create API does not enforce

**Evidence:** `DockerInfoResponse.memoryLimit` is hard-coded to `true`, while memory fields are absent from `DockerHostConfig` and NC-009 records them as silently omitted.

**Impact:** The official CLI or other Docker clients can select behaviour based on a capability the runtime does not actually provide.

**Solution design:** derive every `/info` capability from the selected runtime descriptor and the bridge's decoded request surface. Report `false` until the field is translated and live-certified.

**Acceptance:** Capability flags are cross-checked against accepted create fields and real runtime effects for stock and provider lanes.

### PAR-005: Empty `HostIp` is forced to loopback instead of following Docker binding semantics

**Evidence:** `DockerRouter.portBindings` replaces an empty address with `127.0.0.1`. Docker normally applies the daemon/network default, commonly all interfaces. D06 proves connectivity and collision handling but does not assert the exact host bind address.

**Impact:** A Development Container port requested with Docker defaults is reachable from a different network scope. The loopback choice is safer, but it is a semantic difference and cannot be called 100% parity.

**Solution design:**

1. Record the exact Docker oracle result for an empty `HostIp`, explicit loopback, explicit IPv4, and IPv6.
2. Make the default Docker-compatible.
3. If the project retains a safer loopback policy, expose it as an explicit user option rather than silently changing the request.
4. Verify both native listener scope and Docker inspect projection.

**Acceptance:** D06 asserts bind addresses and remote/local reachability, not only successful local connectivity.

### PAR-006: Container summaries use an image reference as `ImageID`

**Evidence:** `containerSummary` assigns `snapshot.spec.image` to both `Image` and `ImageID`.

**Impact:** Docker clients expecting an immutable content identifier can receive a tag or name in the ID field. Cache, reuse, rebuild, and image-change decisions may be wrong.

**Solution design:** resolve and store the immutable image digest/ID when creating or reconciling a container; keep the user reference separately; update it after pull/build/tag operations.

**Acceptance:** Tag movement, digest references, multiple aliases, rebuild, list, and inspect match Docker's `Image` and `ImageID` fields.

### STATE-001: The schema migration function cannot safely perform a future upgrade

**Evidence:** schema version 2 was the repository's initial schema. `migrate` runs `CREATE TABLE IF NOT EXISTS` and then advances any older stored version directly to 2. It has no ordered `vN` to `vN+1` changes or schema-shape verification.

**Impact:** The next column, constraint, or data change could mark an old database current without actually changing its tables.

**Solution design:** implement explicit transactional migrations for each version; validate table and index shape with SQLite pragmas; run foreign-key and integrity checks; retain a backup/rollback path; and refuse unknown gaps.

**Acceptance:** Checked-in databases for every historical schema upgrade to the current version with data preserved; malformed and future schemas fail without mutation.

### STATE-002: Operations and events have no bounded retention policy

**Evidence:** completed operations are deleted only when a project lease is released. Events are never pruned. These tables are mostly dormant today, but wiring the documented coordinator and event journal would make them grow indefinitely.

**Impact:** Long-lived projects can accumulate state, slow diagnostics and startup, and grow the WAL/database without a limit.

**Solution design:** define time and count retention, preserve unfinished operations, compact only below the oldest active event cursor, checkpoint WAL safely, and expose bounded retention statistics in diagnostics.

**Acceptance:** High-volume event and mutation tests keep database size within the configured bound while active cursors, recovery records, and recent diagnostics remain correct.

### OBS-001: Structured observability and privacy claims are not implemented

**Evidence:** no metrics implementation consumes request latency, reconciliation outcome, leak count, or stream termination. Runtime context correlation is not logged. Engine errors and the socket path are sent to the logger without `DiagnosticsRedactor`, while the design states that values are privacy-redacted before emission.

**Impact:** Performance regressions and leaked work are harder to localise, and future enabled logging can disclose home paths, image references, or command error text.

**Solution design:** add one privacy-redacting structured log layer; propagate correlation, endpoint, project pseudonym, provider, duration, bytes, and termination reason; expose local metrics through diagnostics rather than outbound telemetry; and unit-test redaction before the log handler.

**Acceptance:** End-to-end tests join one request across layers, verify required fields, and prove that home paths, credential-shaped values, environment secrets, and raw command arguments are absent.

### GOV-001: Live branch protection does not require all gates claimed by `QUALITY.md`

**Evidence:** live `main` protection requires `Validate` and `CodeQL`. `QUALITY.md` describes ASan, TSan, Sonar, dependency review, documentation, and package/Homebrew validation as merge requirements. Those workflows run, but they are not all branch-protection requirements.

**Impact:** A maintainer or automation path can merge while a documented gate is failing or absent.

**Solution design:** create one stable required-check aggregator that depends on every merge gate, or mark each stable check name as required; require branches to be up to date; prevent bypass for normal release-facing changes; and test required-check names against the workflows.

**Acceptance:** A temporary failing gate blocks merge in a controlled test, and repository tests fail when a documented required check disappears or changes name.

## Complete conformance solution programme

The following designs summarise the confirmed non-conformances in [`CONFORMANCE.md`](CONFORMANCE.md). PAR-001 covers NC-001. The exhaustive field-by-field implementation, upstream dependency, ownership, and acceptance design is in [`UNSUPPORTED-CAPABILITIES.md`](UNSUPPORTED-CAPABILITIES.md); that catalogue is authoritative when this summary groups multiple Docker fields under one row.

| Gap | Solution design | Required acceptance evidence |
| --- | --- | --- |
| NC-002 GPU `DeviceRequests` | Decode Docker device requests and Dev Containers GPU requirements into a typed GPU request. Discover runtime devices and capabilities. Use a tagged stock Apple API only when it can provide the requested guest-visible GPU semantics; otherwise fail before creation. | Required, optional, absent, selected-device, capability, Feature, and failure fixtures against Docker and every Apple lane; real workload proof, not device metadata alone |
| NC-003 privileged mode | Model privileged mode separately from `capAdd ALL`. Define the Dev Containers observable contract for devices, mounts, nested Docker, sysctls, namespaces, and security controls. Add or upstream a native Apple primitive that satisfies that contract. | Docker-in-Docker, device access, mount, capability, sysctl, and denial tests; stock mode remains rejected until all required observations match |
| NC-004 security options | Parse each Docker security option into a typed policy. Preserve the valid `seccomp=unconfined` no-op only when the Apple default is proved equivalent. Add tagged runtime support for `no-new-privileges`, seccomp profiles, labels, and system-path settings where Dev Containers uses them. | Positive and negative security-state probes inside the guest, error-shape checks, and proof that unsupported options have no side effects |
| NC-005 hostname | Use a typed create-time hostname field or an upstream Apple flag in a tagged release. Keep name, hostname, service aliases, `/etc/hostname`, and inspect projection distinct. | Image and Compose fixtures covering explicit hostname, default hostname, aliases, DNS, restart, and recreate |
| NC-006 advanced mounts | Extend `RuntimeMount` with bind propagation, consistency, volume `nocopy`, tmpfs size/mode, and other required nested fields. Translate them exactly or reject them before volume creation. | Per-option filesystem, propagation, mode, persistence, and cleanup comparisons |
| NC-007 image anonymous volumes | Allocate a managed anonymous volume per declared image target, seed it with image content using Docker copy-up rules, attach it separately from the writable root, and track remove/auto-remove ownership. | Image `VOLUME` content, persistence, sharing isolation, inspect, commit/recreate, `rm -v`, auto-remove, and leak checks |
| NC-008 dynamic network attachment | Add or upstream native attach/detach APIs with aliases and IP configuration. Do not emulate with container recreation because that changes identity and lifecycle. | Running-container connect/disconnect, aliases, DNS, inspect, events, force, failure, and cleanup comparisons |
| NC-009 resource, namespace, device, DNS, host, restart, and stop options | Implement the typed capability slices UC-CON-002/003, UC-RES-001 to UC-RES-008, UC-DEV-002, UC-SEC-003/004, and UC-NET-001 in the unsupported-capability catalogue. Use tagged Apple configuration where it already exists, guest cgroups or upstream runtime APIs where Docker semantics require them, and service-owned lifecycle state only where the Docker engine owns the behaviour. | One differential fixture per field family, exact `/info` capability flags, inside-guest enforcement probes, restart across service recovery, stop timeout/signal tests, and zero cleanup differences |

### Partial and unverified Development Containers properties

These rows are not all confirmed defects, but each is a blocker to a 100% claim because there is no release-bound proof.

| Surface | Design and evidence needed |
| --- | --- |
| Private registries | Credential-helper and registry-auth flows with bounded secret handling, pull/build/Feature coverage, failure redaction, and no credentials in evidence |
| Cross-architecture images | Explicit platform selection, Rosetta/emulation capability detection, execution proof, error behaviour, and performance reporting |
| Legacy `dockerFile` and root `context` | Run the official CLI's legacy merge path and compare the resulting build context, Dockerfile selection, labels, and cache behaviour |
| `build.cacheFrom` and arbitrary build options | Model the emitted Buildx/Docker requests strictly, prove cache import/export effects, and reject options the Apple builder cannot enforce |
| `updateRemoteUserUID: true` | Verify UID/GID rewrite, workspace ownership, existing user/group conflicts, rebuild, reuse, and named-volume effects |
| All `userEnvProbe` modes | Cover login, interactive, login-interactive, none, multiple shells, probe failure, timeout, and environment precedence |
| `shutdownAction` values | Add explicit `stopContainer`, `stopCompose`, and `none` journeys in CLI and VS Code, including reopen and service shutdown |
| `init` | Prove PID 1, signal forwarding, child reaping, exit status, and cleanup against Docker |
| Capabilities | Test representative adds/drops, invalid names, `ALL`, default capability differences, and Feature-contributed requirements |
| Feature ordering and metadata | Cover `overrideFeatureInstallOrder`, dependency ordering, lifecycle contributions, mounts, security requirements, option values, and failure cleanup |
| Lifecycle command forms | Cover string, array, and object forms, parallel object commands, failure short-circuiting, reuse counts, and each `waitFor` value |
| Variables and image metadata merging | Generate precedence tests for every supported variable, `devcontainer.metadata`, image labels, Feature metadata, and rebuild/reuse identity |
| Declarative secrets | Verify file format, permissions, missing/optional values, redaction, lifecycle exposure, rebuild, and cleanup without retaining secret material |
| Compose specification used by Dev Containers | Build a property-to-request matrix for build, deploy, devices, namespaces, security, resources, secrets, configs, logging, platform, profiles, dependencies, health, and multi-file merges; certify each used combination or block it explicitly |

## Performance and scalability optimisations

### OPT-001: Complete the typed Apple client fast path

**Current work:** the integrated PR 10 implementation reuses official clients,
uses typed stock inventory and inspection, retains enhanced-distribution CLI
inventory, and uses direct APIs for networks, archives, and managed host files.
Managed-host cache entries are invalidated whenever bootstrap recreates the
guest filesystem state.

**Design:** keep the CLI only for operations without an equivalent stable API. Cache immutable or incarnation-bound data, never mutable runtime truth. Invalidate caches on every in-process mutation and retain a low-frequency poll only for external changes.

**Acceptance:** exact-head full parity, five cold and ten warm runs, phase and resource measurements, and no regression in external-change detection or stock tagged-runtime support.

### OPT-002: Remove repeated Compose model and process startup work

**Evidence:** C01, C02, and C04 expose repeated Compose configuration, provider launch, model load, discovery, and lifecycle costs. A lower-layer in-memory client reuse experiment was neutral because every Compose command is a new process.

**Design:**

1. Cache only immutable normalised Compose model data keyed by the ordered file set, contents, environment inputs, profiles, project directory, CLI/provider version, and configuration hash.
2. Store the cache in the owning `container-compose` process or an explicit versioned on-disk cache, not in the runtime-neutral core.
3. Invalidate on any input or provider fingerprint change.
4. Parallelise independent image and service preparation while preserving dependency and health order.

**Acceptance:** C01/C02/C04 phase traces show fewer model loads and process starts, with exact Docker ordering, health, error, and cleanup observations.

### OPT-003: Replace fixed readiness polling with mutation wakeups and runtime events

**Evidence:** the current adapter polls external state at 200 ms and several lifecycle paths contain fixed 100 ms or 200 ms waits.

**Design:** wake reconciliation immediately after owned mutations, subscribe to stable native events where available, retain bounded polling for external writers, and use monotonic deadlines with jitter only for genuine retryable external state.

**Acceptance:** start/event wait phases improve without missed external changes, duplicate events, busy polling, or changed event order.

### OPT-004: Stream build, image, archive, and log data end to end

**Evidence:** HTTP requests are fully buffered, build contexts are passed as `Data`, command output is often accumulated in `Data`, and generic HTTP streams do not apply backpressure.

**Design:** use bounded asynchronous byte streams or private file descriptors from NIO through validation and native transfer; validate tar metadata incrementally; retain bounded diagnostic tails rather than entire output where the Docker protocol permits streaming.

**Acceptance:** large Feature and Dockerfile builds reduce copies and peak RSS, slow readers remain bounded, and byte-exact archive and progress fixtures remain equal.

### OPT-005: Add phase, resource, and round-trip measurement

**Design:** record monotonic spans for decode, state lookup, native inventory, create, start, event wait, exec/user probe, Compose model, archive, cleanup, and response drain. Count CLI launches and typed API calls. Capture process CPU, peak RSS, bytes, and cache hits locally in parity artefacts.

**Acceptance:** every optimisation names the phase it changes and retains raw measurements. Aggregate results cannot hide a slower high-frequency phase or a memory regression.

### OPT-006: Preserve paths already close to Docker

Engine negotiation and several build/resource scenarios are already close to or faster than Docker in repeated evidence. Changes to those paths require profiling evidence and the same non-regression gate. Ratio alone is not enough to prioritise a sub-second fixture.

## Test, quality, and governance backlog

### TEST-001: Fixture coverage is not derived from the specification

**Evidence:** the manifest has 18 CLI fixtures and one VS Code fixture, while the conformance ledger contains multiple partial and unverified properties. There is no scheduled schema-drift comparison.

**Solution design:** generate a versioned coverage map from the pinned Dev Containers schema, reference documentation, CLI-emitted Docker requests, and Compose property surface. Fail validation if any property lacks a certified fixture, an explicit blocker, and an owner. A scheduled job should report upstream additions without changing pins automatically.

**Acceptance:** 100% of schema and lifecycle rows map to evidence or an open blocker, and an injected schema property makes the coverage job fail.

### TEST-002: One VS Code workspace is too narrow for a full claim

**Solution design:** add minimal, independent real VS Code workspaces for image, Dockerfile, Feature, single-container lifecycle, Compose dependencies, rebuild/reuse, cancellation, failed create, and recovery. Keep them small enough to diagnose and retain exact extension/CLI provenance.

**Acceptance:** every user journey passes on Docker, stock Apple, and the provider with equivalent remote identity, workspace, ports, lifecycle counts, errors, and cleanup.

### TEST-003: Some public images and Features are tag-pinned rather than digest-pinned

**Evidence:** `TESTING.md` already records that upstream content can drift between rebuilds.

**Solution design:** resolve every image, Feature, extension, CLI package, and tool to an immutable digest or verified archive hash. Record both the human reference and immutable identity in the manifest and lane evidence.

**Acceptance:** release validation fails if a mutable reference has no checked immutable identity or resolves to a different payload.

### TEST-004: The parser and protocol boundary have no fuzzing programme

**Evidence:** OpenSSF code-scanning alert 7 reports no fuzzer integration. The highest-risk inputs are HTTP framing, JSON DTOs, query filters, Docker progress streams, tar/PAX archives, labels, Compose arguments, and SQLite diagnostic decoding.

**Solution design:** add deterministic Swift fuzz/property tests and a continuous fuzz target for pure parsers. Seed corpora from real Docker/CLI traffic and every prior regression. Enforce memory, time, and crash bounds.

**Acceptance:** CI runs a short deterministic corpus; scheduled or hosted fuzzing retains crashes and minimised reproducers; malformed inputs never crash, hang, escape archive roots, or bypass strict decoding.

### TEST-005: Low DTO coverage hides the highest semantic risk

**Evidence:** overall first-party coverage is above 91%, but `DockerDTOs.swift` is 24.53%. Several large files exceed 850 lines, including router, server, state, and DTO boundaries.

**Solution design:** split DTOs by endpoint and API object, add exact coding-key/default/unknown-key tests, and separate router endpoint families into reviewable modules. Do not chase coverage with trivial getters; focus on wire shapes and rejection behaviour.

**Acceptance:** every advertised request/response object has round-trip, omission/null, malformed, unknown-key, and Docker-oracle tests. DTO behavioural coverage exceeds 80% and the project remains above its 90% overall gate.

### GOV-002: OpenSSF review and project-age findings need explicit treatment

**Evidence:** live code scanning contains four Scorecard alerts: no fuzzing, no approved changesets in the sampled history, a repository younger than 90 days, and no detected OpenSSF Best Practices badge effort.

**Solution design:** resolve fuzzing through TEST-004; require an independent approving review for release-facing changes when another maintainer is available; treat the age alert as temporal evidence rather than suppressing it; and either pursue the badge or document why it does not improve the current release assurance.

**Acceptance:** actionable alerts close through evidence, not dismissal. Any inapplicable or time-based alert has a dated review record and does not conceal product risk.

### DOC-001: Several design and testing claims exceed the production implementation

**Evidence:** `DESIGN.md` says every request carries a lease, deadline, fingerprint, and idempotency key; describes production keyed locks and startup reconciliation; and claims local metrics and privacy-redacted structured logs. `TESTING.md` states that these behaviours are test objectives. The reviewed production wiring does not implement them. `TESTING.md` also reports 127 Swift tests while the reviewed suite ran 148.

**Solution design:** treat architecture statements as either implemented facts or clearly marked target design. Add document assertions tied to executable feature flags/tests where practical. Update counts from generated evidence instead of manual prose.

**Acceptance:** no present-tense implementation claim lacks a production call path and boundary test; generated documentation reports the exact reviewed commit and test evidence.

## Adjacent repository findings

The runtime-neutral core must remain independent from `ComposeCore`. Cross-repository work belongs to the component that owns the behaviour.

| Repository | Current finding | Project action |
| --- | --- | --- |
| `devcontainer` | Draft PR 10 contains the main current runtime-round-trip optimisation | Review and certify the exact final head; do not count it as `main` until merged and re-run |
| `container-compose` | Open PR 173 fixes inherited OCI `VOLUME` metadata for Compose commit | Track as provider quality work; it is not a Dev Containers release blocker unless a certified workflow consumes Compose commit |
| `container-compose` | Issue 156 documents a proven process-group cancellation design | Reuse the design in this repository through its own narrow process supervisor; do not import `ComposeCore` |
| `apple/container` | Stock 1.1.0 lacks several primitives needed by NC-002 to NC-009, while later fork/main work contains related capabilities | Produce small upstream-ready changes, consume only tagged upstream releases in the stock lane, and keep enhanced provider provenance separate |
| `apple/containerization` | Guest/runtime primitives may be needed for archive, device, namespace, and process correctness | Keep each generic correction independently testable and upstream-shaped; never hide a missing primitive in the bridge |

## Delivery sequence

1. **Stop silent loss:** PAR-001, PAR-002, ENG-004, PAR-004, and exact negative fixtures.
2. **Make ownership real:** ARC-001, ARC-002, ENG-002, PROC-001, schema migrations, and crash recovery.
3. **Bound the service:** ENG-001, ENG-003, state retention, privacy-redacted observability, and fuzzing.
4. **Close the specification:** implement and certify NC-002 to NC-009, then every partial and unverified property.
5. **Broaden real consumers:** schema-derived fixtures and the multi-workspace VS Code matrix.
6. **Optimise measured hot paths:** complete typed Apple APIs, event wakeups, Compose model work, and end-to-end streaming.
7. **Certify the north star:** zero semantic differences across the complete mapped surface and the comparable/better performance objectives above.

No phase may trade away functional parity for speed. A performance change that alters semantics, cleanup, errors, event order, or failure behaviour is a regression.

## Full-parity release exit criteria

- [ ] Every pinned Development Containers property and lifecycle rule is certified in the machine-readable coverage map.
- [ ] Every Docker request member emitted by the official client is translated or explicitly rejected before side effects.
- [ ] NC-001 to NC-009 are closed with real Docker and Apple evidence.
- [ ] All partial and unverified conformance rows are certified or remain explicit blockers to a full-parity release.
- [ ] Multiple real VS Code journeys pass in all three lanes.
- [ ] Crash, cancellation, restart, concurrent mutation, and cleanup tests prove no leaked work or resources.
- [ ] Request and response streaming remains within explicit memory and queue bounds.
- [ ] All test inputs and component fingerprints are immutable and reproducible.
- [ ] Required merge and release gates match live repository protection.
- [ ] Warm CLI aggregate and real VS Code medians meet the comparable objective, with p90 and resource-use evidence.
- [ ] The exact final commit, runtime stack, evidence artefacts, documentation, package, and Homebrew formula are aligned.
