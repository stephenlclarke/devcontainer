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

@Test
func `TTY container create preserves initial console dimensions through inspect`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(id: "sha256:image", references: ["alpine:test"], createdAt: Date(), size: 1)
    )
    let router = DockerRouter(runtime: runtime)
    let created = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=initial-console-size",
            body: Data(
                #"""
                {"Image":"alpine:test","Cmd":["sh","-c","stty size"],"Tty":true,
                "HostConfig":{"ConsoleSize":[37,113]}}
                """#.utf8
            )
        )
    )
    #expect(created.status == 201)
    let object = try #require(JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any])
    let id = try #require(object["Id"] as? String)
    let snapshot = try await runtime.inspectContainer(id: id, context: RuntimeRequestContext())
    #expect(snapshot.spec.terminal)
    #expect(snapshot.spec.terminalWidth == 113)
    #expect(snapshot.spec.terminalHeight == 37)
    #expect(snapshot.spec.command == ["sh", "-c", "stty size"])

    let inspected = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/containers/\(id)/json")
    )
    #expect(inspected.status == 200)
    let inspectObject = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
    let hostConfig = try #require(inspectObject["HostConfig"] as? [String: Any])
    #expect(hostConfig["ConsoleSize"] as? [Int] == [37, 113])
    #expect(inspectObject["Path"] as? String == "sh")
    #expect(inspectObject["Args"] as? [String] == ["-c", "stty size"])
}

@Test
func `non-TTY and zero-size creates keep the native terminal default`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(id: "sha256:image", references: ["alpine:test"], createdAt: Date(), size: 1)
    )
    let router = DockerRouter(runtime: runtime)
    for (name, tty, size, expectedWidth, expectedHeight) in [
        ("non-tty-size", false, "[37,113]", nil as UInt16?, nil as UInt16?),
        ("zero-tty-size", true, "[0,0]", 0 as UInt16?, 0 as UInt16?),
        ("zero-height-size", true, "[0,113]", 113 as UInt16?, 0 as UInt16?),
        ("zero-width-size", true, "[37,0]", 0 as UInt16?, 37 as UInt16?)
    ] {
        let created = await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/create?name=\(name)",
                body: Data(
                    #"{"Image":"alpine:test","Cmd":["true"],"Tty":\#(tty),"HostConfig":{"ConsoleSize":\#(size)}}"#.utf8
                )
            )
        )
        #expect(created.status == 201)
        let object = try #require(JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any])
        let id = try #require(object["Id"] as? String)
        let snapshot = try await runtime.inspectContainer(id: id, context: RuntimeRequestContext())
        #expect(snapshot.spec.terminalWidth == expectedWidth)
        #expect(snapshot.spec.terminalHeight == expectedHeight)
        let inspected = await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/containers/\(id)/json")
        )
        #expect(inspected.status == 200)
        let inspectObject = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let hostConfig = try #require(inspectObject["HostConfig"] as? [String: Any])
        let reportedSize = hostConfig["ConsoleSize"] as? [Int]
        let expectedReportedSize = tty
            ? size.dropFirst().dropLast().split(separator: ",").compactMap { Int($0) }
            : nil
        #expect(reportedSize == expectedReportedSize)
    }
}

@Test
func `malformed initial container console dimensions fail before creation`() async {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(id: "sha256:image", references: ["alpine:test"], createdAt: Date(), size: 1)
    )
    let router = DockerRouter(runtime: runtime)
    for size in ["[24]", "[24,65536]"] {
        let response = await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/create",
                body: Data(
                    #"{"Image":"alpine:test","Tty":true,"HostConfig":{"ConsoleSize":\#(size)}}"#.utf8
                )
            )
        )
        #expect(response.status == 400)
    }
    #expect(await runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext()).isEmpty)
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
