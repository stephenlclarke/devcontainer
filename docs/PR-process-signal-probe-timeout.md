# Pull request handoff: bound inherited-signal test observation

## Change

The signal-relay regression probe replaces unbounded wrapper waiting and pipe EOF reads with finite process-exit polling and nonblocking drains. The helper now writes the child PID to a same-directory temporary file and atomically renames it into place; the parent waits for a complete positive PID before binding native process identity. Cancellation during marker or wrapper-exit observation runs bounded test-local cleanup and preserves its fixture root. The probe records parent and helper phases, reports exit/output/EOF state with assertion failures, and preserves the fixture directory whenever an expected observation fails or cleanup is needed. Before a cleanup signal, a bounded native process-info query revalidates PID, parent PID, process group, start time, and executable path. The child group is never signalled if the leader is missing, reparented, or has a different identity. Production relay behavior is unchanged.

## Validation

The hosted run `37915763331` at `30a51ba5f0f75456e173bec047bf4efaf86f0996` failed in coverage before any SonarQube steps; its retained diagnostic identifies the partially published PID marker, not a Sonar finding. Swift parsing, SwiftFormat, Markdown lint, and `git diff --check` pass for this follow-up. Strict SwiftLint reports only two pre-existing inclusive-language findings at `Tools/process-test-probe/main.swift:163–164`, outside this change. The focused Bazel target could not run here because neither `bazel` nor `bazelisk` is installed. The exact-head hosted coverage job remains required, and this change does not claim to correct a production signal-relay defect.

## Compatibility and remaining work

The test's production signal sequence and expectations are unchanged. The timeout remains a failing hosted result until a new exact-head coverage run passes. If the retained phase marker identifies a production wait, that wait requires separate evidence and review before any production change.

Related to [the matching issue handoff](ISSUE-process-signal-probe-timeout.md).
