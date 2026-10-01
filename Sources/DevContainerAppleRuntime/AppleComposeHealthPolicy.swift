// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation

extension AppleContainerRuntime {
    static let composeHealthPolicyLabel = "com.apple.container.compose.health-policy"

    /// Native labels carry requested policy only. The gateway still executes
    /// probes and derives health from their actual exit status.
    static func composeHealthPolicy(labels: [String: String]) throws -> ContainerHealthcheck? {
        guard let value = labels[composeHealthPolicyLabel] else { return nil }
        let data = Data(value.utf8)
        let keys: Set = [
            "version", "test", "intervalNanoseconds", "timeoutNanoseconds", "retries", "startPeriodNanoseconds"
        ]
        guard data.count <= 16384,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == keys,
              let policy = try? JSONDecoder().decode(NativeComposeHealthPolicy.self, from: data),
              policy.version == 1,
              policy.health.intervalNanoseconds > 0, policy.health.timeoutNanoseconds > 0,
              policy.health.startPeriodNanoseconds >= 0,
              policy.health.retries > 0, policy.health.retries <= Int(UInt32.max),
              validComposeHealthCommand(policy.health.test)
        else {
            throw DevContainerError(.providerProtocolMismatch, message: "Invalid native Compose health policy")
        }
        return policy.health
    }

    private static func validComposeHealthCommand(_ test: [String]) -> Bool {
        test == ["NONE"] || (test.count == 2 && test[0] == "CMD-SHELL" && !test[1].isEmpty
            && test[1].utf8.count <= 4096 && !test[1].contains("\0"))
    }
}

private struct NativeComposeHealthPolicy: Decodable {
    let version: Int
    let health: ContainerHealthcheck

    private enum CodingKeys: String, CodingKey { case version }

    init(from decoder: any Decoder) throws {
        version = try decoder.container(keyedBy: CodingKeys.self).decode(Int.self, forKey: .version)
        health = try ContainerHealthcheck(from: decoder)
    }
}
