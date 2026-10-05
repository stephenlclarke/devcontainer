# Architecture

The compatibility service listens only on a user-owned Unix socket. Docker CLI,
Docker Compose, the official Dev Container CLI, and the VS Code extension use
that socket without modification. Requests are decoded into provider-neutral
models, executed through the runtime SPI, and returned with Docker-compatible
JSON, streaming, archive, and connection-hijack behavior.

The stock adapter launches an exact Apple `container` executable without a
shell. The optional Compose adapter launches an exact `container-compose`
executable and never links its implementation into this package.

Enhanced CLI inventory preserves provider-only fields while a narrow native XPC identity read restores exact creation and process-start timestamps. Encoded dates must agree before reconciliation; terminal control still requires exact generation ownership, including same-second restarts. The decoder does not require enhanced network attachments to fit stock schemas. This internal correction is not a complete runtime-parity or stable-release claim.

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

Native process input workers own their descriptor closure. Backpressured input uses nonblocking writes, bounded readiness polls and a cancellation flag set before queueing close; task cancellation follows the same path. CLI-backed, PTY and direct-API output share one reader implementation. Cancellation-resistant joins wait for reader-owned descriptor closure and callbacks; caller-owned descriptors are relinquished without closing them. Terminal cancellation waits for the actual child exit status. This prevents cancellation from waiting forever behind a full input queue; it does not implement prepared Compose foreground attachment.

The draft direct-create adapter requires a durable `RuntimeCreationStore`. SQLite schema 4 records intent before native creation, verifies the final native incarnation, and commits successful metadata and intent removal atomically. Failed creates remain observable but cannot be launched, executed, renamed or used for archive transfers through the bridge. Ordinary inventory adoption and name-based deletion never clear the pending record. A different native incarnation may be used without erasing the unresolved operation.

Migration preserves schema-2/3 metadata; rollback to older binaries requires the quiescent pre-upgrade database backup. A safe operator reconciliation interface and live crash/restart qualification remain unfinished. This development behavior is not a stable-release or complete-parity claim.

The private Unix-socket recovery control binds cleanup to a captured process epoch and freezes new work before checking all active HTTP handlers and durable native creation intent. Frozen inspections use cached health and cannot launch probes. Streaming, attached, detached-exec or failed health completion without a reliable terminal receipt keeps recovery quarantined. C02 can record a separate fully owned partial inventory only after a nonzero CLI exit and verified command-group shutdown; each cleanup pass revalidates the epoch and resource incarnation. Neither an empty inventory nor an exited client proves that an uncertain native request cannot complete later. Live fault qualification remains outstanding.

## Stock runtime and SDK provenance

The stock runtime remains the unmodified Apple Container 1.4.1 release. Devcontainer compiles its stock-facing Container SDK from the minimal Stephen-owned derivative `aad0c75555d8ccce45aea01d7e1558eb7dee408e`, based on Apple `9a8917ca2da5cd6ba059b9ba5ca5a74892e9bb7d`. Its only production change uses a direct `ContinuousClock` deadline for the XPC timeout child to avoid the allocator crash observed in the signed package; nested dependency manifests remain unchanged. This SDK source is identified separately from the stock runtime. Foundation, Containerization and Engine API in both profiles, and the unchanged enhanced SDK, reuse their exact published archives through finite source, recipe and toolchain checks. The replacement stock SDK requires its own focused qualification, publication and downloaded admission. Full signed-package runtime qualification remains pending.

Native live readers encountering a running but not-yet-confirmed Engine-owned start join its captured start operation only for the same bound channel incarnation. The pending state excludes closed controls, native exit and completed process state; refreshed creation, Docker identity and terminal mode must match before readers retry. Only public live attachment and exit-wait readers opt in; internal start and history-only paths do not join. After the wait, cancellation, captured channel/operation registration and refreshed native running ownership are revalidated. Unmanaged running processes remain rejected. This corrects the observed replacement-control test race. Optimized regressions for all three reader APIs passed after failing against the old code; fresh signed runtime qualification remains pending.

The unreleased native I/O drain deadline also uses direct `ContinuousClock.sleep(until:)` inside its existing timeout task. It preserves the default 30-second drain interval, EOF cancellation and joining, output-error reporting and durable completion ordering. The signed d52 campaign passed E01, then crashed in E02 container lifecycle at the drain timeout child's `swift_task_dealloc`; E09 never ran. Existing I/O regressions cover successful separate-stream EOF and a retained-writer 20-ms drain failure; focused optimized I/O tests passed; fresh signed-package qualification remains pending.
