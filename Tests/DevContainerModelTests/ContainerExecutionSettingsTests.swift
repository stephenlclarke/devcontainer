// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation
import Testing

struct ContainerExecutionSettingsTests {
    @Test func `defaults and persistence`() throws {
        let old = ContainerSpec(name: "test", image: "image")
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: JSONEncoder().encode(old)).executionSettings == nil)
        #expect(ContainerExecutionSettings().isEmpty)
        try ContainerExecutionSettings().validate()
        var spec = old
        spec.executionSettings = .init(
            memoryLimitInBytes: 6 * 1024 * 1024, sharedMemorySizeInBytes: 1, readOnlyRootFilesystem: true,
            sysctls: ["net.ipv4.ip_local_port_range": "10000 60000"], stopSignal: "SIGUSR1"
        )
        try spec.executionSettings?.validate()
        #expect(spec.executionSettings?.isEmpty == false)
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: JSONEncoder().encode(spec)) == spec)
    }

    @Test(arguments: [UInt64(0), 1, UInt64.max])
    func `invalid memory`(_ bytes: UInt64) {
        #expect(throws: DevContainerError.self) { try ContainerExecutionSettings(memoryLimitInBytes: bytes).validate() }
    }

    @Test(arguments: [UInt64(0), UInt64.max])
    func `invalid shared memory`(_ bytes: UInt64) {
        #expect(throws: DevContainerError.self) {
            try ContainerExecutionSettings(sharedMemorySizeInBytes: bytes).validate()
        }
    }

    @Test(arguments: ["", "net..key", "../key", "net/ipv4.key", "single", "net.bad key"])
    func `invalid sysctl keys`(_ key: String) {
        #expect(throws: DevContainerError.self) { try ContainerExecutionSettings(sysctls: [key: "1"]).validate() }
    }

    @Test(arguments: ["bad\nvalue", "bad\0value"])
    func `invalid sysctl values`(_ value: String) {
        #expect(throws: DevContainerError.self) {
            try ContainerExecutionSettings(sysctls: ["net.ipv4.ip_forward": value]).validate()
        }
    }
}
