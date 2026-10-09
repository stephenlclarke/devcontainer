# Issue: preserve Feature staging cache identity on stock Apple

## Problem

The Apple runtime adapter changed the generated Feature staging Dockerfile from
`COPY . /tmp/build-features/` to `ADD context.tar /tmp/build-features/`. It
created that tar for every build from a newly extracted context. Tar headers
include filesystem timestamps, so unchanged Feature content could produce a
different BuildKit input and miss the downstream Feature-install cache. The
three-run campaign recorded a D05 stock Apple operation of 47.026 seconds
against 4.473 seconds in Docker (10.513x); that timing is retained as a failed
comparison, not evidence of a speedup.

## Expected behavior

- Submit the original staging Dockerfile and its extracted context directly to
  the pinned stock Apple builder.
- Preserve all original context paths, file contents, modes and links; do not
  rewrite or normalize fixture inputs to influence cache behavior.
- Keep the D05 functional observations, frozen-lock negative assertion and
  cleanup requirements unchanged.
- Treat functional parity and any performance comparison as separate results.

## Acceptance evidence

- Focused adapter tests assert that the original `COPY` instruction and nested
  Feature installer remain in the original build context, with no synthetic tar
  archive.
- The pinned stock Apple runtime accepts that direct `COPY` and produces the
  expected D05 functionality and cleanup with no semantic differences.
- Any fresh timing comparison is reported with its complete raw run identity;
  historical references are reused, not regenerated. A timing result does not
  waive a functional failure or establish a general optimization claim.

## Current status

The adapter change and focused fake-CLI regression are prepared. They have not
yet been built or run against the stock Apple runtime. The recorded 10.513x
functional-run comparison remains failed and unchanged. Do not mark this issue
resolved until the pinned runtime proof is complete.
