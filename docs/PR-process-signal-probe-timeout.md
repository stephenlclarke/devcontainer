# Pull request handoff: bound inherited-signal test observation

## Change

The signal-relay regression probe replaces unbounded wrapper waiting and pipe EOF reads with finite process-exit polling and nonblocking drains. Cancellation during marker or wrapper-exit observation now runs bounded test-local cleanup and preserves its fixture root. The probe records parent and helper phases, reports exit/output/EOF state with assertion failures, and preserves the fixture directory whenever an expected observation fails or cleanup is needed. Before a cleanup signal, a bounded native process-info query revalidates PID, parent PID, process group, start time, and executable path. The child group is never signalled if the leader is missing, reparented, or has a different identity. Production relay behavior is unchanged.

## Validation

Focused Swift source parsing, SwiftFormat, SwiftLint, Markdown lint, pure identity-tampering cases, and a small native process-info harness are recorded with the patch receipt. The full instrumented target test, including the cancellation regressions, remains for exact-head integration review. This change does not rerun or waive the failed five-minute hosted coverage job, and it does not claim to correct a production signal-relay defect.

## Compatibility and remaining work

The test's production signal sequence and expectations are unchanged. The timeout remains a failing hosted result until a new exact-head coverage run passes. If the retained phase marker identifies a production wait, that wait requires separate evidence and review before any production change.

Related to [the matching issue handoff](ISSUE-process-signal-probe-timeout.md).
