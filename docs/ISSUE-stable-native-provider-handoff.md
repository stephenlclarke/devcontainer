# Issue: bind native qualification to current stable providers

## Motivation and required behaviour

The current Compose/Q release can differ from the older release copied into the stable runtime slot. Qualification must not run a current finalized package through a stale executable, mix API and CLI binaries from different roots, or silently repair the slot. Admit the exact locked stock release and complete signed Compose/Q pair, require an explicit activation receipt for each current release, and use the resulting stable projection consistently for API, CLI, and provider-helper paths. Keep the separately signed Compose frontend and immutable prepared-package evidence independently authenticated.

Before starting Docker fixtures, qualification also needs a bounded startup/readiness check for each active native API and must restore host services and temporary keychains. Any failed, ambiguous, or incompletely restored startup stops before Docker work and retains private evidence. The sealed qualification must bind sanitized active/source/inventory proof and preflight restoration receipts. Historical replay must use the sealed CAS and immutable prepared releases, since a later legitimate activation may replace the once-active slot. Stable-path activation is file staging only; neither activation nor this preflight supplies macOS privacy authorization.

## Validation and compatibility

Focused regressions cover the stale Compose slot, mixed runtime paths, active receipt identity, active helper bytes checked against the original prepared inventory, service restoration after readiness, startup failure retention, keychain deletion and restore failures, and ordering before Docker. Live runtime qualification, host authorization, final 84-observation parity, and release admission remain separate gates. See [implementation](PR-stable-native-provider-handoff.md).
