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

import Darwin
@testable import DevContainerComposeCLI
import DevContainerModel
import DevContainerState
import Foundation
import Testing

struct DevContainerComposeCommandTests {
    @Test
    func `mutating command claims the project name resolved by Compose`() async throws {
        let fixture = try ComposeCommandFixture(projectName: "canonical-project")
        let environment = fixture.environment

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: [
                    "--env-file", fixture.root.appendingPathComponent(".env").path,
                    "-f", "/projects/example/compose.yaml",
                    "up", "--profile", "debug", "--detach"
                ],
                environment: environment
            ) == 0
        )

        let store = try SQLiteStateStore(path: fixture.state)
        let project = try await store.project(
            key: ProjectKey(rawValue: "\(getuid()):canonical-project")
        )
        #expect(project?.provider == .containerCompose)
        let invocations = try fixture.invocations()
        #expect(
            invocations.contains(
                "--env-file \(fixture.root.appendingPathComponent(".env").path) "
                    + "-f /projects/example/compose.yaml --profile debug config --format json"
            )
        )
        #expect(
            invocations.contains(
                "--env-file \(fixture.root.appendingPathComponent(".env").path) "
                    + "-f /projects/example/compose.yaml up --profile debug --detach"
            )
        )
    }

    @Test
    func `explicit project mutations claim without a configuration probe`() async throws {
        let fixture = try ComposeCommandFixture(projectName: "ignored")

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["scale", "--project-name", "explicit-project", "web=2"],
                environment: fixture.environment
            ) == 0
        )

        let store = try SQLiteStateStore(path: fixture.state)
        #expect(
            try await store.project(
                key: ProjectKey(rawValue: "\(getuid()):explicit-project")
            )?.provider == .containerCompose
        )
        #expect(try fixture.invocations() == ["scale --project-name explicit-project web=2"])
    }

    @Test
    func `invalid explicit project names fail before provider execution`() async throws {
        let fixture = try ComposeCommandFixture(projectName: "ignored")

        await #expect(throws: DevContainerError.self) {
            _ = try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "Invalid", "up"],
                environment: fixture.environment
            )
        }
        #expect(try fixture.invocations().isEmpty)
    }

    @Test
    func `failed mutations preserve child status and failed recovery state`() async throws {
        let fixture = try ComposeCommandFixture(
            projectName: "ignored",
            exitStatus: 17
        )

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "failed-project", "up"],
                environment: fixture.environment
            ) == 17
        )

        let store = try SQLiteStateStore(path: fixture.state)
        let project = try await store.project(
            key: ProjectKey(rawValue: "\(getuid()):failed-project")
        )
        #expect(project?.reconciliationState == .failed)
        #expect(project?.desiredGeneration == 1)
        #expect(try await store.unfinishedOperations().isEmpty)
    }

    @Test
    func `successful project removal releases the provider claim`() async throws {
        for arguments in [
            ["--project-name", "down-project", "down"],
            ["--project-name", "wait-project", "wait", "web", "--down-project"]
        ] {
            let fixture = try ComposeCommandFixture(projectName: "ignored")
            let projectName = arguments[1]
            let project = ProjectKey(rawValue: "\(getuid()):\(projectName)")
            let store = try SQLiteStateStore(path: fixture.state)
            _ = try await store.claimProject(
                key: project,
                provider: .containerCompose,
                composeProject: projectName,
                projectDirectory: fixture.root.path,
                configurationHash: "previous"
            )
            let now = Date()
            try await store.recordResource(
                ResourceRecord(
                    runtimeKind: "container",
                    runtimeID: RuntimeID(rawValue: "\(projectName)-app"),
                    dockerID: DockerID(rawValue: "\(projectName)-docker"),
                    project: project,
                    logicalName: "app",
                    role: "primary",
                    provider: .containerCompose,
                    specificationHash: "specification",
                    generation: 1,
                    observedState: "running",
                    labelsHash: "labels",
                    createdAt: now,
                    updatedAt: now
                )
            )
            #expect(
                try await DevContainerComposeCommand.run(
                    arguments: arguments,
                    environment: fixture.environment
                ) == 0
            )

            #expect(
                try await store.project(key: project) == nil
            )
            #expect(try await store.resources(project: project).isEmpty)
        }
    }
}

private final class ComposeCommandFixture {
    let root: URL
    let state: URL
    private let executable: URL
    private let invocationLog: URL
    private let exitStatus: Int32

    init(projectName: String, exitStatus: Int32 = 0) throws {
        self.exitStatus = exitStatus
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "devcontainer-compose-cli-tests-\(UUID().uuidString)",
                isDirectory: true
            )
        state = root.appendingPathComponent("state.sqlite")
        executable = root.appendingPathComponent("container-compose")
        invocationLog = root.appendingPathComponent("invocations.log")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        let script = """
        #!/bin/sh
        set -eu
        printf '%s\n' "$*" >> "$INVOCATION_LOG"
        case " $* " in
          *" config --format json "*)
            printf '%s\n' '{"name":"\(projectName)"}'
            ;;
          *)
            exit \(exitStatus)
            ;;
        esac
        """
        try Data(script.utf8).write(to: executable, options: .atomic)
        #expect(chmod(executable.path, S_IRWXU) == 0)
    }

    deinit {
        try? FileManager.default.removeItem(at: root)
    }

    var environment: [String: String] {
        [
            "DEVCONTAINER_COMPOSE_BIN": executable.path,
            "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
            "DEVCONTAINER_CONFIG": root.appendingPathComponent("config.toml").path,
            "DEVCONTAINER_SOCKET": root.appendingPathComponent("docker.sock").path,
            "DEVCONTAINER_STATE": state.path,
            "INVOCATION_LOG": invocationLog.path,
            "PATH": "/usr/bin:/bin"
        ]
    }

    func invocations() throws -> [String] {
        guard FileManager.default.fileExists(atPath: invocationLog.path) else {
            return []
        }
        return try String(contentsOf: invocationLog, encoding: .utf8)
            .split(separator: "\n")
            .map(String.init)
    }
}
