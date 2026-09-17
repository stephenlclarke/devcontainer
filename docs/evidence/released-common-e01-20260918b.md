# Runtime campaign evidence

Campaign: `released-common-e01-20260918b`.

Functional result applies only to the explicitly requested fixtures below. This is not full-release qualification or a quiet paired benchmark. Raw ratios of 10x or more require investigation; they are not waived or certified as performance passes.

| Fixture | Lane | Status | Setup s | Operation s | Cleanup s | Operation / Docker | Cleanup |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| E01-engine-negotiation | apple-stock | passed | 5.538767416 | 0.121125167 | 0.847610042 | 12.302x | passed |
| E01-engine-negotiation | container-compose | passed | 5.548434875 | 2.034307541 | 0.862147000 | 206.606x | passed |
| E01-engine-negotiation | docker | passed | 14.960515958 | 0.009846333 | 2.754892834 | 1.000x | passed |

Functional parity: true.

Unrequested fixtures: C01-compose-service, C02-compose-dependencies, C03-compose-resources, C04-compose-lifecycle, D01-image-config, D02-dockerfile-config, D03-users-environment, D04-lifecycle-hooks, D05-features, D06-ports, D07-reuse-cleanup, E02-container-lifecycle, E03-exec-streams, E04-image-build, E05-archive-copy, E06-network-volume, F01-fault-recovery, V01-vscode-end-to-end.

The accompanying JSON binds each case to its exact harness, contract, release set, runtime and retained evidence seal.
