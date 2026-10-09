# Issue: Recapture service ownership across a proven PID transition

## Problem

Native runtime admission samples launchd service PIDs and then takes a separate process-table snapshot. A service PID can change between those observations, so the old process may be absent from the table and the controller aborts before making any host change. The retained Compose failure occurred in `service-snapshot`; it did not retain the missing PID, so it cannot establish whether that instance was a transient transition or a persistent process inconsistency.

## Expected behavior

Retry a small bounded number of times only when launchd reports a different PID set before and after the process-table sample. Service label, definition path and executable must continue to match the original snapshot for every accepted sample. A stable launchd PID missing from the process table, changed service definition, or exhausted retry bound remains a hard failure.

## Scope and validation

The ownership-capture helper rechecks exact service identities around each sample and retries up to three times only after an observed PID transition. Deterministic tests cover transition then stable capture, stable missing PID rejection, and definition mutation rejection. No host services are contacted by these tests. Fresh native runtime qualification is still required to determine whether the retained campaign symptom is resolved.
