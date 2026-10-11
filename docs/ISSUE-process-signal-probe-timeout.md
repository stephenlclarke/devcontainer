# Issue: bound and diagnose the inherited-signal regression probe

## Problem

The inherited-process signal regression test waited for the wrapper with `Process.waitUntilExit()` and then drained both output pipes with `readDataToEndOfFile()`. Either operation could wait indefinitely if the wrapper or a descendant retained a pipe descriptor. The hosted five-minute test deadline then reported only a job timeout, while the test removed its temporary fixture directory before its assertions ran.

The exact e878 hosted Sonar run `37196928783` timed out during `make coverage-check` after entering both inherited-signal test parameter cases. Its retained log had no test assertion, crash, or failure message. The exact same source's main coverage job passed, as did its sanitizer jobs; this is evidence of an intermittent test hang, not proof of a production relay defect. The failed hosted run remains a failure and its deadline is unchanged.

A later run at head `30a51ba5f0f75456e173bec047bf4efaf86f0996` failed the coverage test suite before SonarQube started. Its `signalBeforeSpawnReturns=false` case observed a missing child PID/ownership binding, then lost the expected signal and exit/output assertions. The probe shell created the PID marker with output redirection, which makes an empty file visible before its PID text is written; the parent treated file existence as complete publication and read immediately. The retained sanitized diagnostic is `sonarqube-job-113771459299-sanitized.log` in the workflow's private failure-remediation evidence directory. This failure did not produce SonarQube findings.

## Required outcome

Keep the production relay ordering, signals, and deadlines unchanged. Bound the test's wrapper-exit observation and each pipe drain. Cancellation during either wait must also run finite test-local cleanup and retain the fixture root. Publish the child PID through a same-directory temporary file and atomic rename, and wait for a complete positive PID before binding its process identity. Bind the wrapper and child to native process snapshots containing PID, parent PID, process group, start time, and executable path; revalidate the target snapshot immediately before each cleanup signal. If the child leader has exited or been reparented, or either identity has changed, do not signal its group. Report parent/helper phases and the retained fixture path, and preserve that directory for diagnosis. Remove fixture data only after all assertions about ownership, signals, exit status, output, and EOF pass.

## Scope

This issue concerns only test-local observation and diagnostics in `ProcessRunnerTests` and its helper executable. A failure at `awaiting-inherited-return` points to the production inherited call for later diagnosis; it does not by itself establish a root cause.
