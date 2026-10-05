# Pull request handoff: fix(runtime): use direct clocks for native delays

## Change

Replace the seven remaining production `Task.sleep(for:)` calls in `DevContainerAppleRuntime` with local `ContinuousClock.sleep(until:)` calls. Each deadline is computed immediately before its existing sleep, within the same task or loop, using the unchanged duration. All throwing/ignored-error handling, loop limits, output draining, registration fences, ownership checks and cancellation paths remain intact. The existing nanosecond sleep and dependency/library code remain unchanged. The same workaround covers 37 runtime-test helper sleeps with unchanged assertions and durations. README, design, compatibility and DocC describe the narrow compiler workaround.

## Evidence and validation

The [issue handoff](ISSUE-native-runtime-clock-deadlines.md) records the exact signed 095 E06 crash, diagnostic SHA256 and allocator frame. It is actual RED evidence; no fake or anonymous-test RED is claimed. The earlier SDK timeout and I/O drain fixes establish the same workaround pattern, not proof that every remaining site is individually reproducible.

Optimized stock/release invocation `5b204c53-5eb4-4ce0-b95b-8326b1db51ea` passes all 319 runtime tests. Whole-source SwiftFormat and strict SwiftLint pass. The preceding optimized run failed in a deferred-session test helper; the issue records its diagnostic and the test-only correction. Fresh signed E06 and full release qualification remain required; these unit results do not establish stable-release readiness.
