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
        #expect(project?.provider == .stock)
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
    func `enhanced runtime claims projects with its engine provider`() async throws {
        let fixture = try ComposeCommandFixture(
            projectName: "enhanced-project",
            backend: .containerCompose
        )

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "enhanced-project", "up", "--detach"],
                environment: fixture.environment
            ) == 0
        )

        let store = try SQLiteStateStore(path: fixture.state)
        #expect(
            try await store.project(
                key: ProjectKey(rawValue: "\(getuid()):enhanced-project")
            )?.provider == .containerCompose
        )
    }

    @Test
    func `native compose never falls back to Docker or Colima executables`() async throws {
        let fixture = try ComposeCommandFixture(projectName: "dockerless-project")

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "dockerless-project", "up", "--detach"],
                environment: fixture.environment
            ) == 0
        )
        #expect(try fixture.trapInvocations().isEmpty)
        let packagedAdapter = Paths(environment: fixture.environment)
            .dockerCompatibility.path
        #expect(try fixture.runtimeSelections() == [
            "stock|/fixtures/container|\(packagedAdapter)|\(fixture.socket.path)"
                + "|io.github.stephenlclarke.container.compose.network-aliases.v1"
                + "|unix://\(fixture.socket.path)"
        ])
        #expect(
            try fixture.invocations() == [
                "--project-name dockerless-project up --detach"
            ]
        )
    }

    @Test
    func `native compose rejects explicit Docker and Colima executables`() async throws {
        let fixture = try ComposeCommandFixture(projectName: "dockerless-project")

        for executable in ["docker-compose", "colima"] {
            var environment = fixture.environment
            environment["DEVCONTAINER_COMPOSE_BIN"] = fixture
                .forbiddenExecutable(named: executable).path
            await #expect(throws: DevContainerError.self) {
                _ = try await DevContainerComposeCommand.run(
                    arguments: ["--project-name", "dockerless-project", "up"],
                    environment: environment
                )
            }
        }
        #expect(try fixture.trapInvocations().isEmpty)
        #expect(try fixture.invocations().isEmpty)
    }

    @Test
    func `compatibility adapter environment override is ignored`() async throws {
        let fixture = try ComposeCommandFixture(projectName: "dockerless-project")
        var environment = fixture.environment
        let forbidden = fixture.forbiddenExecutable(named: "docker")
        environment["DEVCONTAINER_DOCKER_BIN"] = forbidden.path

        #expect(
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "dockerless-project", "up"],
                environment: environment
            ) == 0
        )
        #expect(try fixture.trapInvocations().isEmpty)
        #expect(try fixture.runtimeSelections().allSatisfy { !$0.contains(forbidden.path) })
    }

    @Test
    func `external compose must identify the native provider source`() async throws {
        let fixture = try ComposeCommandFixture(
            projectName: "dockerless-project",
            composeSource: "example/foreign-compose"
        )

        await #expect(throws: DevContainerError.self) {
            _ = try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "dockerless-project", "up"],
                environment: fixture.environment
            )
        }
        #expect(try fixture.invocations().isEmpty)
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
            )?.provider == .stock
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
        #expect(try await store.project(key: project)?.provider == .stock)
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
        #expect(try await store.project(key: project)?.provider == .stock)
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
                provider: .stock,
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
                    provider: .stock,
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
    let socket: URL
    private let executable: URL
    private let invocationLog: URL
    private let trapLog: URL
    private let runtimeSelectionLog: URL
    private let backend: BackendProvider
    private let exitStatus: Int32
    private let liveVolumes: [String]
    private let volumeProbeStatus: Int32

    init(
        projectName: String,
        backend: BackendProvider = .stock,
        exitStatus: Int32 = 0,
        liveVolumes: [String] = [],
        volumeProbeStatus: Int32 = 0,
        composeSource: String = "stephenlclarke/container-compose"
    ) throws {
        self.backend = backend
        self.exitStatus = exitStatus
        self.liveVolumes = liveVolumes
        self.volumeProbeStatus = volumeProbeStatus
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "devcontainer-compose-cli-tests-\(UUID().uuidString)",
                isDirectory: true
            )
        state = root.appendingPathComponent("state.sqlite")
        socket = root.appendingPathComponent("engine.sock")
        executable = root.appendingPathComponent("container-compose")
        invocationLog = root.appendingPathComponent("invocations.log")
        trapLog = root.appendingPathComponent("forbidden-executables.log")
        runtimeSelectionLog = root.appendingPathComponent("runtime-selections.log")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        try Data(Self.composeScript(
            projectName: projectName,
            exitStatus: exitStatus,
            composeSource: composeSource
        ).utf8).write(to: executable, options: .atomic)
        #expect(chmod(executable.path, S_IRWXU) == 0)
        for forbidden in ["docker", "docker-compose", "colima"] {
            let trap = root.appendingPathComponent(forbidden)
            let trapScript = """
            #!/bin/sh
            printf '%s\n' '\(forbidden)' >> '\(trapLog.path)'
            exit 97
            """
            try Data(trapScript.utf8).write(to: trap, options: .atomic)
            #expect(chmod(trap.path, S_IRWXU) == 0)
        }
    }

    private static func composeScript(
        projectName: String,
        exitStatus: Int32,
        composeSource: String
    ) -> String {
        """
        #!/bin/sh
        set -eu
        if [ "$*" = "version --format json" ]; then
          test -z "${DOCKER_CONTEXT-}"
          test -z "${DOCKER_CONFIG-}"
          printf '%s\n' '{"version":"0.15.0","source":"\(composeSource)","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","containerDistribution":"apple"}'
          exit 0
        fi
        printf '%s\n' "$*" >> "$INVOCATION_LOG"
        printf '%s|%s|%s|%s|%s|%s\n' \
          "$CONTAINER_COMPOSE_RUNTIME_PROFILE" \
          "$CONTAINER_COMPOSE_CONTAINER" \
          "$CONTAINER_BIN" \
          "$CONTAINER_COMPOSE_ENGINE_SOCKET" \
          "${CONTAINER_COMPOSE_RUNTIME_CAPABILITIES-}" \
          "$DOCKER_HOST" >> "$RUNTIME_SELECTION_LOG"
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
            exit \(exitStatus)
            ;;
        esac
        """
    }

    deinit {
        try? FileManager.default.removeItem(at: root)
    }

    var environment: [String: String] {
        var result = [
            "DEVCONTAINER_COMPOSE_PROVIDER": ComposeProviderKind.containerCompose.rawValue,
            "DEVCONTAINER_BACKEND": backend.rawValue,
            "DEVCONTAINER_CONFIG": root.appendingPathComponent("config.toml").path,
            "DEVCONTAINER_SOCKET": socket.path,
            "DOCKER_HOST": "unix:///tmp/ambient-docker.sock",
            "DOCKER_CONTEXT": "desktop-linux",
            "DOCKER_CONFIG": "/tmp/docker-config",
            "DEVCONTAINER_STATE": state.path,
            "INVOCATION_LOG": invocationLog.path,
            "RUNTIME_SELECTION_LOG": runtimeSelectionLog.path,
            "LIVE_VOLUMES": liveVolumes.joined(separator: "\n"),
            "VOLUME_PROBE_STATUS": String(volumeProbeStatus),
            "PATH": "\(root.path):/usr/bin:/bin",
            "DEVCONTAINER_CONTAINER_BIN": "/fixtures/container"
        ]
        result["DEVCONTAINER_COMPOSE_BIN"] = executable.path
        result["DEVCONTAINER_DOCKER_BIN"] = "/fixtures/ignored-devcontainer-docker"
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

    func trapInvocations() throws -> [String] {
        guard FileManager.default.fileExists(atPath: trapLog.path) else {
            return []
        }
        return try String(contentsOf: trapLog, encoding: .utf8)
            .split(separator: "\n")
            .map(String.init)
    }

    func forbiddenExecutable(named name: String) -> URL {
        root.appendingPathComponent(name)
    }

    func runtimeSelections() throws -> [String] {
        guard FileManager.default.fileExists(atPath: runtimeSelectionLog.path) else {
            return []
        }
        return try String(contentsOf: runtimeSelectionLog, encoding: .utf8)
            .split(separator: "\n")
            .map(String.init)
    }
}

@Test
func `packaged compose is resolved beside the dispatcher archive`() {
    #expect(
        Paths.bundledComposePath(
            executablePath: "/opt/homebrew/Cellar/devcontainer/1.0.2/bin/devcontainer-compose"
        )
            == "/opt/homebrew/Cellar/devcontainer/1.0.2/libexec/devcontainer-compose/bin/compose"
    )
}

@Test
func `packaged Docker compatibility command is resolved beside the dispatcher`() {
    #expect(
        Paths.bundledDockerCompatibilityPath(
            executablePath: "/opt/homebrew/Cellar/devcontainer/1.0.2/bin/devcontainer-compose"
        )
            == "/opt/homebrew/Cellar/devcontainer/1.0.2/bin/devcontainer-docker"
    )
}
