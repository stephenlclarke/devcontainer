# Notices

devcontainer

Copyright 2026 devcontainer project authors.

This project is licensed under the Apache License, Version 2.0.

The project interoperates with, but is independent of, Apple container, the Dev Containers project, Docker, Microsoft Visual Studio Code, and container-compose. Product and project names remain the property of their respective owners.

`Sources/DevContainerProcess/ProcessCommand.swift` adapts the Apache-2.0 POSIX launcher from [container-compose](https://github.com/stephenlclarke/container-compose/blob/a28bb4586532a6b44f79a8beeea965c083477ed8/Sources/ComposeCore/ComposeProcessCommand.swift). Copyright 2026 container-compose project authors; attribution is retained in the adapted source. Changes specialize it for macOS, preserve exact executable paths and null default streams, suspend interactive children until terminal handoff, and separate exit observation from interruption-safe process reaping.

Release archives include `THIRD-PARTY-NOTICES.txt`, containing the complete
root license and notice texts for every exact SwiftPM dependency, and
`devcontainer.spdx.json`, identifying those reviewed dependencies and their
declared SPDX licenses.

The build harness includes `Tools/bazel/oci_image_layout.py`, copied unchanged from [container-compose's OCI image-layout validator](https://github.com/stephenlclarke/container-compose/blob/c716f013f6380941fd5fd04168ce4fbff5b61027/Tools/release/validate-oci-image-layout.py). Copyright 2026 container-compose project authors; Apache License 2.0. The original copyright and license header is retained. Source SHA-256: `a97ff5f88f5049a704f316922fd2626614830f2beb2d319ee458571744caa9d6`.

`Sources/DevContainerDockerClient/DockerFrontendUnixHTTPClient.swift`, `DockerFrontendUnixHTTPConnection.swift`, `DockerFrontendRequestLifetime.swift`, `DockerFrontendRequestDeadline.swift`, and their Unix-socket test helpers adapt the bounded transport from [container-engine-api](https://github.com/stephenlclarke/container-engine-api/tree/40436017e1e93012b8dab7cfc3c79783538065c3). Copyright 2026 container-engine-api project authors; Apache License 2.0. Original copyright and SPDX notices are retained. Changes use internal Devcontainer types, preserve the shared HTTP wire/response and ordinary error types, and supply a local absolute-deadline error for compatibility with the pinned Enhanced API.
