// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient

protocol AppleContainerBootstrapClient: Sendable {
    func bootstrap(id: String) async throws -> any ClientProcess
}

struct LiveAppleContainerBootstrapClient: AppleContainerBootstrapClient {
    let client: ContainerClient

    func bootstrap(id: String) async throws -> any ClientProcess {
        try await client.bootstrap(id: id, stdio: [nil, nil, nil])
    }
}
