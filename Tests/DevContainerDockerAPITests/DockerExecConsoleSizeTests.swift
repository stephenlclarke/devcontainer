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

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `non-terminal create and terminal exec accept Docker console dimensions`() async throws {
    let session = InMemoryProcessSession(frames: [], exitCode: 0)
    let runtime = InMemoryRuntime(execSession: session)
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:image",
            references: ["alpine:test"],
            createdAt: Date(),
            size: 1
        )
    )
    let router = DockerRouter(runtime: runtime)
    let containerID = try await createConsoleContainer(router)
    let execID = try await createConsoleExec(router, containerID: containerID)
    let snapshot = try await runtime.inspectExec(
        id: ExecID(rawValue: execID),
        context: RuntimeRequestContext()
    )
    #expect(snapshot.spec.terminalWidth == 80)
    #expect(snapshot.spec.terminalHeight == 24)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/exec/\(execID)/start",
                body: Data(
                    #"{"ConsoleSize":[40,120],"Detach":true,"Tty":true}"#.utf8
                )
            )
        ).status == 200
    )
    let applied = try #require(await session.terminalSize())
    #expect(applied.width == 120)
    #expect(applied.height == 40)
}

private func createConsoleContainer(
    _ router: DockerRouter
) async throws -> String {
    let created = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=console-size",
            body: Data(
                #"{"Image":"alpine:test","HostConfig":{"ConsoleSize":[24,80]}}"#.utf8
            )
        )
    )
    #expect(created.status == 201)
    let createdObject = try #require(
        JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any]
    )
    let containerID = try #require(createdObject["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/\(containerID)/start"
            )
        ).status == 204
    )
    return containerID
}

private func createConsoleExec(
    _ router: DockerRouter,
    containerID: String
) async throws -> String {
    let execCreated = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/\(containerID)/exec",
            body: Data(
                #"""
                {
                  "Cmd":["true"],
                  "AttachStdout":true,
                  "ConsoleSize":[24,80],
                  "Tty":true
                }
                """#.utf8
            )
        )
    )
    #expect(execCreated.status == 201)
    let execObject = try #require(
        JSONSerialization.jsonObject(with: bytes(execCreated)) as? [String: Any]
    )
    return try #require(execObject["Id"] as? String)
}

@Test
func `console dimensions require exactly two unsigned 16-bit values`() throws {
    let router = DockerRouter(runtime: InMemoryRuntime())

    #expect(try router.consoleSize(nil, name: "ConsoleSize") == nil)
    #expect(throws: DevContainerError.self) {
        _ = try router.consoleSize([24], name: "ConsoleSize")
    }
    #expect(throws: DevContainerError.self) {
        _ = try router.consoleSize([24, 65536], name: "ConsoleSize")
    }
}
