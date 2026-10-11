# Keep the isolated VS Code session alive during a slow rebuild

## Problem

The `d9d89ecc` campaign passed all 81 CLI fixtures and the Docker and stock VS Code lanes, but the container-compose VS Code lane exited with status 0 during `remote-containers.rebuildContainer`. The driver had already observed the first attach, terminal command, forwarded port, and VS Code server. The official extension then removed the first container and was still starting its replacement when the isolated GUI exited and the remote authority resolver reported `Canceled`. No driver result was written; the original failed campaign remains retained.

## Expected behavior

The test driver should keep the same isolated VS Code application alive while the official extension closes and reopens its working window for rebuild. A private local anchor window must not read or write driver state, mark any of the eight required observations, run fixture commands, or mask unexpected GUI exit. The existing phase deadline and exact-profile cleanup remain required.

## Validation

Focused Python tests cover anchor path selection, exact-profile cleanup, and early GUI exit failure. Focused Node tests cover exact anchor matching, state-free anchor activation, separate-window opening, and explicit opening failure. A fresh live V01 lane and then the maintained full campaign remain necessary to establish parity.

Tracked in [issue 99](https://github.com/stephenlclarke/devcontainer/issues/99).
