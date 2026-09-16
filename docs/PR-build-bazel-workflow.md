# Pull request: native Bazel qualification

## Motivation

Establish a small executable proof of the approved build-workflow redesign before replacing production CI and release paths. Tracked against [the workflow issue](ISSUE-build-bazel-workflow.md).

## Implementation

- Pin verified Bazel tooling and native Swift/SQLite targets with declared generated version inputs.
- Enroll and validate SSD storage; isolate download/cache/output/test scratch paths.
- Adapt upstream test-source/XML writes for external-volume sandbox compatibility and export/merge native Swift coverage from the original test execution.
- Add version-generator and launcher regressions, evidence validation and opt-in Make targets.

## Validation and compatibility

See the dated [qualification evidence](bazel-workflow.md#qualification-evidence). Both test frameworks, source-derived coverage, warm-cache reuse and focused ASan/TSan runs are exercised. Existing product build, package manifests, optional Compose provider, runtime parity, release workflows and protected checks remain unchanged. No release is produced by this change.

## Remaining risks

The subset does not qualify the external Apple/Compose dependency graph, full product coverage, host-runtime performance, leak harness or recovery/publication protocol. Foundation temporary-directory use requires a wider audit. Automated retained-evidence promotion and cleanup are not implemented. Keep this opt-in until the remaining gates in the implementation document are complete.
