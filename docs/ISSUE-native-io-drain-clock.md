# Native I/O drain timeout allocator crash blocks signed runtime qualification

## Observed failure

The signed d52 full campaign passed E01 and crashed in E02 container lifecycle. E09 never ran. Diagnostic `devcontainer-engine-2026-10-05-193933.ips`, incident `8061B171-A2E2-4684-8833-80F245A6CB3D`, records fault thread 1 with `swift_task_dealloc` followed by closure #2 in `AppleContainerIO.finish(exitCode:drainTimeout:)`, image offset 188660. Its SHA256 is `25cda10853fe38653bd54659266f515ca8c796af551ebc54fa3d86fb57a4b86c`. This actual signed-package failure is RED evidence. The earlier anonymous XPC test was a nonreproducer and is not claimed RED.

## Required correction

Replace only the drain timeout child's generic `Task.sleep(for:)` with a direct `ContinuousClock.sleep(until: clock.now.advanced(by: drainTimeout))`. Construct the clock inside that child so the timer starts at the same stage. Preserve the default 30-second duration and caller overrides, EOF cancellation and timeout joining, monitor/input/control drains, timeout errors, durable output completion and exit-state ordering.

## Scope and validation

Existing `AppleContainerIOTests` cover complete separate stdout/stderr EOF and real exit, plus retained-writer failure with a 20-ms drain deadline. Keep their behavior intact. The optimized stock `DevContainerAppleRuntimeTests` passed all 318 cases in retained invocation `ddf6b9f1-d0b2-470a-a89e-c7aa793096a7`; fresh signed runtime qualification remains required. Do not change the Apple 1.4.1 runtime, SDK pins, stream contracts or ownership boundaries for this correction.
