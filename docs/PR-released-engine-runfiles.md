# Fix: smoke-test the released Engine Bazel runfiles closure

## Motivation and implementation

The released Engine Python entry point imports `prepare_candidate`, which imports `candidate_archive`. The shared `released_engine_inputs` filegroup lacked that module, so the runfiles-based launcher could fail before it reached argument parsing. The filegroup now declares `candidate_archive.py`, and a small Bazel `sh_test` invokes the exact `released-engine.sh` from its runfiles with `--help`. The test checks the campaign option in parser output and never enters runtime or release-admission logic.

## Validation

A temporary clean worktree with the old `released_engine_inputs` declaration fails with `ModuleNotFoundError: No module named 'candidate_archive'`. The corrected closure passes the focused target via `Tools/bazel/run.sh test //Tools/testing:released_engine_runfiles_smoke --test_output=errors`. `bash -n`, ShellCheck, Markdown lint, and `git diff --check` pass. Only this inert help target runs; no product build or runtime qualification is performed.

## Compatibility and remaining risks

This changes test runfiles and documentation only. The released product, archive helper implementation, admission semantics, and live qualification paths are unchanged. The smoke test does not replace the existing released Engine integration target.

Linked issue: [released Engine runfiles closure](ISSUE-released-engine-runfiles.md).
