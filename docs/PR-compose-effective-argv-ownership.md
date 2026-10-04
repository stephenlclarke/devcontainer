# Pull request handoff: authenticate effective Compose guest arguments

## Change

The guest ownership check admits only a complete `Config.Cmd` with absent or empty `Config.Entrypoint`, or a single entrypoint equal to the requested executable with `Config.Cmd` exactly equal to the remaining requested arguments. It retains strict ID shape, name, owner label and both image checks. On rejection, boolean-only diagnostic fields identify which check failed without recording raw labels, image values or commands.

## Validation

Focused Python guest and Compose foreground suites pass 79 tests. Cases include the Q-shaped split for the E13 shell command, exact cleanup of an owned split-command guest, full-command mode, and rejection of changed entrypoint, command, name, valid-but-changed ID at journal cleanup, owner label and image. No live runtime or E13 campaign was run for this patch.

## Compatibility and remaining work

The effective process arguments and all identity requirements remain unchanged. This source-level correction does not establish which field caused the historical failure because that inspect response was not retained; it also does not claim complete Docker inspection metadata parity. A reviewed new source head and fresh signed-package parity campaign remain required.

Related to [pull request 83](https://github.com/stephenlclarke/devcontainer/pull/83) and [the matching issue handoff](ISSUE-compose-effective-argv-ownership.md).
