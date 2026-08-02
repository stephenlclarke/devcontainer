# Parity timing analysis

<!-- markdownlint-disable MD013 -->

This report analyzes three complete, successful parity runs surrounding the 1.0.0 release. It compares every CLI and real VS Code fixture against the corresponding real-Docker oracle, identifies stable overhead, and defines evidence-backed optimization targets.

## Objective

The project's performance goal is user-visible performance comparable to or better than Docker while preserving exact functional parity. The target is at most `1.00x` the matching Docker oracle. Any completed result above `2.50x` requires further investigation; that trigger is not a performance pass criterion and does not, by itself, change functional parity. A timeout, other non-completion, or missing or invalid timing evidence remains a hard evidence failure. [`PARITY-ROADMAP.md`](PARITY-ROADMAP.md) defines the aggregate, VS Code, per-fixture, tail-latency, resource-use, and regression objectives.

## Result

All 162 CLI fixture-lane executions and all nine real VS Code lane executions completed successfully with zero normalized semantic differences and complete timing evidence. No candidate fixture timed out. The historical results are interpreted below using the current objective and investigation trigger.

Across the 18 sequential CLI fixtures:

| Lane | Mean summed fixture time | Range across three runs | Ratio to Docker mean | Mean extra time |
| --- | ---: | ---: | ---: | ---: |
| Docker oracle | 81.080s | 80.660–81.574s | 1.000x | — |
| Stock Apple | 106.733s | 103.699–108.711s | 1.316x | 25.654s |
| `container-compose` provider | 116.573s | 112.452–118.792s | 1.438x | 35.494s |

For the real VS Code fixture:

| Lane | Mean wall time | Range | Ratio to Docker mean | Mean extra time |
| --- | ---: | ---: | ---: | ---: |
| Docker oracle | 38.477s | 35.640–41.713s | 1.000x | — |
| Stock Apple | 41.965s | 38.885–46.912s | 1.091x | 3.488s |
| `container-compose` provider | 51.931s | 49.910–55.081s | 1.350x | 13.454s |

The result is functionally passing. Both Apple lanes miss the comparable-or-better objective in the aggregate CLI and real VS Code means. The repeated-run means for stock C01 and E06, and provider C01, D03, D06, and E06, are above `2.50x` and require further investigation.

## Evidence set

| Run | Commit | Relationship to 1.0.0 | Evidence |
| --- | --- | --- | --- |
| `30397969752` | `dacfacdc8c15fb745d7ed97d4e95e8dce378560a` | Final candidate with the same runtime source; later release-commit changes were documentation only | [Runtime parity run](https://github.com/stephenlclarke/devcontainer/actions/runs/30397969752) |
| `30399495867` | `a0200ecb3f642d4af9f0dbc7676f710d08f8bc1b` | Exact `1.0.0` tag commit | [1.0.0 runtime parity run](https://github.com/stephenlclarke/devcontainer/actions/runs/30399495867) |
| `30428273577` | `4963496d4732f68b3e98dcf4bf691170ade28c26` | Post-release validation; intervening source changes affected packaging/CI, not runtime behavior | [Post-release runtime parity run](https://github.com/stephenlclarke/devcontainer/actions/runs/30428273577) |

Every run used the same release runtime fingerprints: real Docker Engine 29.2.1, Docker CLI 29.6.2, Docker Compose 5.3.1, official `@devcontainers/cli` 0.88.0, stock Apple `container` 1.1.0, `container-compose` 0.10.1 with its exact matched custom runtime, VS Code 1.130.0, and Dev Containers extension 0.467.0.

The analysis uses downloaded `parity-comparison`, Docker, stock, and provider artifacts. Each fixture duration is monotonic wall time stored in lane JSON and JUnit. The historical comparison JSON is the authoritative source for recorded ratios and the status produced at the time; this document applies the current performance objective and investigation policy.

## Method

For each fixture and lane:

1. take `durationSeconds` from each of the three successful lane artifacts;
2. calculate the arithmetic mean, minimum, and maximum;
3. calculate candidate/Docker as the ratio of the candidate mean to the Docker mean;
4. calculate candidate overhead as candidate mean minus Docker mean;
5. retain raw per-run values so variability is visible;
6. inspect official-CLI timestamped spans and fixture operations for the highest-overhead rows.

CLI fixture timing starts immediately before `devcontainer up` or the engine probe and includes the probe, additional semantic assertions, and cleanup. VS Code timing includes launch, extension activation, attach, integrated operations, rebuild, reopen, and cleanup. It is deliberately user-observable end-to-end time rather than a microbenchmark.

Three observations are enough to locate large repeated costs but not enough for a high-confidence micro-optimization benchmark. Optimization acceptance therefore requires more warm/cold repetitions and reports median plus a tail percentile.

## Complete three-run matrix

Ratios below are ratios of three-run means. Ranges show the minimum and maximum raw duration.

| Fixture | Docker mean (range) | Stock mean (range) | Stock/Docker | Provider mean (range) | Provider/Docker |
| --- | ---: | ---: | ---: | ---: | ---: |
| C01-compose-service | 1.712s (1.637–1.751) | 5.025s (3.957–7.035) | 2.935x | 4.394s (4.296–4.507) | 2.566x |
| C02-compose-dependencies | 14.389s (14.317–14.425) | 12.679s (12.521–12.767) | 0.881x | 20.089s (17.911–21.332) | 1.396x |
| C03-compose-resources | 11.694s (11.646–11.737) | 9.138s (9.101–9.183) | 0.781x | 9.670s (9.446–9.813) | 0.827x |
| C04-compose-lifecycle | 4.406s (4.337–4.448) | 10.289s (9.045–11.027) | 2.335x | 9.024s (7.780–11.180) | 2.048x |
| D01-image-config | 1.408s (1.378–1.440) | 2.892s (2.710–3.061) | 2.054x | 2.962s (2.894–3.029) | 2.104x |
| D02-dockerfile-config | 2.983s (2.573–3.680) | 4.615s (4.489–4.699) | 1.547x | 5.078s (4.989–5.169) | 1.703x |
| D03-users-environment | 1.684s (1.617–1.737) | 3.611s (3.578–3.637) | 2.144x | 5.194s (4.130–7.291) | 3.084x |
| D04-lifecycle-hooks | 1.515s (1.373–1.602) | 3.069s (3.046–3.107) | 2.026x | 3.229s (3.179–3.314) | 2.132x |
| D05-features | 24.758s (23.505–26.847) | 25.025s (24.759–25.526) | 1.011x | 26.147s (25.769–26.818) | 1.056x |
| D06-ports | 1.601s (1.528–1.646) | 3.347s (3.245–3.470) | 2.090x | 4.013s (3.645–4.678) | 2.506x |
| D07-reuse-cleanup | 3.030s (2.852–3.379) | 6.102s (6.057–6.135) | 2.014x | 6.540s (6.360–6.633) | 2.158x |
| E01-engine-negotiation | 0.069s (0.069–0.069) | 0.072s (0.071–0.073) | 1.048x | 0.074s (0.072–0.076) | 1.077x |
| E02-container-lifecycle | 1.272s (1.231–1.320) | 1.661s (1.641–1.695) | 1.306x | 1.766s (1.721–1.791) | 1.389x |
| E03-exec-streams | 1.524s (1.457–1.655) | 1.974s (1.932–2.003) | 1.296x | 2.201s (2.104–2.311) | 1.445x |
| E04-image-build | 2.811s (2.342–3.700) | 3.471s (3.397–3.549) | 1.235x | 4.353s (3.817–4.847) | 1.548x |
| E05-archive-copy | 1.346s (1.305–1.400) | 2.038s (1.965–2.151) | 1.513x | 2.246s (1.963–2.402) | 1.668x |
| E06-network-volume | 2.087s (1.977–2.280) | 7.554s (5.760–8.529) | 3.620x | 5.735s (5.468–6.111) | 2.748x |
| F01-fault-recovery | 2.791s (2.339–3.243) | 4.171s (3.671–4.573) | 1.494x | 3.858s (3.570–4.036) | 1.382x |
| V01-vscode-end-to-end | 38.477s (35.640–41.713) | 41.965s (38.885–46.912) | 1.091x | 51.931s (49.910–55.081) | 1.350x |

## Run-to-run stability

The summed 18-fixture CLI totals have low coefficient of variation:

| Lane | Population standard deviation | Coefficient of variation |
| --- | ---: | ---: |
| Docker oracle | 0.377s | 0.46% |
| Stock Apple | 2.178s | 2.04% |
| `container-compose` provider | 2.917s | 2.50% |

The VS Code fixture is naturally noisier:

| Lane | Population standard deviation | Coefficient of variation |
| --- | ---: | ---: |
| Docker oracle | 2.495s | 6.49% |
| Stock Apple | 3.533s | 8.42% |
| `container-compose` provider | 2.257s | 4.35% |

The largest per-fixture variability is stock C01 (28.3% coefficient of variation) and provider D03 (28.5%). Their single-run maximum ratios should not be treated as stable regressions. By contrast, the aggregate CLI totals and provider VS Code total are consistent enough to prioritize.

Across all three runs, the largest observed stock ratio was 4.314x on E06 and the largest observed provider ratio was 4.509x on D03. Both completed, and both exceed the current `2.50x` investigation trigger.

## Absolute overhead hotspots

Relative ratios can exaggerate short fixtures, so optimization priority uses absolute extra seconds as well.

### Stock Apple

| Fixture | Mean extra time | Mean ratio | Interpretation |
| --- | ---: | ---: | --- |
| C04-compose-lifecycle | +5.882s | 2.335x | Multiple stop/start/recreate/down operations expose per-VM lifecycle and readiness cost. |
| E06-network-volume | +5.468s | 3.620x | Two containers plus network/volume creation, DNS, mounts, persistence, inspect, and cleanup cause repeated Apple CLI/runtime round trips. |
| C01-compose-service | +3.313s | 2.935x | A short single-service case exposes fixed create/start/event/cleanup overhead; variability is high. |
| D07-reuse-cleanup | +3.072s | 2.014x | Reuse, forced rebuild, persistent volume checks, and cleanup repeat inspection and lifecycle work. |
| D03-users-environment | +1.926s | 2.144x | Short fixture exposes fixed container start and exec/user probing overhead. |

### container-compose provider

| Fixture | Mean extra time | Mean ratio | Interpretation |
| --- | ---: | ---: | --- |
| C02-compose-dependencies | +5.701s | 1.396x | Three services, health readiness, and provider model/runtime operations dominate absolute cost. |
| C04-compose-lifecycle | +4.618s | 2.048x | Repeated provider model load and native lifecycle operations. |
| E06-network-volume | +3.648s | 2.748x | Repeated engine-to-Apple operations across two containers and two resource types. |
| D03-users-environment | +3.510s | 3.084x | One run was an outlier; fixed create/start/user-probe overhead remains visible. |
| D07-reuse-cleanup | +3.509s | 2.158x | Reuse/rebuild/cleanup crosses the provider and bridge repeatedly. |

D05 Features is the longest CLI fixture in absolute terms, but it is already close to Docker: stock is 1.011x and provider is 1.056x. Its BuildKit/Feature installation workload is not a priority for Apple-specific optimization.

## VS Code phase analysis

Timestamped extension-driver events separate first attach, rebuild, and reopen:

| Phase | Docker mean | Stock mean | Provider mean |
| --- | ---: | ---: | ---: |
| Reopen in container to first attach | 15.983s | 16.456s | 26.329s |
| Rebuild command to rebuilt container | 5.816s | 8.034s | 7.720s |
| Reopen locally command to local window | 1.141s | 1.163s | 1.674s |

Stock first attach is only 0.473s slower than Docker on average. Its largest user-visible deficit is rebuild at +2.218s.

The provider’s dominant deficit is first attach at +10.346s. Rebuild adds +1.904s. Reopen is normally about 1.13s; its 1.674s mean is raised by one 2.770s observation. Provider optimization should therefore profile the first Compose model resolution/create/readiness/attach path before spending effort on reopen behavior.

## Evidence-backed optimization opportunities

### 1. Reduce repeated runtime process launches and inventory reads

E06 performs network create, volume create, two container starts, several execs, a one-shot persistence container, two inspect calls, and cleanup. The Apple adapter currently launches the `container` executable for runtime operations and often refreshes state through additional commands. The stable +5.468s stock overhead makes this the highest-value bridge target.

Investigate:

- operation-scoped reuse of already validated image/network/volume/container snapshots;
- eliminating duplicate list/inspect calls within one Docker request sequence;
- direct `ContainerClient` API calls where stock Apple 1.1.0 exposes an equivalent primitive;
- batching state reconciliation after a transaction rather than after each non-dependent operation;
- retaining strict invalidation after out-of-band runtime changes.

Acceptance: E06 median overhead should fall without changing any raw observation, event ordering, cleanup proof, or failure behavior.

### 2. Shorten create/start event readiness

Official-CLI spans in the post-release run show Docker start-event waits around 0.1–0.3s for short fixtures, while Apple lanes commonly spend about 0.7–2.3s in the corresponding resolve/start/event window. This fixed cost appears across C01, C03, C04, D01, D03, D04, D06, and D07.

Investigate:

- emitting the Docker `start` event immediately from the authoritative successful Apple start result while preserving event cursor ordering;
- avoiding a subsequent full inventory poll when the create/start response already contains enough identity;
- coalescing concurrent inspect/event subscribers;
- replacing fixed polling with runtime notifications where the public Apple API supplies them.

Acceptance: short-fixture median time improves and reconnectable event tests still prove no lost, duplicated, or reordered lifecycle event.

### 3. Optimize provider first attach

The provider’s VS Code first attach averages 26.329s versus 15.983s for Docker and 16.456s for stock. This accounts for most of its +13.454s total VS Code overhead.

Investigate in `container-compose` and its matched runtime:

- cache the resolved Compose model for the exact ordered file/environment/configuration hash;
- avoid repeated provider version/provenance probes within one attach;
- reuse image and project inventory already collected during `up`;
- parallelize independent image checks and service preparation while preserving dependency health ordering;
- record phase-level spans around model load, image resolution, VM creation, readiness, Dev Container injection, and VS Code server attach.

Any required change belongs in the owning `stephenlclarke/container-compose`, `stephenlclarke/container`, or `stephenlclarke/containerization` repository and must arrive through a focused pull request with regression coverage.

### 4. Reduce repeated Compose lifecycle model work

C04 repeatedly runs restart, force-recreate, discovery, down, and cleanup. Provider logs show a Compose model load for restart, recreate, down, and the final cleanup. Stock Docker Compose also repeats config/inspect/event operations.

Investigate:

- cache only immutable parsed model data under the existing project configuration hash;
- invalidate on ordered file, environment, profile, or provider change;
- skip the final duplicate down when the explicit lifecycle assertion already proved the project absent;
- preserve the cleanup proof through a cheap absence query rather than suppressing cleanup.

The last point may optimize the test harness rather than the product. Product and harness improvements must be reported separately.

### 5. Preserve the already-efficient paths

E01 engine negotiation is effectively equal (1.048x stock, 1.077x provider). D05 Features is near equal despite its long workload. C02 stock and C03 both Apple lanes are faster than Docker in this sample. These paths should not be rewritten without profiling evidence.

## Changes not justified by the data

- Raising the investigation trigger to avoid surfacing current results: no evidence justifies it.
- Adding retries: retries would hide hangs or regressions and violate the parity policy.
- Parallelizing the 18 top-level fixtures: that would distort per-fixture comparison and cause resource contention; use parallelism only inside a fixture when the product contract permits it.
- Removing cleanup or semantic probes: their time is intentionally part of the user-observable and leak-free contract.
- Claiming the post-release run is an optimization: no runtime source changed. Its different timing is ordinary host/run variation.
- Optimizing solely for the highest ratio: short fixtures need absolute-overhead analysis.

## Post-analysis E03 reliability fix

The documentation commit that published this analysis triggered
[runtime parity run `30432362538`](https://github.com/stephenlclarke/devcontainer/actions/runs/30432362538).
Docker and stock Apple passed, and 17 of 18 provider CLI fixtures passed. The
provider E03 4 MiB duplex exec transfer stopped making progress and was killed
at its 300-second deadline. Its 301.720s duration was a timeout failure, not an
acceptable slowdown and not a result that a successful retry could waive.

The direct Apple process output path still used dispatch-source readiness
events even though the CLI-backed process path already used dedicated blocking
drain threads to avoid missed readiness edges under duplex backpressure. Main
now uses independently blocking stdout and stderr drains for direct exec as
well. The regression test requires nonblocking ordered stdin, blocking output
drains, and byte-exact 4 MiB duplex transfer.

Pre-commit live validation after the change comprised:

- 10 consecutive provider E01-E03 sequences, all passing;
- E03 durations of 1.993-2.241s in nine runs and 5.326s in one run;
- one complete 18-fixture provider lane with E03 at 2.005s and no semantic,
  cleanup, timeout, or timing-evidence failure.

These observations validate the reliability fix; they do not replace the
historical three-run matrix above or claim a statistically significant speed
improvement.

The exact fix commit, [`74566c2`](https://github.com/stephenlclarke/devcontainer/commit/74566c2b22069369f23d55110f872f9ec427ea43),
then passed the complete
[hosted parity workflow](https://github.com/stephenlclarke/devcontainer/actions/runs/30435178597):

| Surface | Docker | Stock Apple | Provider |
| --- | ---: | ---: | ---: |
| CLI, 18-fixture total | 81.104s | 104.720s (1.291x) | 117.155s (1.445x) |
| Real VS Code V01 | 37.007s | 39.969s (1.080x) | 56.472s (1.526x) |
| E03 byte-exact duplex exec | 1.776s | 2.010s (1.132x) | 2.030s (1.143x) |

All semantic, cleanup, CLI, VS Code, and comparison gates passed. The largest
single provider ratio was C01 at 4.618x (7.661s versus 1.659s), an absolute
difference of 6.002s and a result requiring further investigation under the
current policy. The run confirms the race fix without changing the optimization
priorities derived from the three-run matrix.

## 2026-07-29 targeted current-worktree comparison

The uncommitted runtime optimizations were compared with pre-change
`d593111090d5aab72b0e432f91480268c3ee79c3` on this Mac. This is a focused,
three-run warm comparison, not a replacement for the release matrix or the
optimization acceptance protocol below.

The workload contains the four fixtures that exercise the changed paths:

- C04 Compose lifecycle;
- E02 container lifecycle;
- E06 network and volume inventory;
- F01 concurrent lifecycle and `/events` handling.

Each run used the same debug parity engine, pinned Dev Containers CLI, fixture
sources, and local Docker oracle. The Apple lane used the installed custom
Homebrew `container` distribution with `DEVCONTAINER_ALLOW_CUSTOM_STOCK=1`;
it must not be described as a measurement of an unmodified Apple release.

### Aggregate timings

Raw samples are total fixture wall-clock seconds for each four-fixture lane.
The median is the middle of three samples; ranges are included to show the
observed variation.

| Lane | Pre-change samples | Current samples | Pre-change median | Current median | Median change |
| --- | ---: | ---: | ---: | ---: | ---: |
| Custom Apple runtime | 26.236, 24.610, 25.042s | 25.450, 21.526, 23.440s | 25.042s | 23.440s | -1.602s (-6.4%) |
| `container-compose` provider | 26.426, 25.641, 23.844s | 22.449, 22.267, 21.780s | 25.641s | 22.267s | -3.374s (-13.2%) |

### Per-fixture timings

Samples are sorted ascending for compact range inspection. Per-fixture medians
do not sum to the aggregate median because each lane run has different
within-run timings.

| Lane | Fixture | Pre-change samples | Current samples | Pre-change median | Current median | Median change |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Custom Apple runtime | C04 Compose lifecycle | 8.690, 10.083, 12.869s | 8.515, 10.847, 12.608s | 10.083s | 10.847s | +0.764s (+7.6%) |
| Custom Apple runtime | E02 container lifecycle | 2.278, 2.345, 3.847s | 2.118, 2.147, 2.197s | 2.345s | 2.147s | -0.198s (-8.4%) |
| Custom Apple runtime | E06 network/volume | 6.345, 6.376, 9.490s | 6.153, 6.452, 6.462s | 6.376s | 6.452s | +0.076s (+1.2%) |
| Custom Apple runtime | F01 lifecycle/events | 4.085, 4.713, 4.767s | 3.984, 4.272, 4.661s | 4.713s | 4.272s | -0.441s (-9.4%) |
| `container-compose` provider | C04 Compose lifecycle | 10.135, 12.717, 13.153s | 8.644, 8.650, 9.043s | 12.717s | 8.650s | -4.067s (-32.0%) |
| `container-compose` provider | E02 container lifecycle | 2.185, 2.218, 2.306s | 2.161, 2.389, 2.752s | 2.218s | 2.389s | +0.171s (+7.7%) |
| `container-compose` provider | E06 network/volume | 6.129, 6.587, 6.671s | 6.085, 6.266, 6.952s | 6.587s | 6.266s | -0.321s (-4.9%) |
| `container-compose` provider | F01 lifecycle/events | 4.380, 4.577, 4.853s | 4.065, 4.703, 4.786s | 4.577s | 4.703s | +0.126s (+2.8%) |

The provider's aggregate improvement is principally the C04 lifecycle change.
The Apple aggregate improvement comes from E02 and F01; C04 and E06 did not
improve in this small sample. It is therefore evidence of a promising targeted
improvement, not proof that every optimized path is faster.

### Functional result and original measurement limits

For all three current runs, all four fixture observations matched the Docker
oracle with no fixture-level semantic differences. The current Docker oracle
also passed all three focused runs.

The original timing runs recorded a lane-level cleanup failure because the
adapter re-adopted Apple's stopped internal `buildkit` resource as a Docker
container after the isolated BuildKit builder was removed. That was a runtime
state-cleanup defect, not a difference that could be waived.

The correction excludes only resources carrying both reserved labels
`com.apple.container.resource.role=builder` and
`com.apple.container.plugin=builder` from the Docker inventory. It also prunes
any previously stored metadata for that resource. A live `buildx create`,
bootstrap, and removal probe confirmed that the builder remains available while
active, then leaves both `docker ps -a` and `runtime_containers` empty after
removal. The focused Apple and provider lanes subsequently passed with zero
cleanup differences. The original timing samples retain their recorded cleanup
status and should be refreshed before using them as release evidence.

The Docker-oracle blocker was subsequently corrected in the parity harness.
The Docker lane now selects the daemon-integrated default BuildKit, preserving
the daemon's trust store, rather than creating a Docker-container builder that
cannot verify the locally intercepted Docker Hub certificate. The complete
one-run matrix is recorded in the closeout section below. The earlier three
warm samples remain below the five-cold/ten-warm repetition requirement.

## Optimization measurement protocol

For each candidate change:

1. keep the Docker oracle, runtime versions, fixture sources, runner, power state, and cleanup policy fixed;
2. run at least five cold iterations and ten warm iterations per affected fixture and lane;
3. retain raw monotonic durations, candidate/Docker ratios, fingerprints, and phase spans;
4. report median, minimum, maximum, p90, and median absolute overhead;
5. compare the candidate with a same-host baseline interleaved closely enough to limit thermal/background drift;
6. require zero semantic differences and zero cleanup differences;
7. treat a timeout, other non-completion, or missing or invalid timing evidence as a hard failure;
8. mark every completed candidate above `2.50x` Docker for further investigation and every result above `1.00x` as missing the performance objective;
9. accept an optimization only when the median improvement exceeds ordinary baseline variation and does not worsen the p90 materially;
10. rerun the complete three-lane CLI and VS Code matrix before publication.

The repository already records durations in machine-readable JSON, JUnit, and the human comparison matrix. Future phase instrumentation should supplement those artifacts rather than replace the end-to-end metric.

## Priority order before the July 2026 E06 work

1. Provider VS Code first attach.
2. Stock E06 network/volume request sequence.
3. Stock/provider C04 Compose lifecycle.
4. Shared fixed create/start/event overhead in short fixtures.
5. D07 reuse/rebuild/cleanup.
6. Re-measure D03 provider variability before changing it.

This historical order selected the E06 work described below. It does not
change the performance pass policy.

## July 2026 Apple runtime optimization

The `perf/devcontainer-speedups` work implements the highest-priority stock
E06 optimization and the subsequently identified E05 archive-copy fast path
in the Devcontainer adapter itself. The final candidate:

- reuses one Apple `ContainerClient` and `NetworkClient` per engine process;
- uses Apple’s typed API for stock container inventory and exact inspection,
  while retaining the enhanced runtime's additive CLI inventory schema;
- uses distribution-safe direct clients for network operations and archive
  transfer;
- resolves Docker IDs through durable metadata before issuing a filtered
  runtime lookup;
- maps typed Apple snapshots directly instead of serializing them through an
  intermediate JSON object;
- wakes event polling immediately after authoritative in-process mutations
  while retaining the 200ms fallback for out-of-process changes;
- copies the managed `/etc/hosts` block through the direct API and skips the
  transfer when the container incarnation and desired block are unchanged;
- invalidates the managed-host cache after every runtime bootstrap, including
  stop/start and transient archive starts, and on recreation, removal, or
  archive upload.

Container stop, kill, and delete deliberately remain on the CLI path. A
candidate that moved those mutations to the direct API improved the median but
caused materially worse cleanup tails with the custom runtime, so it was
rejected.

### Same-host comparison

The final source was selected using real E06 parity probes on the same Mac,
with an unmodified `main` engine and candidate engine alternated or run under
the same consecutive-load pattern. Each probe created a real network, managed
volume, bind and tmpfs mounts, two long-running containers, a transient
auto-remove container, DNS traffic, exec traffic, persistence checks, inspect
calls, and complete cleanup.

| Runtime | Sample | Baseline median | Candidate median | Median improvement | Baseline mean | Candidate mean | Baseline p90 | Candidate p90 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Stock Apple `container` 1.1.0 | 10 + 10 sustained runs | 5.584s | 4.655s | 16.6% | 6.650s | 5.566s | 8.670s | 7.776s |
| Matched custom `container` used by `container-compose` 0.10.1 | 10 + 10 sustained runs | 5.697s | 4.691s | 17.7% | 5.674s | 4.976s | 5.788s | 5.231s |

All 40 observations passed their semantic and cleanup contracts. The
candidate improved the stock mean by 16.3% and p90 by 10.3%; it improved the
custom-runtime mean by 12.3% and p90 by 9.6%. Periodic VM cleanup tails remain
visible in both baseline and candidate samples, but the accepted candidate
does not worsen them.

These are focused local A/B measurements, not a replacement for the hosted
three-lane release matrix. Full Docker, stock Apple, custom-runtime, and real
VS Code parity remain publication gates.

### Final-source confirmation

After the client protocols were made injectable for deterministic unit tests,
the exact final source completed ten additional E06 runs per Apple runtime.
All 20 runs passed their semantic and cleanup contracts:

| Runtime | Sample | Median | Mean | p90 | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stock Apple `container` 1.1.0 | 10 | 4.307s | 5.188s | 7.355s | 7.381s |
| Matched custom `container` | 10 | 4.620s | 5.297s | 7.879s | 8.092s |

Most observations were close to the median. The slower observations coincided
with periodic VM cleanup and did not change semantics or leave resources.

The archive fast path removes two `container cp` process launches by calling
the same official typed copy API directly. Three focused E05 runs per runtime
all preserved content, executable mode, symbolic links, a 110-character path,
and a 1 MiB binary:

| Runtime | Sample | Median | Minimum | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Stock Apple `container` 1.1.0 | 3 | 1.664s | 1.576s | 1.808s |
| Matched custom `container` | 3 | 1.703s | 1.630s | 1.833s |

In the final complete matrix, E05 took 1.606s on stock and 1.700s on the
custom runtime, compared with Docker's 1.290s. The earlier custom-runtime
matrix observation before this fast path was 4.896s.

### Final acceptance matrix

The exact final source passed all 54 CLI fixture-lane executions with zero
normalized semantic or cleanup differences:

| Matrix | Docker | Stock Apple | Custom runtime |
| --- | ---: | ---: | ---: |
| 18-fixture total | 81.957s | 120.979s | 114.792s |
| E05 archive copy | 1.290s | 1.606s (1.245x) | 1.700s (1.318x) |
| E06 network and volume | 1.766s | 4.453s (2.522x) | 4.422s (2.504x) |

The stock total includes a 48.454s Feature build and a 12.449s Compose
lifecycle observation; neither path was changed by this work. Stock E06 at
`2.522x` and custom E06 at `2.504x` both exceed the current `2.50x`
investigation trigger. They remain functionally passing, miss the
comparable-or-better objective, and require phase-level investigation.

The authenticated real VS Code Dev Containers attach, command, rebuild,
reopen, and cleanup workflow also passed on every lane:

| Docker | Stock Apple | Custom runtime |
| ---: | ---: | ---: |
| 39.497s | 39.733s (1.006x) | 53.687s (1.359x) |

### Rejected lower-layer changes

An operation-static client-reuse change was also measured in
`container-compose`. It was neutral within ordinary variation because each
Compose CLI invocation is a new process. The change was removed. A 50ms event
polling experiment added four times as many inventory requests without a
stable improvement and was also removed.

The optimization therefore requires no source change in `container-compose`,
the custom `container` fork, or the custom `containerization` fork. Dedicated
`perf/devcontainer-speedups` branches exist in those repositories so later
Compose-stack work can be reconciled without colliding with concurrent
development, but they intentionally remain source-identical to their
respective main branches.

The remaining priority order is:

1. Provider VS Code first attach.
2. Stock/provider C04 Compose lifecycle.
3. D07 reuse/rebuild/cleanup.
4. Re-measure D03 provider variability before changing it.
5. Complete the E06 phase-level investigation because both final observations
   remain above `2.50x`; distinguish product-owned round trips from ordinary
   VM cleanup variance.

## 2026-07-30 integration closeout

This closeout ran on `ULTUK2M30000`: macOS 26.5.1 (`25F80`), Docker
Engine 29.5.2/API 1.54, Xcode 26.6, Swift 6.3.3, and arm64. The reference
manifest now binds release parity to that exact local host and Docker daemon.

The performance branch integration exposed and corrected seven additional
parity blockers:

- ordinary HTTP input half-close now allows an in-flight response to complete;
- a completed hijack releases both connection trackers, preventing rejection
  after 64 sequential exec or attach streams;
- the bounded request limit admits real Buildx image archives up to 512 MiB,
  with a separate 1 GiB aggregate retained-body budget;
- valid streaming tar archives are padded only after validation so macOS
  `tar` can extract them;
- the stock Apple native builder receives Feature content through a tar
  `ADD` staging context because its scratch-stage `COPY` dropped nested files;
- stock and provider BuildKit containers receive valid host resolver
  nameservers without claiming general Dev Container DNS-option support;
- auto-removed containers reconcile their durable resource row and release an
  empty non-Compose project claim.

The Docker reference harness also moved from an isolated Docker-container
builder to the daemon-integrated BuildKit after the isolated builder failed
Docker Hub certificate verification. That failure remains environment
evidence and its incomplete timings are excluded.

### Complete local CLI matrix

All 54 fixture-lane executions completed successfully with zero semantic and
cleanup differences:

| Lane | Summed time | Ratio to Docker |
| --- | ---: | ---: |
| Docker oracle | 163.599s | 1.000x |
| Stock Apple 1.1.0 | 158.780s | 0.971x |
| `container-compose` provider | 154.025s | 0.941x |

These aggregate ratios meet the `<=1.00x` objective in this one run, but they
are not an optimisation acceptance result. The lanes ran sequentially with
different cache state: Docker D05 took 98.015s, while stock and provider D05
took 39.252s and 42.074s. The repeated cold/warm protocol remains required.

The completed per-fixture results above `2.50x` Docker and therefore requiring
investigation are:

| Fixture | Stock/Docker | Provider/Docker | Immediate interpretation |
| --- | ---: | ---: | --- |
| C04 Compose lifecycle | 2.849x | 1.960x | Stock lifecycle round trips |
| D04 lifecycle hooks | 2.158x | 2.611x | Provider fixed overhead |
| D06 ports | 2.304x | 2.683x | Provider forwarding setup |
| D07 reuse and cleanup | 3.055x | 3.164x | Both lanes repeat inspect, rebuild, and cleanup work |
| E04 image build | 25.883x | 6.760x | Cache-sensitive Docker denominator; rerun cold and warm before attribution |

E06 completed at 2.305x stock and 2.392x provider, below the investigation
trigger in this run but still outside the `<=1.00x` objective. Every other
completed result above `1.00x` also misses the objective even when it does not
trigger the `2.50x` investigation rule.

That intermediate matrix certified the corrected worktree's CLI behaviour
only; it did not itself satisfy the hosted merge gate. The five-cold/ten-warm
protocol remains required before claiming a performance improvement.

### Final-source local validation

After the remaining real VS Code and provider-provenance fixes, the complete candidate was rerun locally on the same `ULTUK2M30000` host. The directly used Homebrew tooling was current for this run: Docker CLI 29.6.2, BuildKit 0.32.0, and Docker Buildx 0.36.0. The stock lane used unmodified Apple `container` 1.1.0. The provider lane used the stable `container-compose` 0.10.1 stack through exact `/opt/homebrew/opt` paths rather than the independently installed `current` lane.

The final-source changes add Docker `ConsoleSize` handling, PTY-backed
interactive exec and resize, deterministic exec completion state, effective
native user/environment metadata, a shell-first stock Docker wrapper that
remains compatible with VS Code's `node-pty`, and exact provider runtime
selection through `CONTAINER_COMPOSE_CONTAINER`.

The first exact-source rerun exposed a further intermittent E03 failure. Stock
Apple's 4 MiB duplex `cat` exec timed out after 302.539s, and focused stress
reproduced the stall through both the CLI-owned and typed process paths. Both
paths closed a pipe to signal EOF, which depends on every copied descriptor
being closed. Apple process descriptor transfer can retain a copy, leaving the
guest blocked after all input bytes have been written.

Non-terminal direct exec now uses a Unix socket pair for standard input and
signals EOF with `shutdown(SHUT_WR)`. A socket half-close is visible to the
guest even while a copied host descriptor remains open. Terminal exec remains
on the PTY-backed CLI path. A focused regression test retains a descriptor
copy and proves that the peer still receives EOF. Ten consecutive live stock
E03 runs then passed byte-exactly in 3.189–3.593s before the complete matrix
below.

All 54 CLI fixture-lane executions again passed with zero semantic and cleanup differences:

| Lane | Summed time | Ratio to Docker | Performance result |
| --- | ---: | ---: | --- |
| Docker oracle | 76.807s | 1.000x | Reference |
| Stock Apple 1.1.0 | 168.097s | 2.189x | Objective missed |
| `container-compose` provider | 385.353s | 5.017x | Objective missed |

The 14 CLI fixtures with at least one completed result above the `2.50x`
investigation trigger were:

| Fixture | Stock/Docker | Provider/Docker |
| --- | ---: | ---: |
| C01 Compose service | 4.590x | 7.104x |
| C04 Compose lifecycle | 3.968x | 4.801x |
| D01 image configuration | 3.252x | 5.953x |
| D02 Dockerfile configuration | 3.033x | 8.254x |
| D03 users and environment | 3.103x | 16.356x |
| D04 lifecycle hooks | 2.534x | 7.446x |
| D05 Features | 1.318x | 8.887x |
| D06 ports | 3.464x | 8.775x |
| D07 reuse and cleanup | 3.329x | 8.134x |
| E01 engine negotiation | 2.670x | 1.011x |
| E03 exec streams | 1.210x | 3.911x |
| E04 image build | 24.138x | 18.351x |
| E06 network and volume | 2.829x | 5.874x |
| F01 fault recovery | 1.688x | 2.831x |

The real VS Code open, attach, server, lifecycle, terminal, port-forward, rebuild, reopen, and cleanup journey passed in all three lanes:

| Lane | End-to-end time | Ratio to Docker | Performance result |
| --- | ---: | ---: | --- |
| Docker oracle | 40.379s | 1.000x | Reference |
| Stock Apple 1.1.0 | 55.053s | 1.363x | Objective missed |
| `container-compose` provider | 110.625s | 2.740x | Investigate |

The provider VS Code result crossed the `2.50x` investigation trigger. Both
candidate lanes missed the comparable-or-better objective.

These are final-source functional and raw timing results, not performance
certification. The lanes ran sequentially with existing caches: Docker E04
completed in 0.564s, making its ratios unsuitable for attribution, while the
provider D05 path took 98.899s. The five-cold/ten-warm paired protocol remains
required before accepting or rejecting an optimisation. The hosted workflow
independently requires the same exact candidate to retain complete CLI and real
VS Code evidence.
