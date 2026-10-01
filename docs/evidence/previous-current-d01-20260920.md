# Previous/current runtime observations

These are retained observations, not a quiet benchmark or new stable-release qualification. A failed baseline is a functional difference, never a speedup. Campaign lists identify samples, not recorded execution order. Quiet paired sampling is not certified.

| Fixture | Role | Product | Samples | Eligible | Median s | P95 s |
| --- | --- | --- | ---: | --- | ---: | ---: |
| D01-image-config | baseline | 1.0.1 (published-release) | 1 | false | not compared | not compared |
| D01-image-config | target | fbcb3f68a89f (local-candidate) | 1 | true | 3.191203166 | 3.191203166 |

## D01-image-config

| Role | Campaign | Status | Setup s | Operation s | Cleanup s | Cleanup |
| --- | --- | --- | ---: | ---: | ---: | --- |
| baseline | published-baseline-d01-20260920a | failed | 6.822899958 | 2.081057333 | 0.044811042 | unknown |
| target | current-stock-d01-20260920b | passed | 6.809036125 | 3.191203166 | 1.524288666 | passed |

Exact product digests, source commits, runtime/harness fingerprints, all samples and evidence seals are in the JSON report.
