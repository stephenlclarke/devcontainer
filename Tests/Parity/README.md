# Differential parity fixtures

The parity suite treats a pinned real Docker Engine plus the official [`@devcontainers/cli`](https://github.com/devcontainers/cli) as the behavioral oracle. Every claimed fixture must produce equivalent semantic observations through the stock Apple and container-compose lanes.

The checked-in [`manifest.json`](manifest.json) is the release contract.
`python3 Tools/parity/validate_manifest.py --release` fails closed if a fixture
or required pin is incomplete. A stable release cannot bypass the
candidate-bound runtime recordings and comparison. The runner preflight
matches the direct `@devcontainers/cli` package's npm SHA-512 integrity value
as well as its version; runtime fingerprints retain the exact tag commit and
integrity pin with every lane.

[`spec-coverage.json`](spec-coverage.json) is the north-star coverage ledger.
It maps every explicit property in the pinned Development Containers base
schema, plus lifecycle rules, to certified fixture evidence or an explicit
blocker and owner. `python3 Tools/parity/validate_coverage.py` fails when a
property is missing, duplicated, unowned, or certified without an implemented
fixture. The scheduled specification-drift workflow compares the current
upstream schema with the checked inventory and reports additions without
moving the project pin.

Each implemented CLI fixture contains:

- `.devcontainer/devcontainer.json` and any Dockerfile or Compose files;
- `contract.json`, containing the semantic assertions;
- `probe.sh`, copied into the development container to emit canonical observations;
- only public, digest-pinned images and Features;
- no credentials, private registry names, personal paths, or machine-specific state.

The V01 fixture is driven through the real, authenticated VS Code application,
the official Dev Containers VSIX, and a test-only workspace probe extension.
It records open, activation, command, port, rebuild, reopen, server, and cleanup
observations without patching the Microsoft extension.

Runtime jobs retain raw output, normalized observation JSON, JUnit results, a
Markdown matrix, backend fingerprints, cleanup evidence, and diagnostics for
every difference.

Every fixture result also records monotonic wall-clock `durationSeconds`. The comparison JSON and Markdown matrix show stock-Apple/Docker and provider/Docker ratios for the same fixture. Comparable or better performance (`<=1.00x` Docker) is the objective. A completed result above `2.50x` Docker is marked for further investigation but does not, by itself, change functional parity. A timeout, other non-completion, or missing or invalid timing evidence fails the parity gate. The harness does not retry or normalize a failed result. The full objective and investigation policy are in [`PARITY-ROADMAP.md`](../../PARITY-ROADMAP.md).
