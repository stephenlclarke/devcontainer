# Accept Buildx column spacing in BuildKit version admission

## Problem

The exact-owned Docker builder booted during the `8c800d17` full parity campaign, but Buildx printed `BuildKit version:      v0.30.0` with column padding. The admission check required one literal space and rejected the pinned version before any fixtures ran. The failed campaign and its successful exact-owned builder cleanup remain retained.

## Expected behavior

Accept one `BuildKit version:` field whose trimmed value exactly matches the pinned BuildKit version. Reject a missing, duplicate, different, or extra-token version. Keep the builder image pin, cache ownership, fixture scope, timing gate, and cleanup checks unchanged.

## Validation

The actual padded output failed the focused regression before the fix and passes afterward. Focused tests also reject malformed version fields. A new full runtime campaign is required; this source fix does not make the failed campaign pass.
