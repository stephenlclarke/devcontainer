# Issue: upper terminal build additions invalidate unchanged lower releases

## Problem

The initial-terminal-size fix adds upper runtime and packaging targets to shared Bazel files. Lower Swift layer receipts include those shared file hashes, so the unchanged stock and fork releases fail their recipe comparison even though their selected source revisions and compilation rules remain unchanged.

## Required behavior

Reuse only the exact four stock and four current fork layer archives. Require the reviewed shared build snapshot, Go SDK version input, patch bytes, selected module-rule digest, unchanged production AST, exact canonical layer lock, source pins and lower receipts. Reject unrelated edits and changed lower inputs. Retain the actual current build inputs and graph evidence rather than substituting historical source records.

## Validation

The original verifier fails all eight new consumer regressions. The corrected verifier passes all 25 focused tests on Python 3.12, including seven shared-input mutation cases against all eight layer selections and the existing lower-source, recipe and header checks. Full source-quality checks pass. Actual compiled-consumer release gates remain pending.
