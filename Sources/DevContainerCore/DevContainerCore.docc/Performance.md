# Performance

Three complete successful runs around the 1.0.0 release provide repeated timing
evidence for all 18 CLI fixtures and the real VS Code fixture.

## Aggregate result

| Lane | CLI mean | CLI ratio | VS Code mean | VS Code ratio |
| --- | ---: | ---: | ---: | ---: |
| Docker oracle | 81.080s | 1.000x | 38.477s | 1.000x |
| Stock Apple | 106.733s | 1.316x | 41.965s | 1.091x |
| `container-compose` provider | 116.573s | 1.438x | 51.931s | 1.350x |

Every run has zero semantic differences and zero performance failures. The
policy fails only a timeout/non-completion or a candidate duration at least
`10x` the matching Docker fixture.

## Measured hotspots

- Stock C04 Compose lifecycle: +5.882s mean.
- Stock E06 network and volume lifecycle: +5.468s mean.
- Provider C02 Compose dependencies: +5.701s mean.
- Provider C04 Compose lifecycle: +4.618s mean.
- Provider VS Code first attach: 26.329s versus Docker 15.983s.

The optimization priorities are provider first attach, repeated Apple runtime
process/inventory work in E06, repeated Compose model/lifecycle work in C04,
and shared create/start/event readiness overhead. The Feature build path is
already close to Docker and is not a priority.

No timing change between the release and post-release runs is attributed to an
optimization because the runtime source did not change.

After this analysis, a provider E03 duplex-transfer timeout exposed a
readiness-edge race in the direct process output monitor. Main replaces that
monitor with independent blocking stdout/stderr drains. Ten repeated provider
E03 sequences and a complete local provider lane pass. Acceptance remains
gated by the full hosted parity workflow, and the change does not alter the
historical matrix above.

The complete 19-row timing matrix, variability analysis, phase breakdown,
artifact links, and measurement protocol is maintained in
[PERFORMANCE.md](https://github.com/stephenlclarke/devcontainer/blob/main/PERFORMANCE.md).
