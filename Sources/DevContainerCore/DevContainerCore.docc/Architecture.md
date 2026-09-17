# Architecture

The compatibility service listens only on a user-owned Unix socket. Docker CLI,
Docker Compose, the official Dev Container CLI, and the VS Code extension use
that socket without modification. Requests are decoded into provider-neutral
models, executed through the runtime SPI, and returned with Docker-compatible
JSON, streaming, archive, and connection-hijack behavior.

The stock adapter launches an exact Apple `container` executable without a
shell. The optional Compose adapter launches an exact `container-compose`
executable and never links its implementation into this package.

Project provider claims are durable and immutable while resources exist. This
prevents stock and custom runtime operations from creating split-brain projects.
The dispatcher classifies the complete supported Compose global-option surface
before execution and uses the selected provider's canonical configuration output
when explicit project identity is absent.

Archive uploads preserve member permission bits independently of the host service's file-creation mask. Validated tar data is extracted beneath an untouched private `0700` parent, so an archive's root-directory mode cannot expose host staging. Both native and CLI upload paths use the extracted child. This staging guarantee does not by itself establish complete archive metadata parity.

The stock adapter also supports coordinated identity/lifecycle handoff for
stopped containers. An atomic quiescence check rejects running containers,
active execs, starts, and concurrent lifecycle mutations before exporting the
canonical name, Docker identifier, immutable Apple bundle key, provider
fingerprint, and stopped-state snapshot. Event history is empty because the
legacy polling source cannot prove a durable journal.

For the complete diagrams and decisions, see the repository
[software design](https://github.com/stephenlclarke/devcontainer/blob/main/DESIGN.md).

## Development native-create recovery

The draft direct-create adapter requires a durable `RuntimeCreationStore`. SQLite schema 4 records intent before native creation, verifies the final native incarnation, and commits successful metadata and intent removal atomically. Failed creates remain observable but cannot be launched, executed, renamed or used for archive transfers through the bridge. Ordinary inventory adoption and name-based deletion never clear the pending record. A different native incarnation may be used without erasing the unresolved operation.

Migration preserves schema-2/3 metadata; rollback to older binaries requires the quiescent pre-upgrade database backup. A safe operator reconciliation interface and live crash/restart qualification remain unfinished. This development behavior is not a stable-release or complete-parity claim.
