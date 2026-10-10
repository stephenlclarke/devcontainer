# Issue: cover changed init-I/O diagnostics in source analysis

## Motivation and required behaviour

PR 88's hosted Sonar workflow failed after its SonarCloud Quality Gate passed. The repository's changed-line guard found 12 uncovered lines in `AppleContainerIODiagnostics`, and Sonar reported `python:S5778` in `test_loaded_image_cannot_substitute_for_selected_effective_guest`. Keep the diagnostic behavior and test assertions intact while making every new trace branch observable in a focused regression and isolating the one potentially throwing assertion operation.

## Analysis boundary and validation

Cover failed input writes, input EOF request/completion, stdout and stderr EOF accounting, process exit, and both drain snapshot paths. The exception assertion must contain only `verify_provider_configuration`; fixture extraction and mutation belong before it. Do not suppress Sonar findings, weaken the changed-line guard, or modify release gates. Revalidate the focused Apple runtime and Python tests, formatting, and a fresh exact-head hosted analysis. See [the implementation handoff](PR-sonar-diagnostic-test-coverage.md) and [PR 88](https://github.com/stephenlclarke/devcontainer/pull/88).
