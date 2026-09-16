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

@testable import DevContainerDockerCLI
import DevContainerModel
import Testing

@Suite("Docker-less engine transport")
struct DockerlessEngineTransportTests {
    @Test
    func `accepts only the project Apple runtime engine and caches identity`() throws {
        let transport = StubTransport([
            .init(
                status: 200,
                headers: [
                    DevContainerEngineIdentity.header.lowercased():
                        DevContainerEngineIdentity.value
                ],
                target: "/_ping"
            ),
            .json(["Images": 0], target: "/info"),
            .json(["Images": 0], target: "/info")
        ])
        let application = DockerCLIApplication(
            transport: DevContainerEngineTransport(transport: transport)
        )

        _ = try application.run(arguments: ["info"])
        _ = try application.run(arguments: ["info"])

        #expect(transport.requests.map(\.target) == ["/_ping", "/info", "/info"])
    }

    @Test
    func `notification transport authenticates before forwarding the request`() throws {
        let transport = StubTransport([
            .init(
                status: 200,
                headers: [
                    DevContainerEngineIdentity.header: DevContainerEngineIdentity.value
                ],
                target: "/_ping"
            ),
            .json(["Id": "fixture"], target: "/build")
        ])
        let verified = DevContainerEngineTransport(transport: transport)

        _ = try verified.send(
            DockerHTTPRequest(method: "POST", target: "/build"),
            maximumBodyBytes: 1024,
            onRequestSent: {},
            onBody: { _ in }
        )

        #expect(transport.requests.map(\.target) == ["/_ping", "/build"])
    }

    @Test
    func `rejects a foreign Docker-compatible engine before its workload request`() {
        let responses = [
            StubTransport.StubResponse(status: 200),
            StubTransport.StubResponse(
                status: 503,
                headers: [
                    DevContainerEngineIdentity.header: DevContainerEngineIdentity.value
                ]
            )
        ]
        for var identityResponse in responses {
            identityResponse.target = "/_ping"
            let transport = StubTransport([
                identityResponse,
                .json(["Images": 0], target: "/info")
            ])
            let application = DockerCLIApplication(
                transport: DevContainerEngineTransport(transport: transport)
            )

            #expect(throws: DockerHTTPClientError.self) {
                try application.run(arguments: ["info"])
            }
            #expect(transport.requests.map(\.target) == ["/_ping"])
        }
    }
}
