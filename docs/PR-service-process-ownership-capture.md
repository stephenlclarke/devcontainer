# PR: Retry coherent service ownership capture after PID transitions

## Motivation

The controller sampled launchd process IDs and the host process table at different times. A PID transition between those reads could stop qualification before service mutation, even when a subsequent coherent snapshot would be available. The retained Compose failure did not include a process capture, so it does not prove that its PID was transient. See the [issue](ISSUE-service-process-ownership-capture.md).

## Implementation

Recheck each service's exact label, definition path and executable before and after a process-table sample. If the launchd PID mapping changed during that sample, retry at most twice more. Accept only a stable mapping whose non-null PIDs all exist in that sample. A persistent missing PID, changed service definition, or repeated transition fails closed. Error messages distinguish these failure classes without exposing process arguments, environment or definition contents.

## Validation

The focused `test_runtime_services` Python suite passes 45 tests. New deterministic coverage proves that a PID transition is retried and the stable service plus descendant are captured, while an unchanged missing PID and a service-definition change are rejected. No launchd, runtime, build or fixture workload was run. The preserved campaign remains failed; fresh native qualification is required to establish whether this correction resolves the observed startup failure.

## Compatibility and risks

Service ownership remains limited to process trees rooted at exact launchd registrations. Retries are bounded and do not signal or restart a service. Process inventory inconsistency and service-definition changes remain errors. No runtime, package, fixture or release identity changes are included.
