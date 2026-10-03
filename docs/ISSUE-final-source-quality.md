# Issue: close final-source analysis findings before release integration

## Motivation and required behaviour

The `f63253e` PR checkpoint compiles on GitHub and passes Swift coverage, but its Sonar clean-project check reports seven maintained-source findings and two guarded launcher-case findings. The release requires a clean analysis; compilation and component coverage alone are insufficient.

Keep transport lock, descriptor ownership, cancellation, deadline and response-error behaviour unchanged while explaining the intentional diagnostic callback and reducing closure nesting. Split the Python loader precondition and prepare test payloads outside exception assertions, preserving the exact tested operation and expectations.

## Analysis boundary and validation

The two launcher cases are guarded by `protected_define_key`, whose current domain contains exactly four keys. Both cases exhaust that domain; unknown keys never enter them. Independently review that control flow and run the existing joined/split long/short flag matrix before classifying these two findings. Do not suppress the rule or change archive recipes merely to silence analysis. New protected keys require both rejection branches and the test matrix to change together.

Focused Docker-client and Python regressions, strict source formatting/lint, and a fresh exact-head Sonar check are required. Final exact-main QA, packaging and runtime qualification remain separate gates. See [the implementation handoff](PR-final-source-quality.md) and [PR 83](https://github.com/stephenlclarke/devcontainer/pull/83).
