# PR: retain a bounded first-party Docker frontend transport

## Motivation and implementation

The enhanced dependency lacks the duplex transport declarations required by the first-party frontend. Four internal transport files adapt the stock API implementation at `40436017e1e93012b8dab7cfc3c79783538065c3`; original Apache-2.0 attribution remains in the sources and `NOTICE.md`. The frontend routes ordinary metadata requests, event/build streams and attach/exec upgrades through this compatibility client. Shared request, response and ordinary error types remain unchanged; the absolute-deadline case uses a local error because Enhanced API 48 lacks that enum case.

Cancellation shuts down the owned socket to interrupt blocked workers; worker-owned closure and lock-protected duplicated descriptors prevent descriptor-reuse races. The duplex path preserves buffered output, independently serializes each direction and half-closes stdin. Ordinary metadata remains capped at 30 seconds and interactive sessions retain the existing finite ceiling. Three source-format corrections also address the exact-head hosted Validate failures without changing behaviour.

## Validation

Real Unix-socket regressions exercise malformed upgrades, bounded server errors, head-adjacent bytes, concurrent output/input, half-close, unsafe paths and permissions, incomplete-header cancellation, response cancellation, trickling absolute deadlines, delayed timer delivery and concurrent close. The complete native Docker-client target passes with both stock and enhanced prebuilt Engine API inputs; it does not depend on the Container SDK. Unused selected archive metadata is explicitly loaded before origin validation, with no source fallback or dependency rebuild. After adding framing and error-boundary regressions, focused stock coverage reaches 431/472 client lines (91.3%), 590/636 lines across the four new transport files (92.8%), and 1784/1853 Docker-client lines (96.3%). These are component results, not final aggregate release coverage. Independent integrated review found no socket ownership, deadline, framing or cleanup blocker. The expanded target passes all 99 test functions in 11 suites with both profiles, and origin verification succeeds. Failed draft, fixture-path and over-broad metadata-selection attempts remain retained as unqualified feedback.

## Compatibility and remaining gates

No package manifest, dependency revision, released asset, lower-layer producer or recipe changes. This source compatibility fix does not replace the final exact-main source quality, 90% coverage, signed/notarized package, live qualification or release publication gates. Historical failed compile evidence remains retained.

Linked issue: [transport compatibility](ISSUE-docker-frontend-transport.md). Integration review: [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
