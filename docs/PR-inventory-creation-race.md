# PR: Bound inventory cleanup to identities preceding its observation

## Motivation

A stale container-list observation could delete an identity persisted during the request, causing output-capture startup to fail. See [the issue](ISSUE-inventory-creation-race.md).

## Implementation

Read durable metadata before awaiting the native inventory request in both the typed and CLI paths. Reconciliation can still adopt observed native identities, but orphan cleanup cannot consider identities created after its initial snapshot. Preserve exact identity and output-history validation.

## Validation

The new `AppleContainerInventoryCreationRaceTests` regression failed on the original source and passed after the correction with Bazel in the stock profile. It deterministically checks both preservation of the concurrent creation and cleanup of the earlier orphan. The complete stock `DevContainerAppleRuntimeTests` suite passed: 323 tests across 40 suites. Packaged C03 evidence remains required before declaring live qualification complete.

## Compatibility and risks

The list response remains an observation at its original point in time. A newly created container can appear in the next request, while its identity survives this request's cleanup. Historical failed campaigns remain immutable. This fix does not relax incarnation equality, certificate verification, attachment deadlines, or the performance gate.
