# Match E04 builder readiness before measuring the build

## Motivation

The full parity run bootstraps Docker Buildx before fixtures, while finalized native runs first encounter BuildKit during the timed E04 build. In the retained run, E04 completed functionally but measured 16.417 seconds on stock Apple and 12.075 seconds on container-compose versus 0.571 seconds on Docker, failing only the existing 10x timing comparison.

Admit the exact locked native builder in the active private provider HOME, start it before the E04 timer, and prove its listener with one uniquely tagged disposable build. Remove that image by its exact tag/ID and verify absence; retain private journal evidence and clean up the owned builder through `ReleasedBuilder`. Run an equivalent disposable readiness build through the Docker oracle before its timer. Make E04's measured positive and negative RUN steps consume a per-invocation nonce so a previous run cannot satisfy either build from cache. Keep E04 on the legacy Engine route, its existing semantic observations and timing threshold, and retain all failed campaign evidence.

## Validation

Focused regression tests and a fresh signed E04 component run are required before another full release campaign. The readiness phase remains outside the measured fixture duration and is reported separately. Stable publication remains conditional on zero semantic differences, unchanged timing limits and verified host restoration.

## Compatibility and risks

Readiness uses a separate nonce and cannot satisfy the measured RUN from cache. No timing waiver, retry, lower dependency rebuild or fixture assertion change is introduced. The release must qualify the exact signed package with both native providers and the Docker oracle.
