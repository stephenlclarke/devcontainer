# Enhanced engine qualification, 20 September 2026

The enhanced candidate passes the complete E08 terminal contract after a devcontainer-only process-identity correction. E05 archive copying, E06 networking/volumes, F01 fault recovery and E04 image builds also have new passing enhanced results; E02 lifecycle/restart passes again on the corrected candidate. All completed cases, including failures, cleaned up automatically with zero owned resources and no additional authorization dialog. Report-only recovery is clear.

These are focused functional observations, not quiet benchmarks, complete three-lane parity, published-release comparisons or stable-release qualification. Enhanced E03 exec transfer remains unresolved. E07 now passes setup but times out during first-generation attachment; its exact failing substage and any shared cause with E03 remain unproved.

## Inputs

Campaign `enhanced-engine-20260920` uses harness source `022390de4dcdc9c2df3ed909fc418cc7de4d6080` and reuses product source `ea3e51f9d406307ddfc8d5180b2e4a804807042f`, package invocation `5665b66a-fd27-4f39-8ecc-4344d7daa322`, archive SHA-256 `ee671f58b194e2a32bb0d308a3ecae5d801ae9572b17f995627e0ed668a3544e`.

Corrected campaign `enhanced-terminal-precise-20260920` uses clean signed source `9deafa654eafcf903ee56c295381ae7123d9efb2`, package invocation `e7f101f6-03a9-41fb-adc6-1361af7df5be`, archive SHA-256 `efb1c405272fa4fbaac3b9d4a649f19e98ca118eb61eec8285602e71b3d352d9`, preparation key `1cd098fab4a1104bfa98af8d197cdc6cee5fc7c095129b21ce0efa4061dd04bc`. The optimized package passed its smoke test. Its single build observation took `37.948833916` seconds, with 1,245 action-cache hits and 24 executed actions. The retained receipt has `quietHostVerified=false`; this is not a quiet benchmark or speedup comparison.

Both campaigns use the same authenticated downloaded runtime from Compose 0.15.1: Container `780a86b995ac` and Containerization `7e066a3101bc84fa0f7231daf6a03aa9ef62a567`. No lower repository, runtime binary, fixture assertion or deadline changed. Harness fingerprint: `7ef9173e54692462f7655832373af3712b212bcb2cd93dc63a102dac8cc55101`. Guest prerequisites are prepared locally; fresh-machine enhanced guest publication remains unqualified. Results from different product candidates are not interchangeable final-head evidence.

## Functional results

Durations are seconds, rounded to six decimal places; sealed results retain exact nanoseconds. Failed operation durations are never speedup denominators.

| Fixture | Candidate | Result | Setup | Operation | Cleanup | Bazel invocation |
| --- | --- | --- | ---: | ---: | ---: | --- |
| E05 archive copy | Original | Passed | 8.423267 | 5.867686 | 1.965050 | `7199a4b0-6db3-4558-b5db-65dae0e99320` |
| E06 network/volume | Original | Passed | 8.255075 | 10.260853 | 2.911448 | `c4186d55-312d-4b30-9d17-47ece8a72abf` |
| F01 fault recovery | Original | Passed | 8.038237 | 9.086371 | 2.064650 | `e0ca4b37-6bc5-4c79-b8e3-ae83f4b49e16` |
| E08 terminal | Original | Failed, resize 409 | 7.973919 | 5.716666 | 2.074718 | `b27eb6a7-b16b-4a45-9ed8-41d6b689c7c0` |
| E08 terminal | Corrected | Passed | 8.814528 | 7.192814 | 1.912450 | `2778e8e3-7d6a-48a9-aa54-a8af9b29f2b4` |
| E07 init attachment | Corrected | Timeout | 8.173034 | 34.489636 | 2.133922 | `90572a0c-4342-43de-9eab-b9ccb3be0bde` |
| E02 lifecycle/restart | Corrected | Passed | 8.675603 | 8.991038 | 1.939378 | `c363601a-b72c-4d38-adc4-651b1afef887` |
| E04 image build | Corrected | Passed | 16.924969 | 4.339225 | 3.536775 | `4cb331d1-435d-48fb-8f4a-5703c811930f` |

Initial corrected-package invocation `760ce244-0a01-44cc-8a4f-2d5232c0e2f8` failed admission because the archive had not yet been explicitly prepared. It never started a runtime case. The existing preparation command then authenticated/extracted that same archive; the subsequent live pass is separately identified above. No rebuild or functional-failure retry was hidden.

## Terminal identity correction

Enhanced inventory preserves CLI-only fields but its text timestamps lose precision. Creation identity was already restored from the native XPC response; process start identity was not. E08 reached startup and exact terminal output, then resize returned 409 because the rounded start timestamp did not match the owned native process generation.

The narrow native identity decoder now reads top-level `startedDate` without decoding incompatible enhanced network attachments. Reconciliation validates the CLI-encoded start date before restoring the precise native value. Missing, malformed or inconsistent observations fail before metadata changes. Two generations within one encoded second remain distinct; attachment and resize ownership retain exact equality rather than a one-second tolerance. Stock API behavior and separately maintained Compose/Container code are unchanged.

Failing-before regression: `08873f8d-3605-4201-90e8-2ed090598225`. Corrected inventory/attachment suites pass under enhanced (`4c151681-d44c-4ed4-aa0f-ce5bde91465b`) and stock (`be731746-d36f-48ee-bee0-21e5bb570de9`) configurations. Independent complete code review found no actionable issue. The unchanged original E08 then passes all seven observations: raw terminal output, running resize, detach preserving init, stdin after reconnect, acknowledged wait, exact exit and automatic removal.

Focused instrumented invocation `9bb16c85-7747-4d45-bcaa-b00c6502f778` at clean `9deafa6` passes 18 test functions across two suites. Combined LCOV SHA-256: `f835ede1b02b630c2e7ffe4b12c82ff1cae98b49aaa06ab34b4acd372ee2f293`. Measured changed executable lines are 19/19 covered; data-only declarations and comments have no executable-line denominator. This is changed-line coverage, not whole-module/repository coverage or Sonar authority. No coverage-collector/profile errors were observed in the retained test log. Scoped SwiftFormat, strict SwiftLint and diff checks pass.

## Remaining attachment failure

E07 creates and starts successfully, observes startup output and opens both primary live and combined history/live attachments with HTTP 101. Both interactions then reach the original connection deadline. No completed combined-attachment receipt exists. Current diagnostics do not distinguish waiting for observer startup history from subsequent duplex transfer or EOF; a common E03 cause must not be assumed. The failed result has empty observations and remains immutable. Cleanup removes only the authenticated owned guest and restores original services automatically.

Next proof: retain bounded, payload-free stage/progress observations for this existing path, with focused error-path tests, before another correction. Do not increase timeouts, normalize output or repeat E03 unchanged.

## Retained evidence

The local `runtime-cases.sqlite` store owns immutable results and request timings. Sensitive service journals are private and not published. IDs/seals identify evidence, not release authority.

| Fixture | Case ID | Result seal SHA-256 |
| --- | --- | --- |
| E05 | `9ca46c617512fc6507f201a63eb2e87815cb653f5eaa3b3995dc655d5910bec5` | `af7864b8a815e8214a192412e1a2162f0cfb3b0920b717d48acc380164ff5b85` |
| E06 | `7aabd8fcbb2c9f31bf7a187a617dfcac18c4c8869fdd17d15bacb0a67f275d5c` | `ecae846668419a8f09c1a993a00112b68bcd9c5eb84b1d71d29eb31ee9e56d08` |
| F01 | `1368267b96de6bcc9c9ab99b331b95e00ef3d630833b0d134a87fc16d93d9b93` | `eb447506630364a37c1cc9dfe8c715115daac5e0bd077aa3342d29a569a6b8a8` |
| E08 original | `c4965b61757f7bda040633a2f065134e3ffc295c28a5d5d4f3dfd974b575842e` | `b51e23e7ea16a477a39a384394000ab1a3b640b142aa67bdd6c54a2f6424f5a0` |
| E08 corrected | `fb8570dfbeccc39f0a55b0a7a1fbf1ed581070ada1533bbe01fd94c7585fc6c5` | `e74e31a728eb8305401c84eb0d6531a60eb324e9ce8e41d220982137f42e93c5` |
| E07 | `95ff68ebda1c1773caf31e3a92de7e06efe1b8bf4d8dbeec10e3c53715489765` | `7689cf1cc068c3f81adb3df2e0b87c1f62eee34846e77acdec511178e0a2bea1` |
| E02 | `73d56c2b9df89fa85149dca4cf0d54b55c238ae12aaa58f9524d8a21e1fad1ef` | `d25f95cc434cf3056b73b8ed1508f8d5112ef2c6330a54f6507addcbf039b361` |
| E04 | `36b88468d856cb8fac4b5086654256be03ee383ed8f15abc921f2eeb7c0248c0` | `f56e0319cc22f79dc9d31eba1caa39f7988c811e29412e00e87ee8f46786d3ab` |

This checkpoint changes internal generation fidelity, not CLI options, pins, installation procedures or stable compatibility claims. README, architecture, DocC testing/architecture, harness and issue/PR handoffs describe the change. Compatibility tables, published release notes, schemas and examples remain unchanged because no new stable release or public contract is declared. Full release quality gates, quiet downloaded-release comparisons, signing/notarization and documentation/VHS publication remain open.
