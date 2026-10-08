# Parity qualification source admission

## Problem

The parity workflow used `github.sha` as both the workflow tooling identity and the product source identity. The immutable 1.1.0 qualification is bound to tagged product source `297c9dde27f6519d6ba5b02314622e4a21c88702`, while the current workflow tooling commit is `e4b686d337caccd0ac90ef1ae75aea6d88f3bf1d`. The strict verifier correctly rejects the mismatch because it validates the exact product checkout and source tree.

## Required result

Authenticate `qualification.json` against its independently configured SHA before extracting `sourceCommit`. Compare a fixed native source, fixture, build and runtime-harness closure between the qualified product source and workflow tooling commit. Reject native drift, then run the unchanged strict verifier in a separate clean checkout at the authenticated product source. Preserve the workflow commit as tooling identity and the receipt commit as product identity.

## Validation

Exercise the actual workflow admission script in a temporary Git repository. Prove native source drift fails, workflow/documentation/`Tools/ci/test_workflow_artifacts.py` changes pass, and a receipt whose bytes do not match the trusted SHA fails. Run the original strict verifier against the retained 1.1.0 qualification without repeating runtime or release work.
