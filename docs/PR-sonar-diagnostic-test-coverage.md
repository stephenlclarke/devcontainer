# PR: cover init-I/O diagnostics without changing runtime behavior

## Implementation

The Apple runtime regression now exercises payload-free trace events for failed writes, input EOF request and completion, both output EOFs, process exit, and drain-timeout/completion snapshots. Assertions check the accumulated byte counts, pending writes, EOF state, exit code, and terminal events. The existing disabled-trace test continues to verify that pending-writer callbacks are not evaluated when tracing is off.

The released-builder test now reads the selected configuration once, copies it before mutation, and leaves only `verify_provider_configuration` inside the expected-exception context. Its effective-configuration rejection and unmodified-selection assertion remain unchanged.

## Validation and remaining gates

`Tools.testing.test_build_runtime` passes all 24 tests. The canonical stock Apple runtime suite contains 329 tests in 40 suites, including the new trace-state assertions. Acceptance requires its frozen-source enrolled-SSD invocation and a fresh exact-head PR analysis confirming both changed-line coverage and Sonar findings. The source-format and Markdown checks remain required. No production code, dependency pins, runtime evidence, or release gate changes.

Linked issue: [Sonar diagnostic test coverage](ISSUE-sonar-diagnostic-test-coverage.md). Review: [PR 88](https://github.com/stephenlclarke/devcontainer/pull/88).
