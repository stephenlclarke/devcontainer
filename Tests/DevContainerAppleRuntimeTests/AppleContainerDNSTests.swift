// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerDNSTests {
    @Test(arguments: [true, false])
    func `cli preserves DNS or rejects unsupported runtime`(_ supported: Bool) async throws {
        let fixture = try FakeAppleCLI(enhancedCreateOptions: supported)
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        let spec = ContainerSpec(
            name: "fixture", image: "fixture:latest", command: ["/bin/true"],
            dns: .init(nameservers: ["192.0.2.53"], searchDomains: ["example.test"], options: ["ndots:2"])
        )
        if supported {
            _ = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
            #expect(try fixture.log().contains("--dns 192.0.2.53 --dns-search example.test --dns-option ndots:2"))
        } else {
            await #expect(throws: DevContainerError.self) {
                try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
            }
            #expect(try !fixture.log().contains("create --name"))
        }
    }
}
