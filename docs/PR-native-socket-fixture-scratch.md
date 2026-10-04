# Pull request handoff: select short SSD socket fixtures

## Changes

Three positive native socket lifecycle fixtures explicitly select a short parent independently of ambient `TMPDIR`. This Mac defaults to `/Volumes/SSD/q` and rejects internal overrides; `DEVCONTAINER_TEST_SCRATCH_ROOT` provides an explicit alternative SSD parent. Hosted CI without the enrolled SSD and non-macOS hosts use `/tmp`.

## Validation

The parity tool suite passes all 211 tests with the actual long macOS temporary environment. Positive fixtures select the canonical enrolled SSD parent. The production 103-byte limit and negative oversized-path tests are unchanged.

## Compatibility and risks

This is a unit-fixture correction only. It does not establish live parity or close the broader transient-storage findings. A local missing SSD or invalid override fails rather than selecting an internal fallback.

Related to [the matching issue](ISSUE-native-socket-fixture-scratch.md).
