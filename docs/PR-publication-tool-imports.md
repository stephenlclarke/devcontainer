# Fix publication tool imports

## Result

The pinned publication checkout selects Tools as a complete directory, including the transitive Bazel input helpers used by runtime service inspection. The runner identity preflight constructs the real service adapter without issuing service or installation operations, catching missing imports before publication work.

## Validation and compatibility

Reproduce the sparse checkout in an isolated local repository and execute the same adapter construction. Run workflow lint, Markdown lint and source hygiene checks. The production archive, signed 1.1.0 tag, runtime qualification, immutable asset inventory and Homebrew restoration transaction are unchanged; a real passed-restored installation receipt is still required for GA promotion.
