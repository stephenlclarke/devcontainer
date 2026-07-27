//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

import DevContainerModel
import Foundation
import NIOCore
import NIOPosix
import SocketForwarder

/// Owns host listeners used to make Docker TCP port publishing deterministic
/// across both stock and custom Apple container distributions.
actor PortForwarding {
    private var listeners: [String: [SocketForwarderResult]] = [:]

    func start(
        containerID: String,
        bindings: [PortBinding],
        networkAddresses: [String: String]
    ) async throws -> [PortBinding] {
        await stop(containerID: containerID)
        guard !bindings.isEmpty else {
            return []
        }
        guard let targetHost = Self.preferredAddress(networkAddresses) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "container \(containerID) has no reachable network address"
            )
        }

        var opened: [SocketForwarderResult] = []
        var resolvedBindings: [PortBinding] = []
        do {
            for binding in bindings {
                let proxyAddress = try SocketAddress(
                    ipAddress: binding.hostAddress,
                    port: Int(binding.hostPort ?? 0)
                )
                let serverAddress = try SocketAddress(
                    ipAddress: targetHost,
                    port: Int(binding.containerPort)
                )
                let forwarder: any SocketForwarder
                switch binding.protocolName.lowercased() {
                case "tcp":
                    forwarder = try TCPForwarder(
                        proxyAddress: proxyAddress,
                        serverAddress: serverAddress,
                        eventLoopGroup: NIOSingletons.posixEventLoopGroup
                    )
                case "udp":
                    forwarder = try UDPForwarder(
                        proxyAddress: proxyAddress,
                        serverAddress: serverAddress,
                        eventLoopGroup: NIOSingletons.posixEventLoopGroup
                    )
                default:
                    throw DevContainerError(
                        .unsupportedCapability,
                        message: "unsupported published-port protocol \(binding.protocolName)"
                    )
                }
                let result = try await forwarder.run().get()
                opened.append(result)
                var resolved = binding
                if let port = result.proxyAddress?.port {
                    resolved.hostPort = UInt16(port)
                }
                resolvedBindings.append(resolved)
            }
            listeners[containerID] = opened
            return resolvedBindings
        } catch {
            for listener in opened {
                listener.close()
                try? await listener.wait()
            }
            throw DevContainerError(
                .conflict,
                message: "port forwarding for container \(containerID) failed: \(error)"
            )
        }
    }

    func stop(containerID: String) async {
        let channels = listeners.removeValue(forKey: containerID) ?? []
        for channel in channels {
            channel.close()
            try? await channel.wait()
        }
    }

    func stopAll() async {
        let identifiers = Array(listeners.keys)
        for identifier in identifiers {
            await stop(containerID: identifier)
        }
    }

    static func preferredAddress(
        _ values: [String: String]
    ) -> String? {
        let addresses = values.values.map {
            String($0.split(separator: "/", maxSplits: 1)[0])
        }
        return addresses.first { !$0.contains(":") && !$0.isEmpty }
            ?? addresses.first { !$0.isEmpty }
    }
}
