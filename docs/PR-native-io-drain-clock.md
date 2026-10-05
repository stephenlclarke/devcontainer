# Pull request handoff: fix(runtime): use direct clock for native I/O drain deadline

## Change

`AppleContainerIO.finish` constructs a `ContinuousClock` inside its existing timeout child and sleeps until that clock's current instant advanced by the unchanged `drainTimeout`. This applies the same narrow compiler workaround used for the SDK XPC deadline. The surrounding cancellation/catch, EOF waits, timeout task join, input/control drain, error publication and exit-state ordering remain unchanged.

## Validation and evidence

The signed d52 diagnostic is actual RED evidence: E01 passed, E02 crashed in the drain timeout child at `swift_task_dealloc`, and E09 was never reached. The matching [issue handoff](ISSUE-native-io-drain-clock.md) records the incident and diagnostic hash. No anonymous-test reproduction or synthetic RED claim is made.

Existing focused I/O tests cover EOF success with separate streams and real exit and the 20-ms retained-writer failure. The optimized stock `DevContainerAppleRuntimeTests` passed all 318 cases, including those existing drain tests, in retained invocation `ddf6b9f1-d0b2-470a-a89e-c7aa793096a7`. Fresh exact-source gates, packaging and signed runtime qualification remain pending; this handoff does not establish stable-release readiness or change the original failed campaign.
