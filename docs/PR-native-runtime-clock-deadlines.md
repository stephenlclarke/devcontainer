# Pull request handoff: fix(runtime): use direct clocks for native delays

## Change

Replace the seven remaining production `Task.sleep(for:)` calls in `DevContainerAppleRuntime` with local `ContinuousClock.sleep(until:)` calls. Each deadline is computed immediately before its existing sleep, within the same task or loop, using the unchanged duration. All throwing/ignored-error handling, loop limits, output draining, registration fences, ownership checks and cancellation paths remain intact. The existing nanosecond sleep and dependency/library code remain unchanged. The same workaround covers 37 runtime-test helper sleeps with unchanged assertions and durations. README, design, compatibility and DocC describe the narrow compiler workaround.

## Evidence and validation

The [issue handoff](ISSUE-native-runtime-clock-deadlines.md) records the exact signed 095 E06 crash, diagnostic SHA256 and allocator frame. It is actual RED evidence; no fake or anonymous-test RED is claimed. The earlier SDK timeout and I/O drain fixes establish the same workaround pattern, not proof that every remaining site is individually reproducible.

Optimized stock/release invocation `5b204c53-5eb4-4ce0-b95b-8326b1db51ea` passes all 319 runtime tests. Whole-source SwiftFormat and strict SwiftLint pass. The preceding optimized run failed in a deferred-session test helper; the issue records its diagnostic and the test-only correction. Fresh signed E06 and full release qualification remain required; these unit results do not establish stable-release readiness.

## Archive polling coverage regression

The 455 hosted coverage stage found only the new archive polling delay uncovered (6/7 changed lines). Add one public archive-download regression using a private fake mode that returns each old native state once after start/stop, then transitions. The test checks real returned archive content and stopped-incarnation restoration and proves transfer ordering across both nonmatching inspections. Existing fake modes and production code remain unchanged. Optimized stock/release coverage invocation `d3d814fd-6894-4a38-a90e-95b8727bc212` passes all 320 runtime cases. The authenticated LCOV records two hits on the archive polling delay; collector diagnostics are clean. Full aggregate and changed-line release coverage remain separate gates.

The new regression remains in the existing serialized runtime suite through an extension, keeping the suite body within the repository layout limit. Whole-source SwiftFormat and strict SwiftLint pass; its assertions and dedicated fake behavior remain unchanged.
