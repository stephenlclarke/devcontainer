# Pull request handoff: isolate Compose exception assertions

## Change

Three Compose foreground exception assertions now receive a guest constructed before the assertion. Only the readiness operation can satisfy the expected exception. Production behavior is unchanged.

## Validation

The focused Compose foreground suite passes 52 tests. Markdown lint for these handoff documents and `git diff --check` pass. A fresh SonarCloud scan must confirm that the three findings are resolved; no finding is waived.

## Compatibility and remaining work

The expected deadlines, CLI exit and journal assertions remain intact. The release still requires the local gates, signing, runtime qualification and publication for the new source revision.

Related to [the matching issue handoff](ISSUE-compose-exception-test-isolation.md).
