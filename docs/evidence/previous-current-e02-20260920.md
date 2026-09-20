# Previous/current runtime observations

These are retained observations, not a quiet benchmark or new stable-release qualification. A failed baseline is a functional difference, never a speedup. Campaign lists identify samples, not recorded execution order. Quiet paired sampling is not certified.

| Fixture | Role | Product | Samples | Eligible | Median s | P95 s |
| --- | --- | --- | ---: | --- | ---: | ---: |
| E02-container-lifecycle | baseline | 1.0.1 (published-release) | 1 | false | not compared | not compared |
| E02-container-lifecycle | target | fbcb3f68a89f (local-candidate) | 1 | true | 4.001082208 | 4.001082208 |

## E02-container-lifecycle

| Role | Campaign | Status | Setup s | Operation s | Cleanup s | Cleanup |
| --- | --- | --- | ---: | ---: | ---: | --- |
| baseline | published-baseline-e02-20260920a | failed | 4.920918250 | 0.061195667 | 1.383059209 | passed |
| target | current-stock-e02-20260920b | passed | 4.892087125 | 4.001082208 | 1.398780833 | passed |

Exact product digests, source commits, runtime/harness fingerprints, all samples and evidence seals are in the JSON report.
