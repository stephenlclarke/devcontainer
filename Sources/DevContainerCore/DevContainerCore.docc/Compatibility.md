# Compatibility

Compatibility is evidence-based. Each release records exact versions and
commits for Docker Engine, Docker CLI, Docker Compose, VS Code, the Dev
Containers extension, `@devcontainers/cli`, macOS, Apple container, and the
optional Compose provider.

The parity manifest binds every scenario to Docker-oracle, stock-Apple, and
Compose-provider lanes. A serialized trusted-runner workflow prepares and
fingerprints one distribution at a time so a lane never replaces another
lane's live runtime distribution. Release validation rejects incomplete or
missing evidence.

Stock Apple `container` 1.1.0 does not transport explicit Docker hostnames or
security options. The adapter rejects those fields before side effects and
uses enhanced native flags only when the selected, separately fingerprinted
runtime advertises them. Unsupported security behavior is never normalized
into a parity pass.

See the maintained repositories in the
[Dev Containers organization](https://github.com/devcontainers), especially
the [specification](https://github.com/devcontainers/spec), the
[reference CLI](https://github.com/devcontainers/cli), published
[Features](https://github.com/devcontainers/features), and
[Images](https://github.com/devcontainers/images).
