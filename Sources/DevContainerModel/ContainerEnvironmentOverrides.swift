// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

/// Engine environment overrides preserve removal separately from an empty value.
public struct ContainerEnvironmentOverrides: Equatable, Sendable {
    public private(set) var values: [String: String] = [:]
    public private(set) var removedKeys: [String] = []

    public init(_ entries: [String]) throws {
        var removed = Set<String>()
        for entry in entries {
            let parts = entry.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            let key = String(parts[0])
            guard !key.isEmpty, !entry.contains("\0") else {
                throw DevContainerError(.invalidRequest, message: "Invalid environment entry")
            }
            if parts.count == 2 {
                values[key] = String(parts[1])
                removed.remove(key)
            } else {
                values.removeValue(forKey: key)
                removed.insert(key)
            }
        }
        removedKeys = removed.sorted()
    }
}
