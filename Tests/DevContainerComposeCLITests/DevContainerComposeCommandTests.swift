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
import DevContainerCore
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
    func `docker backed compose does not overwrite inner engine generations`() async throws {
        let projectName = "nested-project"
        let project = ProjectKey(rawValue: "\(getuid()):\(projectName)")
        let fixture = try ComposeCommandFixture(
            projectName: "ignored",
            provider: .docker,
            innerGeneration: 7,
            innerProjectKey: project
        )

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", projectName, "up"],
                environment: fixture.environment
            ) == 0
        )

        let store = try SQLiteStateStore(path: fixture.state)
        let record = try await store.project(key: project)
        #expect(record?.provider == .stock)
        #expect(record?.desiredGeneration == 7)
        #expect(record?.reconciliationState == .clean)
    }

    @Test
    func `docker down retains claim while named volumes remain`() async throws {
        let projectName = "retained-volume-project"
        let fixture = try ComposeCommandFixture(
            projectName: "ignored",
            provider: .docker
        )
        let project = ProjectKey(rawValue: "\(getuid()):\(projectName)")
        let store = try SQLiteStateStore(path: fixture.state)
        _ = try await store.claimProject(
            key: project,
            provider: .stock,
            composeProject: projectName,
            projectDirectory: fixture.root.path,
            configurationHash: "previous"
        )
        let now = Date()
        let volume = ResourceRecord(
            runtimeKind: "volume",
            runtimeID: RuntimeID(rawValue: "\(projectName)_cache"),
            dockerID: DockerID(rawValue: "\(projectName)_cache"),
            project: project,
            logicalName: "cache",
            role: "volume",
            provider: .stock,
            specificationHash: "specification",
            generation: 1,
            observedState: "active",
            labelsHash: "labels",
            createdAt: now,
            updatedAt: now
        )
        try await store.recordResource(volume)

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", projectName, "down"],
                environment: fixture.environment
            ) == 0
        )

        #expect(try await store.project(key: project)?.provider == .stock)
        #expect(try await store.resources(project: project) == [volume])
    }

    @Test
    func `docker down releases an empty project claim`() async throws {
        let projectName = "empty-docker-project"
        let fixture = try ComposeCommandFixture(
            projectName: "ignored",
            provider: .docker
        )
        let project = ProjectKey(rawValue: "\(getuid()):\(projectName)")

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", projectName, "down"],
                environment: fixture.environment
            ) == 0
        )

        let store = try SQLiteStateStore(path: fixture.state)
        #expect(try await store.project(key: project) == nil)
    }

    @Test
    func `native down retains claim when live volume probe is nonempty`() async throws {
        let projectName = "native-volume-project"
        let fixture = try ComposeCommandFixture(
            projectName: "ignored",
            liveVolumes: ["\(projectName)_cache"]
        )
        let project = ProjectKey(rawValue: "\(getuid()):\(projectName)")

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", projectName, "down"],
                environment: fixture.environment
            ) == 0
        )

        let store = try SQLiteStateStore(path: fixture.state)
        #expect(try await store.project(key: project)?.provider == .containerCompose)
        #expect(
            try fixture.invocations() == [
                "--project-name \(projectName) down",
                "--project-name \(projectName) volumes --quiet"
            ]
        )
    }

    @Test
    func `native down retains claim when volume reconciliation fails`() async throws {
        let projectName = "unreconciled-volume-project"
        let fixture = try ComposeCommandFixture(
            projectName: "ignored",
            volumeProbeStatus: 23
        )
        let project = ProjectKey(rawValue: "\(getuid()):\(projectName)")

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", projectName, "down"],
                environment: fixture.environment
            ) == 0
        )

        let store = try SQLiteStateStore(path: fixture.state)
        #expect(try await store.project(key: project)?.provider == .containerCompose)
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
    private let provider: ComposeProviderKind
    private let liveVolumes: [String]
    private let volumeProbeStatus: Int32
    private let innerGeneration: Int64?
    private let innerProjectKey: ProjectKey?

    init(
        projectName: String,
        exitStatus: Int32 = 0,
        provider: ComposeProviderKind = .containerCompose,
        liveVolumes: [String] = [],
        volumeProbeStatus: Int32 = 0,
        innerGeneration: Int64? = nil,
        innerProjectKey: ProjectKey? = nil
    ) throws {
        self.exitStatus = exitStatus
        self.provider = provider
        self.liveVolumes = liveVolumes
        self.volumeProbeStatus = volumeProbeStatus
        self.innerGeneration = innerGeneration
        self.innerProjectKey = innerProjectKey
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
          *" volumes --quiet "*)
            if [ -n "${LIVE_VOLUMES-}" ]; then
              printf '%s\n' "$LIVE_VOLUMES"
            fi
            exit "$VOLUME_PROBE_STATUS"
            ;;
          *)
            if [ -n "${INNER_GENERATION-}" ]; then
              sql="UPDATE projects SET desired_generation = $INNER_GENERATION, "
              sql="${sql}reconciliation_state = 'clean' "
              sql="${sql}WHERE project_key = '$INNER_PROJECT_KEY';"
              /usr/bin/sqlite3 "$DEVCONTAINER_STATE" "$sql"
            fi
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
        var result = [
            "DEVCONTAINER_COMPOSE_PROVIDER": provider.rawValue,
            "DEVCONTAINER_CONFIG": root.appendingPathComponent("config.toml").path,
            "DEVCONTAINER_SOCKET": root.appendingPathComponent("docker.sock").path,
            "DEVCONTAINER_STATE": state.path,
            "INVOCATION_LOG": invocationLog.path,
            "LIVE_VOLUMES": liveVolumes.joined(separator: "\n"),
            "VOLUME_PROBE_STATUS": String(volumeProbeStatus),
            "PATH": "/usr/bin:/bin"
        ]
        switch provider {
        case .docker:
            result["DEVCONTAINER_DOCKER_COMPOSE_BIN"] = executable.path
            result["DEVCONTAINER_DOCKER_BIN"] = executable.path
        case .containerCompose:
            result["DEVCONTAINER_COMPOSE_BIN"] = executable.path
        }
        if let innerGeneration, let innerProjectKey {
            result["INNER_GENERATION"] = String(innerGeneration)
            result["INNER_PROJECT_KEY"] = innerProjectKey.rawValue
        }
        return result
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
