# Fix: authenticate terminal launcher assets in the package smoke check

## Motivation and implementation

The archive smoke test now expects the two terminal helper executables and their Go license. It authenticates their bytes against the candidate receipt, checks both ELF architectures and rejects an ELF interpreter. Existing strict extraction bounds, ownership, path and mode checks remain in place.

## Validation

The original stock package gate fails before this change. All 13 corrected smoke tests pass against the same archive, without a rebuild. Fresh committed stock and enhanced package smoke gates are required before signing and release.

## Compatibility and risks

This changes archive validation only. It does not execute Linux helpers on macOS or alter candidate bytes. Related issue: [terminal package smoke](ISSUE-terminal-package-smoke.md).
