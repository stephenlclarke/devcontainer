# Change handoff: preserve D05 Feature build-cache identity

## Summary

Remove the Apple adapter's timestamp-sensitive tar repack of the generated
Feature staging context. Submit the original `COPY . /tmp/build-features/`
Dockerfile and extracted context directly to the pinned builder.

## Motivation

The former adapter converted a staging `COPY` into `ADD context.tar`. It built
the tar from a fresh extraction for each build, so archive metadata changed
even when Feature files did not. BuildKit therefore received a new `ADD` input
and could rerun the Feature-install step instead of reusing its cache. In the
retained three-run campaign, D05's third stock run took 47.026 seconds against
4.473 seconds in Docker (10.513x). This is a recorded failed comparison; this
change does not alter or reinterpret that result.

## Implementation

- Remove the staging-Dockerfile special case and its temporary tar creation.
- Keep the extracted request context and generated Dockerfile as the exact
  build inputs passed to `container build`.
- Extend the fake-CLI adapter regression to check the original staging `COPY`,
  nested installer path, context argument, and absence of a generated archive.
- Keep D05 assertions and its functional failure history unchanged.

The direct-context path preserves the extracted input tree. No timestamps,
paths, modes, links, or Feature files are normalized to manufacture a cache
hit. This relies on the pinned builder honoring the original `COPY`; that
runtime behavior still requires fresh stock proof.

## Validation

Not run yet. The source and focused regression are prepared, but no build,
test, or stock runtime workload has been executed for this change. Acceptance
requires a run with the pinned stock Apple runtime, passing all unchanged D05
functional and cleanup assertions, and retaining any failed result honestly.

## Compatibility

No public API, fixture, lockfile, or reference Docker behavior changes. The
change affects only how the Apple adapter submits the already-extracted build
context to its existing builder.

## Remaining risk

Direct `COPY` behavior must be confirmed against the pinned stock Apple runtime
and the exact prepared guest. No speedup or optimization acceptance is claimed
until that proof and a valid, separately reported timing comparison exist.

## Linked issue

Tracks [the D05 Feature cache issue](ISSUE-d05-feature-cache.md).

## Local component validation

The complete stock Apple runtime unit suite passed 323 tests across 40 suites after the direct-context change. The generated COPY Dockerfile and nested feature installer are retained; the fake CLI rejects a synthesized archive or mismatched context root. Build arguments use a resolved physical context root to avoid the pinned stock FSSync traversal problem under symlinked temporary-directory ancestors. The first new unit invocation exposed a test recognizer that omitted leading whitespace; that failed log remains retained beside the corrected full-suite result. Live COPY semantics, cache reuse and the original timing gate still require new candidate evidence.
