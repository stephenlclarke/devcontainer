// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation
import Testing

struct RuntimeDNSConfigurationTests {
    @Test func `round trip and legacy state`() throws {
        let legacy = ContainerSpec(name: "fixture", image: "alpine")
        let data = try JSONEncoder().encode(legacy)
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: data).dns == nil)
        var configured = legacy
        configured.dns = .init(
            nameservers: ["192.0.2.53", "2001:db8::53"], searchDomains: ["example.test"], options: ["ndots:2"]
        )
        try configured.dns?.validate()
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: JSONEncoder().encode(configured)) == configured)
        try RuntimeDNSConfiguration().validate()
    }

    @Test(arguments: ["localhost", "192.0.2.999", "192.0.2.53\0suffix", "", "192.0.2.53\nsearch injected"])
    func `invalid nameserver`(_ value: String) {
        #expect(throws: DevContainerError.self) { try RuntimeDNSConfiguration(nameservers: [value]).validate() }
    }

    @Test(arguments: ["", "example.test\noptions debug", "two tokens", "bad\0token"])
    func `invalid resolver token`(_ value: String) {
        #expect(throws: DevContainerError.self) { try RuntimeDNSConfiguration(searchDomains: [value]).validate() }
        #expect(throws: DevContainerError.self) { try RuntimeDNSConfiguration(options: [value]).validate() }
    }
}
