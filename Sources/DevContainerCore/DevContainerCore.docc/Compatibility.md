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
security options. The adapter rejects a non-empty hostname and security
options other than the already-native `seccomp=unconfined` state before
runtime creation. Stock privileged mode maps to Apple `--cap-add ALL`; it is
not full Docker privileged semantics. A separately fingerprinted enhanced
runtime uses native flags only when its actual help surface advertises them.

The certified fixture matrix is not a claim of complete Development
Containers Specification support. See <doc:Conformance> for the complete
property audit, including arbitrary `runArgs`, GPU, advanced mounts, anonymous
volumes, and network-attachment limitations.

See the maintained repositories in the
[Dev Containers organization](https://github.com/devcontainers), especially
the [specification](https://github.com/devcontainers/spec), the
[reference CLI](https://github.com/devcontainers/cli), published
[Features](https://github.com/devcontainers/features), and
[Images](https://github.com/devcontainers/images).
