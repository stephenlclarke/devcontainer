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

The stock adapter also supports coordinated identity/lifecycle handoff for
stopped containers. An atomic quiescence check rejects running containers,
active execs, starts, and concurrent lifecycle mutations before exporting the
canonical name, Docker identifier, immutable Apple bundle key, provider
fingerprint, and stopped-state snapshot. Event history is empty because the
legacy polling source cannot prove a durable journal.

For the complete diagrams and decisions, see the repository
[software design](https://github.com/stephenlclarke/devcontainer/blob/main/DESIGN.md).
