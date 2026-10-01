// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Foundation

/// Native execution settings. VM memory is not a claim of cgroup accounting parity.
public struct ContainerExecutionSettings: Codable, Equatable, Sendable {
    public var memoryLimitInBytes: UInt64?
    public var sharedMemorySizeInBytes: UInt64?
    public var readOnlyRootFilesystem: Bool
    public var sysctls: [String: String]
    public var stopSignal: String?

    public init(
        memoryLimitInBytes: UInt64? = nil, sharedMemorySizeInBytes: UInt64? = nil,
        readOnlyRootFilesystem: Bool = false, sysctls: [String: String] = [:], stopSignal: String? = nil
    ) {
        self.memoryLimitInBytes = memoryLimitInBytes
        self.sharedMemorySizeInBytes = sharedMemorySizeInBytes
        self.readOnlyRootFilesystem = readOnlyRootFilesystem
        self.sysctls = sysctls
        self.stopSignal = stopSignal
    }

    public var isEmpty: Bool {
        memoryLimitInBytes == nil && sharedMemorySizeInBytes == nil
            && !readOnlyRootFilesystem && sysctls.isEmpty && stopSignal == nil
    }

    public func validate() throws {
        if let memoryLimitInBytes, !(6 * 1024 * 1024 ... UInt64(Int64.max)).contains(memoryLimitInBytes) {
            throw DevContainerError(.invalidRequest, message: "Memory must be at least 6 MiB and fit an Int64")
        }
        if let sharedMemorySizeInBytes, !(1 ... UInt64(Int64.max)).contains(sharedMemorySizeInBytes) {
            throw DevContainerError(.invalidRequest, message: "Shared memory size must be positive and fit an Int64")
        }
        let keyCharacters = CharacterSet(
            charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        )
        for (key, value) in sysctls {
            let components = key.split(separator: ".", omittingEmptySubsequences: false)
            guard components.count > 1,
                  components.allSatisfy({ !$0.isEmpty && $0.rangeOfCharacter(from: keyCharacters.inverted) == nil }),
                  value.rangeOfCharacter(from: .controlCharacters) == nil
            else { throw DevContainerError(.invalidRequest, message: "Invalid sysctl key or value") }
        }
    }
}
