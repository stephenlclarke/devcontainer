# ``DevContainerCore``

Run unmodified VS Code Dev Containers tooling on Apple container.

## Overview

The package supplies a Docker Engine API compatibility boundary backed by
Apple's native container runtime. The runtime-neutral core owns project
identity, provider selection, labels, durable state, and capability checks.
The optional `container-compose` provider remains process-isolated.

The implementation follows the
[Development Containers Specification](https://github.com/devcontainers/spec)
and uses the official
[`@devcontainers/cli`](https://github.com/devcontainers/cli) as its black-box
reference consumer.

### Runtime lanes

- Stock Apple: Docker CLI and Docker Compose use the local compatibility
  socket backed by a tagged `apple/container` runtime.
- `container-compose` provider: the configured Compose wrapper invokes Stephen
  Clarke's separately maintained executable while inspection and exec continue
  through the compatibility socket. Apple does not supply this provider.
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
