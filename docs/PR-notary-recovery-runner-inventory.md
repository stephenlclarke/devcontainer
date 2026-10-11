# PR: include notarization recovery in the runner inventory

## Change

Replace the stale numeric count with the exact four workflow/job identities: parity verification, prebuilt package publication, notarization identity verification and notarization profile recovery. Each still requires the designated MBP label.

## Validation

The focused inventory regression passes. All 76 `Tools/ci` tests pass.

## Compatibility and risk

Test-only change; no runtime, secrets or runner selectors change. Missing or unexpected self-hosted jobs fail the inventory check.

Closes [issue 98](https://github.com/stephenlclarke/devcontainer/issues/98).
