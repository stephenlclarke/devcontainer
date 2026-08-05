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

## Current C04 revalidation blocker

On 5 August 2026, the isolated C04 Docker oracle reached `devcontainer up` and
received a valid `remoteWorkspaceFolder`, but the parity runner's probe exited
before it could record lifecycle observations because the official CLI did not
execute `./probe.sh` from that remote workspace. Signed local checkpoint
`56f20056c88eb9fa2acd857ce375adf84e44d59b` records the initial
remote-workspace transition and option-boundary correction with 16 focused
runner tests passing. The live C04 invocation still emits
`/bin/sh: can't open './probe.sh'`; therefore this is a current harness blocker,
not new evidence about the Compose alias/recreate implementation.

The isolated evidence root retains the Docker Compose 5.3.1 SHA-256 match,
current Docker endpoint fingerprint, digest-pinned Alpine fixture, raw `up` and
probe logs, and marker-protected cleanup boundary. Do not reclassify the
historical C04 runtime gap or claim fresh C04 parity until
[devcontainer #46](https://github.com/stephenlclarke/devcontainer/issues/46)
has a source-backed exec-forwarding correction and the exact C04 fixture runs
through all lifecycle assertions.

## Tracking

- GitHub issue: [#40](https://github.com/stephenlclarke/devcontainer/issues/40)
- Independent compose blocker:
  [container-compose #184](https://github.com/stephenlclarke/container-compose/issues/184)
