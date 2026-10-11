# PR: parse the pinned BuildKit version field

## Summary

Docker Buildx prints a column-aligned `BuildKit version:` field. The Docker parity builder now trims only that field's surrounding whitespace and requires exactly one field equal to the pinned version. It still rejects a missing, duplicate, wrong, or extra-token value. Builder creation, ownership, cleanup, and fixture timing are unchanged.

## Validation

The exact padded line from the failed `8c800d17` campaign fails before this patch and passes after it. The focused parity suite passes 187 tests. No Docker runtime or new full campaign was run for this patch.

## Compatibility and risk

The BuildKit image digest and required daemon version remain pinned. A fresh full campaign is needed to establish release timing and parity; the prior campaign remains failed evidence.

## Related work

Closes [issue #96](https://github.com/stephenlclarke/devcontainer/issues/96). The cache-isolation change is tracked in [issue #94](https://github.com/stephenlclarke/devcontainer/issues/94).
