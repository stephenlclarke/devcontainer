# Use the native builder in finalized stock qualification

## Motivation

The Compose-fork lane attempted a separate privileged Docker Buildx builder before any fixtures, although the admitted stock SDK package builds through the native Engine API. Skip that extra setup for authenticated finalized stock packages on native lanes; retain Docker Buildx setup on the Docker oracle and development/enhanced paths. E04 still exercises the unchanged build fixture. This does not grant unsupported security options or alter fixture assertions.

## Validation

Focused regression tests and the complete source/package/runtime release gates are required. Stable publication remains conditional on zero semantic differences and verified host restoration.

## Compatibility and risks

No parity waiver or lower dependency rebuild is introduced. The release must qualify the exact signed package with both native providers and the Docker oracle.
