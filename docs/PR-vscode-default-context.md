# PR: preserve explicit local endpoints under VS Code's default marker

## Summary

The Docker adapter recognizes `DOCKER_CONTEXT=default` as neutral only when `DOCKER_HOST` explicitly supplies an endpoint. The existing Unix endpoint validation still applies, and named environment contexts remain unsupported. Explicit CLI host selection retains its existing precedence.

## Evidence and compatibility

The pinned Dev Containers VSIX `0.467.0` constructs the default marker internally. The failed stock log records the adapter rejecting its server-version query before attachment. This correction follows the extension's neutral-marker handling without modifying the pinned extension or normalizing its results.

## Validation

Focused parser regressions cover the real invocation and rejected alternatives. Executable, full coverage and fresh exact-main runtime gates remain required before release.

Focused stock Docker client, Dev Container CLI and Compose CLI tests pass in invocation `a0531e99-4f4b-464e-b186-763abc9b91e6`. Formatting and focused static analysis pass. Full coverage and fresh exact-main release gates remain required.
