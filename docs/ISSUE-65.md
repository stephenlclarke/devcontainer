# Pin the merged lifecycle identity boundary

## Problem

Devcontainer's lifecycle authority handoff merged in [pull request 62](https://github.com/stephenlclarke/devcontainer/pull/62), but its checked-in Container dependency predates atomic lifecycle discovery and the native inspect correction. Keeping that older revision would leave devcontainer on a different runtime identity contract from the Compose 0.12.0 matched stack.

## Expected behavior

- `Package.swift` and `Package.resolved` name the same immutable Container revision.
- The selected revision includes the merged lifecycle discovery and inspect identity support.
- Devcontainer preserves one stable lifecycle identity when adopting Container records.
- No Compose code is imported into the runtime-neutral core.

## Resolution

Pull request [65](https://github.com/stephenlclarke/devcontainer/pull/65) pins Container merge revision `e76a28de2dcf2c3650871d8e5240d41d6a36cf12`. The resolved package graph accepts that exact revision and all eight focused identity/lifecycle handoff tests pass.

## Tracking

- Devcontainer pull request: [#65](https://github.com/stephenlclarke/devcontainer/pull/65).
- Container dependency: [stephenlclarke/container#135](https://github.com/stephenlclarke/container/pull/135).
- Parent parity issue: [stephenlclarke/container-compose#274](https://github.com/stephenlclarke/container-compose/issues/274).
