# Pull progress quality gate failures

## Problem

The pull bridge passes functional tests, but the exact-main SonarQube analysis reports excessive closure nesting in its progress callback and a combined fixed route/query literal. These findings block the stable release.

## Expected behavior

Reuse the existing bounded progress handler for pull, preserving validation and error propagation in quiet mode. Build the dynamic image-reference query separately from the fixed Docker Engine protocol route.

## Validation

Existing pull parser, progress, quiet, malformed-response, deadline, cancellation and real private-socket regressions must pass. The exact-main SonarQube check must report no remaining issues before publication.

The subsequent exact-main analysis on `5485f889bc5ede913241b02a6d9b68217c6d1664` reports the inline-comment discard closure as empty (`swift:S1186`) and the query-bearing route literal as a hardcoded URI (`swift:S1075`). Make the discard explicit and assemble the fixed protocol path and query delimiter separately, retaining the same request bytes and validation.
