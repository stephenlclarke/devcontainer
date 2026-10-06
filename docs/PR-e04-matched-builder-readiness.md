# Match E04 builder readiness before measuring the build

## Motivation

Finalized native qualification previously started BuildKit inside the measured legacy E04 command, while Docker Buildx was already bootstrapped before timing. Add exact locked native-builder admission and private ownership before the timer, then perform one uniquely tagged disposable build and verify its image is removed. Run the same readiness operation through the Docker oracle before timing. E04 remains on its legacy Engine route, with the existing observations and 10x threshold. Its measured positive and failing RUN steps consume a fresh invocation nonce, preventing prior cache entries from replacing the work under test.

## Validation

Builder startup and readiness durations are retained separately from the measured fixture. The temporary image and builder are removed through exact ownership checks before host restoration. Focused regressions cover singleton setup, measured cache isolation, inspection errors and uncertain cleanup, including isolated module loading without inherited Python paths. A fresh signed E04 component run and the complete source/package/runtime release gates remain required; no failed campaign data is discarded.

## Compatibility and risks

The comparator, timing threshold, E04 semantic assertions and Docker build options remain unchanged. Readiness uses a separate nonce and cannot satisfy the measured RUN from cache. No timing waiver, retry or lower dependency rebuild is introduced. The release must qualify the exact signed package with both native providers and the Docker oracle.
