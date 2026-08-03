# Issue 40: bound and clean up the private provider socket path

## Problem

The shared Engine gateway originally derived its private provider-session socket
from the state database directory. Long GitHub Actions workspace paths can exceed
Darwin's Unix-domain socket path limit before the compatibility service begins
serving requests.

Signed main head `4fa4144c9311183a4120e242bcb7540646733a05` reproduced the
failure in runtime-parity run
[30850425639](https://github.com/stephenlclarke/devcontainer/actions/runs/30850425639):

```text
Error: invalid provider Unix socket path: .../.build/parity/runtime/apple-stock/engine-provider.sock
```

The first short-path implementation also exposed a filesystem hygiene problem:
the provider transport intentionally retains its lock file, so unique parity
socket paths left owner-only directories under `/private/tmp` after shutdown.

## Expected behavior

- Derive a deterministic, collision-resistant private provider socket whose
  length does not depend on the workspace or state path.
- Keep the path below Darwin's 104-byte `sockaddr_un.sun_path` capacity.
- Preserve owner-only directory, socket, selection, and lock-file protections.
- Remove only the expected lock file and private directory after the provider
  has shut down; fail closed around live, unsafe, linked, or unexpected entries.
- Preserve all public Docker API and provider-selection behavior.
- Run against the protocol-v3 Engine dependency required for chunked large
  request bodies.

## Acceptance evidence

- The private path is a 96-bit SHA-256 prefix of the public socket beneath an
  owner-scoped short directory.
- Focused service tests cover deterministic length, secure filesystem modes,
  expected cleanup, live-socket rejection, unsafe-lock rejection, unexpected
  artifact preservation, and both service subprocess modes.
- `make check` passes 202 Swift tests in 18 suites, 95.38% first-party line
  coverage, all Python tooling suites, formatting, strict lint, shell and
  workflow checks, documentation generation, and parity-manifest validation.
- The final stock Apple runtime lane passes all 18 fixtures with no cleanup
  differences.
- The final stable container-compose lane passes 17 of 18 fixtures, including
  `D05-features` with a large protocol-v3 image load and all four observations.
  Its only failure is the independent `C04-compose-lifecycle` alias/recreate
  limitation tracked in container-compose issue 184.
- No `devcontainer-engine-*` directory remains under `/private/tmp` after either
  final Apple lane.

## Tracking

- GitHub issue: [#40](https://github.com/stephenlclarke/devcontainer/issues/40)
- Independent compose blocker:
  [container-compose #184](https://github.com/stephenlclarke/container-compose/issues/184)
