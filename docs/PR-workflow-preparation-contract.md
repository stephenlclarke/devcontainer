# PR: verify current SwiftPM preparation target boundaries

## Implementation

The stock-Apple workflow regression explicitly requires `swift-test: swiftpm-prepare` and `coverage: swiftpm-prepare-coverage`, then uses those headers to select the test recipe. Existing strict compiler, stock lock, single-attempt execution and runner assertions remain unchanged. The test now rejects missing preparation instead of assuming targets have no prerequisites.

## Validation and remaining gates

All 29 focused workflow-artifact tests pass, including the previously failing stock-Apple case; paired Markdown lint and diff checks pass. No production code, Makefile, lock, lower-layer recipe or runtime input changes. Fresh exact-head GitHub validation remains required; final merged-main qualification and publication are pending.

Linked issue: [workflow preparation contract](ISSUE-workflow-preparation-contract.md). Review: [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
