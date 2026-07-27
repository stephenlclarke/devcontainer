# Differential parity fixtures

The parity suite treats a pinned real Docker Engine plus the official [`@devcontainers/cli`](https://github.com/devcontainers/cli) as the behavioral oracle. Every claimed fixture must produce equivalent semantic observations through the stock Apple and container-compose lanes.

The checked-in [`manifest.json`](manifest.json) is the release contract. During bootstrap, fixtures are marked `planned`; `python3 Tools/parity/validate_manifest.py --release` deliberately fails until every fixture is implemented. A stable release cannot bypass that check.

Each implemented fixture will contain:

- `.devcontainer/devcontainer.json` and any Dockerfile or Compose files;
- `contract.json`, containing the semantic assertions;
- `probe.sh`, copied into the development container to emit canonical observations;
- only public, digest-pinned images and Features;
- no credentials, private registry names, personal paths, or machine-specific state.

Runtime jobs will retain raw output, normalized observation JSON, JUnit results, a Markdown matrix, backend fingerprints, cleanup evidence, and diagnostics for every difference.
