// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
#if DEVCONTAINER_ENHANCED_RUNTIME
    import ContainerAPIClient
    @testable import DevContainerAppleRuntime
    import Foundation
    import Testing

    /// Requires an explicitly provisioned enhanced runtime; not a unit test.
    struct LoggingRecordIntegrationTests {
        @Test
        func `live record client rejects missing container`() async throws {
            let environment = ProcessInfo.processInfo.environment
            try #require(environment["DEVCONTAINER_HOST_INTEGRATION"] == "1")
            let client = LiveAppleContainerLoggingRecordClient(client: ContainerClient())
            let missingID = "devcontainer-logging-handoff-\(UUID().uuidString)"
            await #expect(throws: (any Error).self) {
                _ = try await client.loggingRecords(id: missingID)
            }
            await #expect(throws: (any Error).self) {
                _ = try await client.loggingRecordStream(id: missingID)
            }
        }
    }
#endif
