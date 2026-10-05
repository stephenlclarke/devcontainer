# Native parity child backend selection is lost during sanitization

## Problem

The exact `4cface96278c` full campaign selected the enhanced Q provider, but C01-C04 failed before resource creation because the signed Compose facade requested stock metadata. The lane sanitizer intentionally excludes ambient `DEVCONTAINER_BACKEND`; Engine setup restores socket, state and private configuration selections but omits backend. The facade therefore defaults to stock despite the Engine's explicit enhanced provider. GUI launch filters the backend again.

## Expected behavior and scope

Derive the child backend from the admitted native runner lane after each filter. Keep ambient operator selection excluded and preserve the facade's authoritative runtime profile and strict provider checks. Change only runner selection, focused regressions and documentation. The failed campaign and incomplete cleanup remain failed evidence; this correction does not qualify a release.
