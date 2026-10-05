# Issue: native archive smoke check rejects terminal launcher assets

## Problem

The signed engine now requires two packaged Linux terminal launchers and their Go license. The unsigned archive smoke check still expects the old layout and rejects these required files before testing the package.

## Required behavior

Admit only the two declared architecture-specific launchers and their license. Require executable modes, exact receipt hashes, matching ELF architectures and no dynamic ELF interpreter. Continue rejecting unexpected paths, duplicates, unsafe ownership and missing files.

## Validation

The exact-main stock package gate fails with `Duplicate or unexpected archive path` before this correction. All 13 corrected smoke tests pass against the same stock archive without rebuilding it. Fresh committed stock and enhanced package gates and full release checks remain required before publication.
