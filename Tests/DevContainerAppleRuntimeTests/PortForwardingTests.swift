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

@testable import DevContainerAppleRuntime
import DevContainerModel
import Testing

struct PortForwardingTests {
    @Test
    func `forwarder resolves ephemeral listeners and stops all`() async throws {
        let forwarding = PortForwarding()
        let resolved = try await forwarding.start(
            containerID: "fixture",
            bindings: [
                PortBinding(
                    containerPort: 65000,
                    hostPort: nil,
                    protocolName: "tcp",
                    hostAddress: "127.0.0.1"
                )
            ],
            networkAddresses: ["bridge": "127.0.0.1/8"]
        )
        #expect(resolved.first?.hostPort != nil)
        await forwarding.stopAll()
    }

    @Test
    func `forwarder rejects missing addresses and closes partial setup`() async {
        let forwarding = PortForwarding()
        await #expect(throws: DevContainerError.self) {
            try await forwarding.start(
                containerID: "missing-address",
                bindings: [
                    PortBinding(
                        containerPort: 80,
                        hostPort: 0,
                        hostAddress: "127.0.0.1"
                    )
                ],
                networkAddresses: [:]
            )
        }

        await #expect(throws: DevContainerError.self) {
            try await forwarding.start(
                containerID: "partial",
                bindings: [
                    PortBinding(
                        containerPort: 65001,
                        hostPort: 0,
                        protocolName: "tcp",
                        hostAddress: "127.0.0.1"
                    ),
                    PortBinding(
                        containerPort: 65002,
                        hostPort: 0,
                        protocolName: "sctp",
                        hostAddress: "127.0.0.1"
                    )
                ],
                networkAddresses: ["bridge": "::1/128"]
            )
        }
        #expect(
            PortForwarding.preferredAddress(
                ["v6": "2001:db8::7/64"]
            ) == "2001:db8::7"
        )
    }
}
