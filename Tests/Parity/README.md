# Differential parity fixtures

The parity suite treats a pinned real Docker Engine plus the official [`@devcontainers/cli`](https://github.com/devcontainers/cli) as the behavioral oracle. Every claimed fixture must produce equivalent semantic observations through the stock Apple and container-compose lanes.

The checked-in [`manifest.json`](manifest.json) is the release contract.
`python3 Tools/parity/validate_manifest.py --release` fails closed if a fixture
or required pin is incomplete. A stable release cannot bypass the
candidate-bound runtime recordings and comparison. The runner preflight
matches the direct `@devcontainers/cli` package's npm SHA-512 integrity value
as well as its version; runtime fingerprints retain the exact tag commit and
integrity pin with every lane.

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
