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
import Testing

@Test
func `build info uses makefile owned version`() throws {
    #expect(BuildInfo.current.version == "1.0.0")
    #expect(BuildInfo.current.source == "stephenlclarke/devcontainer")
    #expect(!BuildInfo.current.lane.isEmpty)
    #expect(BuildInfo.current.buildType == "development")
    #expect(BuildInfo.current.architecture == "arm64")
    #expect(BuildInfo.current.containerDistribution == "apple")
    #expect(BuildInfo.current.provider == "none")
    let encoded = try JSONEncoder().encode(BuildInfo.current)
    #expect(try JSONDecoder().decode(BuildInfo.self, from: encoded) == BuildInfo.current)
}

@Test
func `identifiers retain their raw values`() {
    #expect(ProjectKey(rawValue: "501:demo").description == "501:demo")
    #expect(RuntimeID(rawValue: "runtime").rawValue == "runtime")
    #expect(DockerID(rawValue: "docker").rawValue == "docker")
    #expect(OperationID.random() != OperationID.random())
    #expect(ExecID.random() != ExecID.random())
}

@Test
func `runtime models round trip through JSON`() throws {
    let spec = ContainerSpec(
        name: "demo",
        image: "alpine:3.22",
        command: ["sleep", "infinity"],
        environment: ["A": "B"],
        labels: ["example": "true"],
        workingDirectory: "/workspace",
        user: "1000:1000",
        hostname: "demo",
        mounts: [
            RuntimeMount(type: .bind, source: "/tmp/source", destination: "/workspace")
        ],
        ports: [
            PortBinding(containerPort: 8080, hostPort: 18080)
        ],
        terminal: true,
        openStandardInput: true
    )
    let snapshot = ContainerSnapshot(
        runtimeID: RuntimeID(rawValue: "runtime"),
        dockerID: DockerID(rawValue: "docker"),
        spec: spec,
        state: .running,
        createdAt: Date(timeIntervalSince1970: 1),
        startedAt: Date(timeIntervalSince1970: 2),
        networkAddresses: ["default": "192.0.2.2"]
    )
    let data = try JSONEncoder().encode(snapshot)
    #expect(try JSONDecoder().decode(ContainerSnapshot.self, from: data) == snapshot)
}

@Test
func `errors include correlation when present`() {
    let error = DevContainerError(
        .invalidRequest,
        message: "invalid",
        correlationID: "correlation"
    )
    #expect(error.description == "invalid [correlation: correlation]")
    #expect(DevContainerError(.notFound, message: "missing").description == "missing")
}
