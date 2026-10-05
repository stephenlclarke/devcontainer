# PR: add Docker CLI image pull support

## Summary

Implement the observed `devcontainer-docker pull IMAGE` command through a private constant for the fixed Engine image-create path and a separately encoded `fromImage` query. The adapter keeps the full tag or digest reference intact, streams bounded progress to stdout, validates and discards rendered progress in quiet mode, and reports transport or in-band Engine failures as command failures. The protocol endpoint is not user-configurable. Unsupported flags and multiple image references remain explicit usage errors.

## Focused coverage

- Parse a digest-pinned image reference and preserve its encoded `fromImage` value.
- Stream progress records and suppress them under `--quiet` while still consuming the response.
- Propagate Engine stream errors and reject unsupported flags, missing references, and extra references.

Focused stock Bazel client and CLI targets pass (invocation `e465bc49-0179-4064-b581-a8f44b2c6b3c`), including final progress records, malformed and empty streams, deadlines, cancellation, and the nonstreaming execution guard. The real executable and Unix transport also pass private-socket pull, quiet, and streamed-error regressions. The coverage harness retains the instrumented child profile and exports the Docker adapter binary. Formatting, focused static analysis, shell checks, and all 17 coverage-tool tests pass. The fix addresses the common `pull` failure seen across the failed stock lifecycle fixtures; release compatibility remains unverified until the release executor reruns them.
