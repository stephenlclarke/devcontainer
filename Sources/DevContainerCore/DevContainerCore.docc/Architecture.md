# Architecture

The compatibility service listens only on a user-owned Unix socket. The bundled
`devcontainer-docker` adapter, official Dev Containers CLI, and VS Code extension
use that Docker-shaped protocol surface. Requests are decoded into provider-neutral
models, executed through the runtime SPI, and returned with Docker-compatible
JSON, streaming, archive, and connection-hijack behavior.

`DOCKER_HOST` and VS Code's `dockerPath` are compatibility field names required
by unmodified upstream clients. They point only to the project-owned
`engine.sock` and adapter, never to Docker software or a Docker daemon socket.
Explicit socket configuration rejects Docker and Docker Desktop socket names,
including symlink aliases, before any connection attempt. The adapter then
requires a project-specific identity response from `devcontainer-engine` before
its first workload request, rejecting a foreign Docker-compatible endpoint even
when it has been assigned another socket filename.

The stock adapter launches an exact Apple `container` executable without a shell. The configured runtime path and its resolved symlink target must both be named `container`, and its version record must identify stock `apple/container` or the explicit `stephenlclarke/container` distribution. Foreign custom distributions and Docker-named backend or Compose-provider values fail before project work. The Compose dispatcher launches an exact native `container-compose` executable and never links its implementation into this package. No product path launches a Docker CLI, Docker Compose, Docker Desktop, Docker daemon, Colima, Podman, or nerdctl. Runtime overrides naming another container runtime and bind mounts resolving to a Docker daemon socket fail before launch or container creation. Every product child-process launch passes through one shared policy that checks both the selected name and resolved symlink target; the source audit rejects direct launch APIs outside that runner.

Release archives make the Compose process boundary auditable. The bundled stock provider includes its exact SwiftPM lockfile, vendored Go module build list, complete dependency legal texts, and a dedicated SPDX 2.3 document covering the Swift packages, Go modules, and Go standard library compiled into it.

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
