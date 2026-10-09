# Issue: include the candidate archive helper in released Engine runfiles

## Problem

The Bazel `released_engine_inputs` filegroup omitted `Tools/bazel/candidate_archive.py`, even though `released_engine.py` imports `prepare_candidate.py`, which imports that module. A Bazel-launched released Engine case therefore failed during Python imports before argument parsing or qualification began.

## Required behavior

- Declare the archive helper in the exact runfiles group used by the released Engine launcher.
- Exercise the real `released-engine.sh` entry point from a Bazel test runfiles tree.
- Use only `--help`, which verifies imports and parser wiring without selecting a lane, starting a runtime, or accessing release assets.
- Keep product and runtime qualification separate from this inert harness smoke test.

## Validation

A temporary clean worktree using the original `released_engine_inputs` closure failed the smoke test with `ModuleNotFoundError: No module named 'candidate_archive'`. The maintained workspace passes the same test after the filegroup declares the helper. The exact test command is `Tools/bazel/run.sh test //Tools/testing:released_engine_runfiles_smoke --test_output=errors`. No product build or live runtime test is needed for this regression.

## Scope and remaining risk

This corrects only Bazel test data closure. It does not change released product bytes, candidate validation behavior, or runtime qualification. Full released Engine integration remains covered by its existing separately gated targets.

Linked implementation: [released Engine runfiles smoke test](PR-released-engine-runfiles.md).
