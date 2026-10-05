# PR: use the corrected stock-facing XPC SDK

## Motivation and implementation

The stock signed Engine failed in XPC timeout task deallocation. Select the minimal Container SDK derivative `aad0c75555d8ccce45aea01d7e1558eb7dee408e` from Apple 1.4.1. The SDK uses direct ContinuousClock deadline sleep; timeout duration, tolerance, task-group structure and error behaviour remain unchanged. Nested Container manifests and all other selected dependency revisions are unchanged. The stock runtime is still Apple's unmodified published binary.

The finite compatibility checks bind the complete new manifest and both resolved locks, the exact graph validator, unchanged production AST and shared Go/Swift build inputs. They admit only the seven exact unchanged canonical layer locks. The old stock SDK is rejected. Historical tests retain their original manifests and graph validator instead of relabeling old evidence.

## Validation

The old SDK optimized focused suite passed 318 test cases; its anonymous-XPC checks do not reproduce the signed-package crash. The retained 0427 signed-package diagnostic is the failing integration result. All 36 focused layer/source-graph tests pass after the transition, including source, asset and shared-build drift rejection. The corrected SDK optimized Apple runtime suite also passes, with exact loaded source hash and graph validation retained at invocation `24c326ce-fb83-4ddd-b758-53727dd0a9a4`. All 346 build-tool tests pass (one expected interpreter compatibility skip). Newly published/downloaded SDK admission and the full signed-package runtime campaign remain pending.

## Compatibility and remaining gates

The source derivative is explicitly identified separately from stock Apple runtime provenance. No runtime result, healthy restoration or GA claim follows from source tests. Preserve the failed campaign and cleanup receipts. Full native checks, code quality, signing, notarization, three-lane parity, installation and GitHub release gates remain required.

Linked issue: [stock SDK crash](ISSUE-stock-xpc-sdk-clock.md).

The replacement stock SDK is published as `layer-container-sdk-stock-81d3650a39845036f9c4` and has passed a clean-source optimized qualification at `/Volumes/SSD/cf/bazel/invocations/run.jmXZ1b`. Its archive was downloaded from GitHub, hash checked and admitted by the maintained consumer verifier. Devcontainer's full signed-package qualification remains separate.

The hosted SwiftPM dependency preparation now validates the same exact SDK derivative revision and rejects the prior source, old Apple SDK location and version-tag substitutions. The CI stock job label identifies the derivative SDK and Apple Containerization accurately.
