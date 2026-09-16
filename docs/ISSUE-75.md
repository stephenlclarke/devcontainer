# Docker-free Development Containers on Apple container

## Problem

VS Code and the official `@devcontainers/cli` currently expect a
Docker-shaped command and Engine API boundary. Apple provides the
[`container`](https://github.com/apple/container) runtime and the
[`containerization`](https://github.com/apple/containerization) framework, but
it does not provide Docker Engine, Docker Compose, or a Compose plug-in for
`container`.
Using a host Docker daemon or forwarding its socket would violate this
project's Docker-free requirement.

The integration must also remain useful with the separately distributed
enhanced `stephenlclarke/container` runtime and native
[`container-compose`](https://github.com/stephenlclarke/container-compose)
without making either one a prerequisite for stock Apple users. The official
Dev Containers parser and lifecycle implementation must remain unmodified so
that compatibility is measured against maintained upstream behaviour rather
than a fork.

## Required outcome

Provide an independently packaged compatibility layer that:

- runs the pinned official `@devcontainers/cli` and VS Code Dev Containers
  extension without installing, discovering, invoking, proxying, or mounting
  Docker software;
- translates the certified Docker CLI and Engine API subset directly to an
  unmodified stock Apple `container` runtime;
- uses a private, process-isolated stock-profile `container-compose` provider
  for multi-service projects while keeping the runtime-neutral core independent
  of Compose implementation modules;
- accepts the enhanced Container distribution only through explicit,
  fingerprinted selection;
- fails closed for protocol fields and runtime semantics it cannot enforce;
- records every standards non-conformance rather than implying full
  specification coverage;
- compares every certified fixture with a real Docker oracle, including raw
  monotonic timing, while treating only non-completion or a result at least ten
  times slower as timing-gate failure;
- ships a signed, notarized, checksummed, SBOM-covered archive and a Docker-free
  Homebrew formula;
- publishes the user guide, architecture, compatibility, conformance, testing,
  performance, quality, and release material as DocC documentation.

## Acceptance criteria

1. All 19 CLI fixtures and the real VS Code fixture have zero normalized
   semantic differences in the real-Docker, stock-Apple, and enhanced-provider
   matrix.
2. Stock mode uses unmodified Apple `container` 1.4.1 and
   `containerization` 0.45.0.
3. The optional provider lane is bound to an immutable stable
   `container-compose` release and exact enhanced Container-family commits.
4. Product paths reject Docker-family executables, Docker socket names and
   aliases, unrecognized runtime provenance, unknown mutating request fields,
   and unsupported semantics before side effects.
5. Unit, contract, integration, sanitizer, static-analysis, documentation, and
   package gates pass; first-party and changed executable-line coverage are at
   least 90 percent.
6. The release archive contains complete legal notices and SPDX inventories for
   the main Swift graph, the bundled provider's Swift graph, its vendored Go
   graph, and the linked Go standard library.
7. The exact reviewed commit is published as stable `1.0.2`, installs from
   `stephenlclarke/tap/devcontainer` without Docker, and reports matching build
   provenance after installation.

## References

- [Development Containers organization](https://github.com/devcontainers)
- [Development Containers Specification](https://github.com/devcontainers/spec)
- [Official Dev Containers CLI](https://github.com/devcontainers/cli)
- [VS Code Dev Containers documentation](https://code.visualstudio.com/docs/devcontainers/containers)
- [Detailed architecture](../DESIGN.md)
- [Standards conformance ledger](../CONFORMANCE.md)
- [Parity and performance roadmap](../PARITY-ROADMAP.md)

## Tracking

Implementation and release evidence are tracked by
[pull request 75](https://github.com/stephenlclarke/devcontainer/pull/75).
