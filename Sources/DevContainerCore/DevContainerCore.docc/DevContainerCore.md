# ``DevContainerCore``

Run unmodified VS Code Dev Containers tooling on Apple container.

## Overview

The package supplies a project-owned, Apple-backed Engine API adapter that
implements the required Docker-shaped protocol without Docker software. The runtime-neutral core owns project
identity, provider selection, labels, durable state, and capability checks.
The required native `container-compose` provider remains process-isolated.

The implementation follows the
[Development Containers Specification](https://github.com/devcontainers/spec)
and uses the official
[`@devcontainers/cli`](https://github.com/devcontainers/cli) as its black-box
reference consumer.

### Runtime lanes

- Stock Apple: the project-owned compatibility adapter uses the local socket
  backed by a tagged `apple/container` runtime; multi-service operations use
  native `container-compose`.
- Enhanced Container: the same adapters invoke Stephen Clarke's separately
  maintained runtime and Compose executable when explicitly selected. Apple
  does not supply a Compose provider.
- Docker oracle: the same fixtures run against a pinned real Docker Engine and
  establish expected behavior.

## Topics

### Essentials

- <doc:UserGuide>
- <doc:Architecture>
- <doc:Compatibility>
- <doc:Conformance>
- <doc:Testing>
- <doc:Performance>
- ``DevContainerConfiguration``
- ``DevContainerConfigurationStore``
- ``RuntimeLabels``
- ``ProjectCoordinator``
