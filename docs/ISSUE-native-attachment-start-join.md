# Running attachment can race Engine-owned start confirmation

The exact-main 2c098de stock-host gate recorded 318 runtime tests with a failure in `replacement joins old controls without crossing resolved identity`, `replaced=false`, at AppleContainerAttachmentTests.swift:278. Its caught error was `Live attachment requires a verified process owned by this engine`. Preserve the retained failed stdout and gate; earlier passing coverage does not waive this failure.

After old I/O controls join, native start can publish running state before confirmContainerProcess/didStart publishes the channel's verified startedAt. A concurrent reader therefore rejects a genuinely pending Engine-owned start. Shared prepareContainerIO is also used by start itself, so indiscriminately awaiting containerStartOperations would permit self-await.

Allow only a same-incarnation bound channel with startAttempted, no verified startedAt, open controls and no native/completed exit to join the captured registered start. Start's own preparation occurs before reserveStart, excluding self-await. Reinspect/revalidate creation/Docker identity/terminal mode and creation completion afterward; keep unmanaged running rejection unchanged.

Reader-only opt-in defaults off in shared preparation. Public attach, live attachment preparation and exit-wait registration opt in. After joining, caller activity, runtime ID, creation date, Docker identity, terminal mode, captured channel, operation registration (completed nil allowed) and verified running start ownership are checked. Start errors propagate without cancelling the shared operation.
