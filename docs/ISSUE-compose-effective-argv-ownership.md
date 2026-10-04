# Issue: distinguish Compose inspect command layout from guest identity

## Problem

The exact-source E13 component campaign on `e87861223828b450280b6a97be53a03f08b6b456` reached a real guest that emitted foreground output, then failed the strict guest ownership check before its ID could be recorded. Its inspect payload was not retained, so the historical failed field is unverified. Source tracing identifies a deterministic incompatibility: the fixture compared all requested arguments with `Config.Cmd`, while Q's inspect response projects the first executable into `Config.Entrypoint` and the remainder into `Config.Cmd`.

## Required outcome

Authenticate the same exact requested executable and arguments under either of those two Docker representations. Continue to reject an extra or altered entrypoint, altered command, ID, name, owner label or image. Preserve the original failed campaign and require a fresh live E13 result for qualification.

Tracked with [pull request 83](https://github.com/stephenlclarke/devcontainer/pull/83).
