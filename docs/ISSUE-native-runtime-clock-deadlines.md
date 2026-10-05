# Native runtime duration delays crash the signed Engine allocator

## Observed failure

The signed 095 campaign passed the first five stock fixtures and failed E06 network/volume. Docker passed all 27 CLI fixtures; the later stock failures followed the Engine crash and are not separate passing qualification evidence. Diagnostic `devcontainer-engine-2026-10-05-210420.ips`, incident `DFAB79C1-2EA6-4F60-BB0A-C93187B30E2D`, has SHA256 `ef65641d0ec16467c68b39b34ef9dadb0a87418e0b8e7ee55bbdeb6a2aa34717`. Fault thread 32 records `swift_task_dealloc` followed by closure #1 in `AppleContainerRuntime.scheduleAutomaticRemoval(id:)`, image offset 771944. This signed failure is actual RED evidence.

## Correction

Apply the direct `ContinuousClock.sleep(until:)` compiler workaround to the seven remaining production generic duration sleeps in `DevContainerAppleRuntime`. Construct a local clock immediately before each existing delay within its current task or loop and advance its current instant by the original duration. Automatic removal remains one second; direct-process drain and archive-transfer state polling remain 20 ms; event change waits, container exit polling and port-forwarding observations remain 200 ms; process-log polling remains 100 ms. Preserve each `try`, `try?`, catch, surrounding deadline, iteration limit, registration fence, identity check and cancellation behavior. The existing nanosecond sleep is outside this generic duration correction.

The SDK XPC timeout and I/O drain timeout previously crashed at the same allocator operation and already use direct clock deadlines. This supports applying the workaround to the same remaining app-runtime call shape; it does not prove every remaining call would crash or guarantee the compiler has no other fault. No dependency, public API, SDK pin, published Apple runtime executable or performance change is included.

## Validation

The first optimized stock run after the seven production corrections aborted in a test helper. Retained invocation `245ec968-5377-411c-8590-9abc16498104` records the failure. Diagnostic `DevContainerAppleRuntimeTests-2026-10-05-220553.ips`, SHA256 `19dbedb666d4150ad7c2b3c664a27315f6ac9d0d486c052fb8ede1bd8bc1717b`, incident `2E575561-DEC9-411D-9982-7C99DB096326`, records `swift_task_dealloc` followed by the closure in the deferred cancellation-before-activation test. This is an allocator abort, not an assertion failure.

Apply the same local clock deadline workaround to the 37 generic duration sleeps across 15 runtime-test files. Preserve all duration expressions, throwing behavior, loops, task boundaries, cancellation and assertions. This does not claim each of the 37 sites independently reproduced the crash or establish a compiler root cause.

Retained optimized stock/release invocation `5b204c53-5eb4-4ce0-b95b-8326b1db51ea` passes all 319 runtime cases with zero failures or errors. Whole-source SwiftFormat and strict SwiftLint checks pass. Existing tests cover lifecycle identity and removal fences, process streams and cancellation, snapshot polling, port-forwarding retirement and archive/I/O waits. The focused parity harness checks pass all 30 selected cases and all 64 qualifier-module cases. Fresh signed E06 and complete release qualification remain required. Preserve both original failed invocations and their diagnostics.
