# Issue: close final-source analysis findings before release integration

## Motivation and required behaviour

The `f63253e` PR checkpoint compiles on GitHub and passes Swift coverage, but its Sonar clean-project check reports seven maintained-source findings and two guarded launcher-case findings. The release requires a clean analysis; compilation and component coverage alone are insufficient.

Keep transport lock, descriptor ownership, cancellation, deadline and response-error behaviour unchanged while explaining the intentional diagnostic callback and reducing closure nesting. Split the Python loader precondition and prepare test payloads outside exception assertions, preserving the exact tested operation and expectations.

## Analysis boundary and validation

The two launcher cases are guarded by `protected_define_key`, whose current domain contains exactly four keys. Both cases exhaust that domain; unknown keys never enter them. Independently review that control flow and run the existing joined/split long/short flag matrix before classifying these two findings. Do not suppress the rule or change archive recipes merely to silence analysis. New protected keys require both rejection branches and the test matrix to change together.

Focused Docker-client and Python regressions, strict source formatting/lint, and a fresh exact-head Sonar check are required. Final exact-main QA, packaging and runtime qualification remain separate gates. See [the implementation handoff](PR-final-source-quality.md) and [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).

The subsequent [PR 88](https://github.com/stephenlclarke/devcontainer/pull/88) analysis at `3a482213` passed coverage and scan setup but reported three exception-context findings and one boolean-assertion finding in the candidate-lane and feature-cache tests. Prepare argument namespaces and monotonic timestamps before the exception context so only the expected failing operation can satisfy the assertion. Preserve the original error messages, cache proof and no-overwrite contract.

Running the complete validation with private evidence permissions also exposed an ambient-umask dependency in the synthetic signing fixture: its Go license inherited `0600` rather than the production-required `0644`. This caused 25 failed assertions and 12 fixture errors before the intended signing operations. Set the fixture's required mode explicitly without changing the production verifier.

The next private-umask pass exposed nine preparation errors: Git's patch application recreates tracked files with the caller's restrictive permissions, so correct patched bytes fail the reviewed `0644` mode check. Correct only the patch subprocess's umask, preserving the parent's private permissions, exact patch hashes, origin checks and rejection of altered output.
