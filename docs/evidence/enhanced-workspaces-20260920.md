# Enhanced workspace qualification, 20 September 2026

All seven unchanged workspace fixtures now have passing enhanced-runtime results: image configuration, Dockerfile builds, users/environment, lifecycle hooks, Features, port publishing, and reuse/rebuild/cleanup. D06 initially timed out; after the operator reported approving a dialog, its complete original contract passed with the same binaries and deadlines. The initial failure and separate diagnostic remain preserved below. Every completed case has automatic cleanup with zero owned resources; the post-approval run needed no further intervention and final recovery reports clear.

These are ordinary functional observations, not quiet benchmarks, measured speedups, three-lane parity, or stable-release qualification. No product was rebuilt for these runs. The separate enhanced E03 large-duplex timeout remains unresolved; see [its evidence](../bazel-test-harness.md#execution-boundaries).

## Inputs and method

- Harness source: `34d9cfce8e1cf5d2822aeab6fcaa3126029778e5`; harness fingerprint: `7ef9173e54692462f7655832373af3712b212bcb2cd93dc63a102dac8cc55101`.
- Reused optimized enhanced product: source `ea3e51f9d406307ddfc8d5180b2e4a804807042f`, candidate invocation `5665b66a-fd27-4f39-8ecc-4344d7daa322`, archive SHA-256 `ee671f58b194e2a32bb0d308a3ecae5d801ae9572b17f995627e0ed668a3544e`.
- Authenticated downloaded runtime: Container `780a86b995ac`, Containerization `7e066a3101bc84fa0f7231daf6a03aa9ef62a567`, from the pinned Compose 0.15.1 release. This is not a fresh-machine guest-publication qualification.
- D01 campaign: `enhanced-d01-20260920`; D02: `enhanced-d02-20260920`; D03-D07: `enhanced-workspaces-20260920`. Each case uses its own isolated root under the exclusive runtime lease.
- Every fixture retains its original inputs, semantic assertions and deadlines. Setup, operation and cleanup are separate monotonic durations. No failed case is retried or converted into a speedup denominator.
- D06 stopped the serial batch. After inspecting its evidence and verifying cleanup, D07 ran separately because it does not depend on published ports.

## Results

Durations are seconds, rounded here to six decimal places. Exact nanoseconds remain in the sealed evidence.

| Fixture | Result | Setup | Operation | Cleanup | Bazel invocation |
| --- | --- | ---: | ---: | ---: | --- |
| D01 image configuration | Passed | 10.393605 | 8.379470 | 2.144083 | `8cf12ca5-cdfb-4fcd-93c9-d4a3ffa11325` |
| D02 Dockerfile | Passed | 17.909576 | 6.170521 | 6.132423 | `34259bf5-1cee-4e21-b2a7-26dfd96c1762` |
| D03 users/environment | Passed | 17.894135 | 6.247445 | 3.127402 | `39f427bf-e41b-4bba-a2f8-d23af0430342` |
| D04 lifecycle hooks | Passed | 9.093194 | 8.133755 | 2.066214 | `c42fd4d3-4ca9-409a-abdb-245475778661` |
| D05 Features | Passed | 18.097204 | 33.952054 | 6.591391 | `79de78cd-0855-416b-abd4-1e7e7bf89ca6` |
| D06 ports | Timeout | 9.393788 | 17.272474 | 2.023547 | `53c84ea7-ed94-4503-b0d0-a019b9c80350` |
| D07 reuse/rebuild/cleanup | Passed | 9.265927 | 12.522048 | 1.826425 | `37094505-c9e2-447b-aa2d-dbff91c82d8f` |
| D06 ports, after approval | Passed | 10.525280 | 8.553880 | 2.360645 | `47e8f828-6575-4be5-9e9c-48398d78e06c` |

In the initial D06 attempt, CLI startup and the guest probe both exited zero; the guest probe reported `forward_metadata=true` and `inside_connectivity=true`. The subsequent host-loopback HTTP readiness check exceeded its existing deadline before the collision test began. That failed receipt retains empty observations and `timeout`; partial command output is diagnostic only. The native worker logged `No route to host` when connecting the published port to the guest. The separate diagnostic below narrowed that failure before the later successful post-approval run. No deadline, fixture, provider or success requirement was changed.

## Separate port diagnosis

A one-shot diagnostic at harness source `1a09f2aa5fb393f2e452fcb6c61421a708afdc0d` reuses the same product and downloaded runtime without rebuilding. It executes the original D06 startup and guest probe, then records native inspection, guest networking, host routing and bounded direct/published HTTP probes. Its helper received independent full-file review; interrupted recovery additionally requires each diagnostic child's exit, stop, retained logs and absent PID/process group before ordinary recovery. Missing evidence preserves quarantine. The diagnostic uses its own private result database, not the authoritative parity store, and deliberately fails because it does not execute the original collision contract.

| Observation | Result |
| --- | --- |
| Native attachment, guest interface, projected Engine address | Agree on one guest address |
| Host route and active bridge | Correct route to that address |
| Host Python direct guest HTTP | Exact original response body and HTTP framing |
| Apple curl direct guest HTTP | Exact original response body |
| Host Python through published loopback port | Connection accepted, then reset; no response bytes |
| Apple curl through published loopback port | Failed |
| Native worker destination | Same guest address and port as successful direct probes |
| Native worker backend connect | `EHOSTUNREACH` (`No route to host`) |
| Automatic cleanup | Passed, zero owned resources, no new approval; recovery clear |

Diagnostic campaign `diagnostic-enhanced-ports-20260920`, case `143f464e9d854bbedbdbdb928942e6e886e5ee50b776d6fe85e7b4d51c241d97`, result seal `10819322ea1bf718e194beb50c8c78350a426d2bf849d28a5df92c60c591315d`. Exact setup/operation/cleanup durations are `9.899601208`, `8.348129375`, and `5.244548250` seconds. These timings include diagnostic commands and must never be compared as D06 benchmark samples. No Bazel invocation or successful parity receipt is asserted for this separate diagnostic.

The privately retained one-shot helper `diagnostics/enhanced-ports-20260920/native_ports.py` has SHA-256 `8e7ec8ac01b9baeebf5113d8a70516a286a349e37f975e32d09f7812fe4c295a`. This supplementary source digest was recorded after execution; it is not represented as part of the original sealed case identity.

The result rules out an absent host route, a wrong observed guest address and an unreachable guest HTTP service during these probes. It does not distinguish a process-specific network authorization issue from a native forwarding defect. Read-only inspection confirms the downloaded worker has a Developer ID signature and its path-specific saved network rule appears to allow access; neither proves the running process's effective authorization. System Settings has numerous identically named runtime entries, so no permission was changed or inferred from an ambiguous row. Apple's [local-network privacy guidance](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy) explains that authorization is code-identity dependent and has no general status-query API. A future authorization diagnostic must run in the responsible runtime context; testing a different host process is not sufficient. No security bypass or unchanged retry is justified by this evidence.

After this diagnostic completed, the operator reported pressing Allow on a dialog. That is a subsequent authorization-state change, not an assistant permission change or part of the diagnostic above. A fresh execution of the original D06 contract then passed all four assertions: guest HTTP, host-published HTTP, actual forward metadata and specifically rejected port collision. Automatic cleanup passed without further intervention and report-only recovery is clear. The original failed seals remain unchanged.

The passing campaign is `enhanced-ports-approved-20260920`, at clean signed harness source `caca30683589c5e6b93527354b74c96f66a3fe97`, Bazel invocation `47e8f828-6575-4be5-9e9c-48398d78e06c`. It reuses the original product archive and runtime, and retains the same harness, contract, release-set and runtime fingerprints as the separate diagnostic. Exact setup/operation/cleanup durations are `10.525280417`, `8.553880125`, and `2.360645250` seconds. This closes the observed D06 functional failure on the approved host. It supports an authorization dependency but does not identify the exact dialog or prove the underlying OS policy cause. It is not a quiet benchmark, fresh-machine setup proof or complete unattended release qualification.

## Retained case identity

The local `runtime-cases.sqlite` store retains original request metadata, immutable runtime/guest fingerprints and results. Sensitive service snapshots and logs remain in the separate private journal and are not published here. Case IDs and result seals identify the original evidence; they do not establish release authority.

| Fixture | Case ID | Result seal SHA-256 |
| --- | --- | --- |
| D01 | `bd1ddac66569b008ab43d36a3e9ec239d3c2e687b376dfe9b422d381f3ea2079` | `b5d4fb669e6b4a100e72b66134ee8d84aa6f6fcdee21940c4c6327eae486145b` |
| D02 | `cdc6a90bf062866b9b32f9560ff92bc5e5142763f5490f53640f92bebc2c1db8` | `2458bac451e8f946d38d91e5290bde69961b57e736af5459a4b95160bf6e320a` |
| D03 | `269af838b772e8af69879d00dcf3c9594bd20ac04c5ab7aa2ce8fe1089267695` | `0ce8b943013596605961027138355ac37ef1bd25953c02a5d41e1355ef28e82c` |
| D04 | `5f949f1c9815f370119b5cd821cb27352ab8d50d4ef42eeda2602c54a47b6330` | `46cf510fd202fec4f658fa61443ee499807ea1b184c5042e9544b164383cedd5` |
| D05 | `b21092c782000dd07f6d37522bf9f72c228083cac23389d16c968fddb80ec056` | `a0f6f8d7166ddb35df9375ba2d841afeb4d4288bf46de41a673158659693e183` |
| D06 | `92b6041f3b5e293cd78ebe1c6eff7fc0f2ed55fbd11d57646be76990cdd9c4d8` | `b7647384d5fdb2e134205b9e8b14a793108240566378232f456d1dbd627288ad` |
| D07 | `619965e1a4ee5ddfd2a026d2a617452ecc947cf65b9a0772f7ab51cf5df21800` | `6c75edd4eccae8e7869a24c31f64c8fa9927a3c209f6ab5039d8e550ea5026cf` |
| D06 after approval | `b16ba6bcd84dcac97d00d765edea72709ea2c8671880b0f9193b94608f2766a8` | `baa8fbeee6640754859d8f78170844bdc052ee0acc000621b3e8578069e05701` |

Git/SSH prompting and isolated test-Keychain interaction are disabled. These successful and failed-case cleanup runs add evidence for that narrow unattended path. Complete host permission/identity preflight, signing/notarization, release publication and unattended fault recovery still require qualification; see [the workflow requirement](../bazel-workflow.md#unattended-authorization-requirement). This report does not claim that changed identities or operating-system permissions can never need new one-time consent.
