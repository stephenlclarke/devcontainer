// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import Foundation

protocol AppleContainerBootstrapClient: Sendable {
    func bootstrap(id: String, stdio: [FileHandle?]) async throws -> any ClientProcess
}

struct LiveAppleContainerBootstrapClient: AppleContainerBootstrapClient {
    let client: ContainerClient

    func bootstrap(id: String, stdio: [FileHandle?]) async throws -> any ClientProcess {
        try await client.bootstrap(id: id, stdio: AppleXPCFileHandleTransfer.copies(of: stdio))
    }
}
