# Architecture

The compatibility service listens only on a user-owned Unix socket. The bundled
`devcontainer-docker` adapter, official Dev Containers CLI, and VS Code extension
use that Docker-shaped protocol surface. Requests are decoded into provider-neutral
models, executed through the runtime SPI, and returned with Docker-compatible
JSON, streaming, archive, and connection-hijack behavior.

The stock adapter launches an exact Apple `container` executable without a shell. The Compose dispatcher launches an exact native `container-compose` executable and never links its implementation into this package. No product path launches a Docker CLI, Docker Compose, Docker Desktop, or Docker daemon. Runtime overrides naming Docker or Colima and bind mounts resolving to a Docker daemon socket fail before launch or container creation.

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
