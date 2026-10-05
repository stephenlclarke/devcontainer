# PR: use the admitted JSON probe for native Compose version detection

## Summary

The `devcontainer-compose` facade now answers Docker-facing `version --short` and `version -s` requests using the selected provider's authenticated `version --format json` response. It validates the provider source and nonempty, single-line version before returning `container-compose <version>`; nonzero provider status and output streams remain intact.

## Focused coverage

The Compose CLI tests cover the real facade entrypoint with the native provider selected over both stock and matched backends, verify the JSON probe and honest vendor-qualified output, preserve provider failure status, and reject invalid version responses. The authenticated released provider also passes its JSON probe, reporting version `0.15.1` and commit `57ee265a5fbd17a1109240a0d0aa99da01acfc3a`.

Focused stock Docker client, Dev Container CLI and Compose CLI tests pass in invocation `a0531e99-4f4b-464e-b186-763abc9b91e6`. Formatting and focused static analysis pass. Full coverage and fresh exact-main release gates remain required.
