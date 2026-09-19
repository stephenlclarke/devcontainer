// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Containerization
import ContainerResource
import DevContainerModel
import Foundation

enum AppleContainerExecutionSettings {
    static func apply(_ settings: ContainerExecutionSettings?, to configuration: inout ContainerConfiguration) throws {
        guard let settings else { return }
        try settings.validate()
        for (key, forced) in ["vm.overcommit_memory": "1", "vm.max_map_count": "262144"] {
            if let requested = settings.sysctls[key], requested != forced {
                throw DevContainerError(.unsupportedCapability, message: "Apple runtime forces sysctl \(key)=\(forced)")
            }
        }
        if let memory = settings.memoryLimitInBytes {
            configuration.resources.memoryInBytes = memory
        }
        configuration.shmSize = settings.sharedMemorySizeInBytes
        configuration.readOnly = settings.readOnlyRootFilesystem
        configuration.sysctls = settings.sysctls
        if let signal = settings.stopSignal {
            do { _ = try Signal(signal) } catch {
                throw DevContainerError(.invalidRequest, message: "Invalid Linux stop signal")
            }
            configuration.stopSignal = signal
        }
    }

    static func observed(_ configuration: ContainerConfiguration) -> ContainerExecutionSettings {
        .init(
            memoryLimitInBytes: configuration.resources.memoryInBytes,
            sharedMemorySizeInBytes: configuration.shmSize,
            readOnlyRootFilesystem: configuration.readOnly,
            sysctls: configuration.sysctls, stopSignal: configuration.stopSignal
        )
    }

    static func observed(_ configuration: [String: Any]) -> ContainerExecutionSettings {
        let resources = configuration["resources"] as? [String: Any]
        return .init(
            memoryLimitInBytes: (resources?["memoryInBytes"] as? NSNumber).flatMap { UInt64($0.stringValue) },
            sharedMemorySizeInBytes: (configuration["shmSize"] as? NSNumber).flatMap { UInt64($0.stringValue) },
            readOnlyRootFilesystem: configuration["readOnly"] as? Bool ?? false,
            sysctls: configuration["sysctls"] as? [String: String] ?? [:],
            stopSignal: configuration["stopSignal"] as? String
        )
    }
}
