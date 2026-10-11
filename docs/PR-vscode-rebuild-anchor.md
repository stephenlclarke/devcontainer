# PR: keep VS Code alive through the rebuild transition

## Summary

Before issuing the official `remote-containers.rebuildContainer` command, the parity driver opens a private local folder in a second window of the same isolated VS Code profile. The driver ignores only that exact local anchor folder before accessing driver state or running commands. Existing profile-scoped cleanup closes the anchor with the rest of the isolated application.

## Validation

The pinned VS Code 1.131.0 build already defaults `window.closeWhenEmpty` to false, while the documented `--wait` flag concerns CLI file lifetime; neither keeps a window open across this explicit rebuild transition. The supported `vscode.openFolder` command with `forceNewWindow` provides the narrow window boundary. All 319 parity Python tests (including the three Node anchor checks), and JavaScript syntax checks pass. No live VS Code run or product build was performed for this patch.

## Compatibility and risk

The official extension, signed native package, fixtures, eight required observations, 60-second phase deadline, unexpected-exit failure, and exact-profile cleanup are unchanged. The anchor is a harness-only empty folder and never counts as a parity observation. The original `d9d89ecc` failure remains retained; live V01 and full-campaign acceptance are pending.

## Related work

This corrects the VS Code transition exposed after Docker builder admission was fixed. It does not alter the Docker builder or native runtime products.

Closes [issue 99](https://github.com/stephenlclarke/devcontainer/issues/99).
