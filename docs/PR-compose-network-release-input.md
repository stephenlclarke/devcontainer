# Fix the Compose network release input

## Motivation

Repeated Compose lifecycle operations blocked Devcontainer runtime qualification. See [issue](ISSUE-compose-network-release-input.md).

## Implementation

Pin both signed stock gateway assets to `layer-compose-stock-79b3c92930a6-3178c36a1378`, including immutable GitHub identities, sizes and SHA-256 digests. Align the parity manifest provider commit with those same released bytes. Keep every other release input unchanged and update the operator documentation.

## Validation

The lower adapter regression failed on the original code and passed after the fix. All 106 Engine tests pass. Exact clean-source stock provider, unit, coverage and package stages passed; selected unit/CLI coverage is 90.981%. Both Mach-O products were signed and verified, and Apple notarization `542ed290-49b6-4229-b4fd-dc61a1edecc1` was Accepted. Fresh GitHub downloads match the local signed asset and provenance byte for byte.

## Compatibility and remaining qualification

The gateway remains a separate external provider; it is not bundled into Devcontainer. Its prerelease provenance does not claim distribution readiness. Fresh Devcontainer exact-source build, signing, complete 84-observation runtime qualification, restoration and publication gates remain required. Performance optimization is deferred.
