# PR: prepare enhanced SwiftPM checkouts with the reviewed dependency patches

## Motivation

Hosted source checks selected the enhanced dependency commits but did not receive the three patches already used by Bazel. The missing Engine API recovery declarations prevented those checks from compiling. This change makes both paths use the same reviewed patch bytes.

## Implementation

`Tools/ci/prepare-swiftpm-dependencies.py` authenticates the selected patch recipients and their real SwiftPM checkout-to-bare-mirror-to-upstream chain. It derives expected output from pristine pinned Git blobs, preflights all recipients before mutation, and permits idempotence only for the exact patch result. Unexpected staged, ignored, unrelated or partial changes fail closed; rollback is limited to the helper's own exact patch application. An empty scratch tree may resolve once without building; a partially populated recipient set requires explicit resolution.

Make build, test, coverage, sanitizer, documentation, demo and unsigned development packaging targets prepare the scratch location they actually consume. DocC preparation shares `DOCS_SCRATCH_PATH` with its existing script. Signed packaging bypasses source preparation and continues to require the independently finalized native archive. The explicit `resolve` target remains resolution-only. CodeQL prepares the default scratch tree before its direct dependency-priming build; its later first-party extraction reuses that tree.

## Validation

All 21 focused tests pass under Python 3.9 and 3.12, including rejection of mode-only changes to existing and patch-added files. Preparation against the existing real `.build` mirrors succeeds and repeat invocation with the mode checks is idempotent, without running a second local SwiftPM compile/test campaign. Independent review confirmed Make ordering, scratch matching, exact patch hashes and mirror admission; the identified file-mode gap is corrected by checking the exact patched index mode as well as file bytes. Hosted SwiftPM compilation remains pending until the coherent review branch is pushed.

## Compatibility and remaining risks

Stock is a no-op only when the active resolved lock equals the authoritative stock lock and its expected top-level selections. The helper binds the three patch recipients; full graph admission remains the responsibility of existing source-graph and release checks. Lower release recipes and old benchmark evidence are unchanged. No final-head quality, signing, live parity or stable-release claim follows from these focused checks.

Linked issue: [SwiftPM patch delivery](ISSUE-swiftpm-dependency-patches.md). Integration review: [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
