# Parity timing analysis

<!-- markdownlint-disable MD013 -->

This report analyzes three complete, successful parity runs surrounding the 1.0.0 release. It compares every CLI and real VS Code fixture against the corresponding real-Docker oracle, identifies stable overhead, and defines evidence-backed optimization targets.

## Result

All 162 CLI fixture-lane executions and all nine real VS Code lane executions completed successfully with zero normalized semantic differences and zero performance failures. No candidate fixture timed out or reached the `10x` Docker failure threshold.

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

The result is functionally passing and performance-passing. It is not exact timing parity.

## Evidence set

| Run | Commit | Relationship to 1.0.0 | Evidence |
| --- | --- | --- | --- |
| `30397969752` | `dacfacdc8c15fb745d7ed97d4e95e8dce378560a` | Final candidate with the same runtime source; later release-commit changes were documentation only | [Runtime parity run](https://github.com/stephenlclarke/devcontainer/actions/runs/30397969752) |
| `30399495867` | `a0200ecb3f642d4af9f0dbc7676f710d08f8bc1b` | Exact `1.0.0` tag commit | [1.0.0 runtime parity run](https://github.com/stephenlclarke/devcontainer/actions/runs/30399495867) |
| `30428273577` | `4963496d4732f68b3e98dcf4bf691170ade28c26` | Post-release validation; intervening source changes affected packaging/CI, not runtime behavior | [Post-release runtime parity run](https://github.com/stephenlclarke/devcontainer/actions/runs/30428273577) |

Every run used the same release runtime fingerprints: real Docker Engine 29.2.1, Docker CLI 29.6.2, Docker Compose 5.3.1, official `@devcontainers/cli` 0.88.0, stock Apple `container` 1.1.0, `container-compose` 0.10.1 with its exact matched custom runtime, VS Code 1.130.0, and Dev Containers extension 0.467.0.

The analysis uses downloaded `parity-comparison`, Docker, stock, and provider artifacts. Each fixture duration is monotonic wall time stored in lane JSON and JUnit. The comparison JSON is the authoritative source for ratios and pass/fail policy.

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

Across all three runs, the largest observed stock ratio was 4.314x on E06 and the largest observed provider ratio was 4.509x on D03. Both completed well below the 10x failure boundary.

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

- Raising the 10x threshold: no evidence requires it.
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
  cleanup, timeout, or performance failure.

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
single provider ratio was C01 at 4.618x (7.661s versus 1.659s), still well below
the 10x failure threshold and an absolute difference of 6.002s. The run
therefore confirms the race fix without changing the optimization priorities
derived from the three-run matrix.

## Optimization measurement protocol

For each candidate change:

1. keep the Docker oracle, runtime versions, fixture sources, runner, power state, and cleanup policy fixed;
2. run at least five cold iterations and ten warm iterations per affected fixture and lane;
3. retain raw monotonic durations, candidate/Docker ratios, fingerprints, and phase spans;
4. report median, minimum, maximum, p90, and median absolute overhead;
5. compare the candidate with a same-host baseline interleaved closely enough to limit thermal/background drift;
6. require zero semantic differences and zero cleanup differences;
7. treat a timeout, non-completion, or `>=10x` Docker duration as a failure;
8. treat smaller slowdowns as informational, not a failure;
9. accept an optimization only when the median improvement exceeds ordinary baseline variation and does not worsen the p90 materially;
10. rerun the complete three-lane CLI and VS Code matrix before publication.

The repository already records durations in machine-readable JSON, JUnit, and the human comparison matrix. Future phase instrumentation should supplement those artifacts rather than replace the end-to-end metric.

## Current priority order

1. Provider VS Code first attach.
2. Stock E06 network/volume request sequence.
3. Stock/provider C04 Compose lifecycle.
4. Shared fixed create/start/event overhead in short fixtures.
5. D07 reuse/rebuild/cleanup.
6. Re-measure D03 provider variability before changing it.

This order balances user-visible seconds, repeatability, and ownership. It does not change the performance pass policy.
