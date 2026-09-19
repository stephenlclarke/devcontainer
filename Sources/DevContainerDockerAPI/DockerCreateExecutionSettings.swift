// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel

extension DockerRouter {
    func executionSettings(_ request: DockerCreateContainerRequest) throws -> ContainerExecutionSettings? {
        let settings = try ContainerExecutionSettings(
            memoryLimitInBytes: positiveByteCount(request.hostConfig?.memory, field: "Memory"),
            sharedMemorySizeInBytes: positiveByteCount(request.hostConfig?.shmSize, field: "ShmSize"),
            readOnlyRootFilesystem: request.hostConfig?.readOnlyRootFilesystem ?? false,
            sysctls: request.hostConfig?.sysctls ?? [:],
            stopSignal: request.stopSignal.flatMap { $0.isEmpty ? nil : $0 }
        )
        try settings.validate()
        return settings
    }

    private func positiveByteCount(_ value: Int64?, field: String) throws -> UInt64? {
        guard let value, value != 0 else { return nil }
        guard value > 0 else {
            throw DevContainerError(.invalidRequest, message: "HostConfig.\(field) cannot be negative")
        }
        return UInt64(value)
    }
}
