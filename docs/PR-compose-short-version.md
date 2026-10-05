# PR: use the admitted JSON probe for native Compose version detection

## Summary

The `devcontainer-compose` facade now answers Docker-facing `version --short` and `version -s` requests using the selected provider's authenticated `version --format json` response. It validates the provider source and nonempty, single-line version before returning `container-compose <version>`; nonzero provider status and output streams remain intact.

## Focused coverage

The Compose CLI tests cover the real facade entrypoint with the native provider selected over both stock and matched backends, verify the JSON probe and honest vendor-qualified output, preserve provider failure status, and reject invalid version responses. The package archive smoke fixture now models the same JSON version contract while keeping the unselected provider as a failing trap. The authenticated released provider reports version `0.15.1` and commit `57ee265a5fbd17a1109240a0d0aa99da01acfc3a`.

Focused stock Docker client, Dev Container CLI and Compose CLI tests passed in invocation `a0531e99-4f4b-464e-b186-763abc9b91e6`. The corrected packaged executable smoke passes in stock package invocation `f96b8d9f-0c97-4b3b-beb2-d77755179de6`, checking the exact JSON arguments, vendor-qualified output, unselected-provider trap and absence of runtime state. Fresh exact-main release gates remain required.
