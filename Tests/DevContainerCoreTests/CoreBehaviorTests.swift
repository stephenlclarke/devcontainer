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

import DevContainerCore
import DevContainerModel
import DevContainerState
import DevContainerTestSupport
import Foundation
import Testing

@Suite(.serialized)
struct CoreBehaviorTests {
    @Test
    func `configuration round trips and rejects invalid values`() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("config.toml")
        let value = DevContainerConfiguration(
            backend: .containerCompose,
            composeProvider: .containerCompose,
            socket: "/tmp/example.sock",
            strictCompatibility: false
        )
        try DevContainerConfigurationStore.save(value, to: path)
        #expect(
            try DevContainerConfigurationStore.load(
                from: path,
                defaultSocket: "unused"
            ) == value
        )

        try Data("backend = \"invalid\"\n".utf8).write(to: path)
        #expect(throws: DevContainerError.self) {
            try DevContainerConfigurationStore.load(from: path, defaultSocket: "socket")
        }
    }

    @Test
    func `configuration defaults expands home and rejects malformed policy`() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("config.toml")

        let defaults = try DevContainerConfigurationStore.load(
            from: path,
            defaultSocket: "default.sock"
        )
        #expect(defaults == DevContainerConfiguration(socket: "default.sock"))

        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: false
        )
        try Data(
            """
            socket = "~/Library/Caches/devcontainer.sock"

            [compose]
            provider = "container-compose"

            [compatibility]
            strict = true
            """.utf8
        ).write(to: path)
        let expanded = try DevContainerConfigurationStore.load(
            from: path,
            defaultSocket: "unused"
        )
        #expect(
            expanded.socket
                == FileManager.default.homeDirectoryForCurrentUser.path
                + "/Library/Caches/devcontainer.sock"
        )
        #expect(expanded.composeProvider == .containerCompose)
        #expect(expanded.strictCompatibility)

        for invalid in [
            "not-an-assignment\n",
            "[compose]\nprovider = \"invalid\"\n",
            "[compatibility]\nstrict = maybe\n"
        ] {
            try Data(invalid.utf8).write(to: path)
            #expect(throws: DevContainerError.self) {
                try DevContainerConfigurationStore.load(
                    from: path,
                    defaultSocket: "unused"
                )
            }
        }
    }

    @Test
    func `labels project and translate without overwriting conflicts`() throws {
        let native = "com.apple.container.compose.project"
        let docker = "com.docker.compose.project"
        let projected = try RuntimeLabels.projectComposeLabels([native: "demo"])
        #expect(projected[docker] == "demo")
        #expect(
            try RuntimeLabels.translateDockerFilters([docker: "demo"])
                == [native: "demo"]
        )
        #expect(throws: DevContainerError.self) {
            try RuntimeLabels.projectComposeLabels([native: "one", docker: "two"])
        }
        #expect(throws: DevContainerError.self) {
            try RuntimeLabels.translateDockerFilters([native: "one", docker: "two"])
        }
        let labels = RuntimeLabels.projectLabels(
            project: ProjectKey(rawValue: "501:demo"),
            provider: .stock,
            generation: 2,
            operation: OperationID(rawValue: "operation"),
            configurationHash: "hash"
        )
        #expect(labels[RuntimeLabels.generation] == "2")
    }

    @Test
    func `registry requires explicit provider registration`() async throws {
        let registry = RuntimeRegistry()
        await #expect(throws: DevContainerError.self) {
            try await registry.runtime(for: .stock)
        }
        let runtime = InMemoryRuntime()
        await registry.register(runtime, for: .stock)
        _ = try await registry.runtime(for: .stock)
        await registry.unregister(provider: .stock)
        await #expect(throws: DevContainerError.self) {
            try await registry.runtime(for: .stock)
        }
    }

    @Test
    func `coordinator commits success and records failure`() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try SQLiteStateStore(path: directory.appendingPathComponent("state.sqlite"))
        let coordinator = ProjectCoordinator(store: store)
        let key = ProjectKey(rawValue: "501:coordinator")
        let result = try await coordinator.withMutation(
            request: ProjectMutation(
                project: key,
                provider: .stock,
                configurationHash: "configuration",
                requestKind: "create",
                requestHash: "request"
            )
        ) { context in
            #expect(context.project == key)
            #expect(context.generation == 1)
            return "done"
        }
        #expect(result == "done")
        #expect(try await coordinator.recoveryOperations().isEmpty)
        #expect(try await coordinator.provider(for: key) == .stock)

        await #expect(throws: DevContainerError.self) {
            try await coordinator.withMutation(
                request: ProjectMutation(
                    project: key,
                    provider: .stock,
                    configurationHash: "configuration",
                    requestKind: "start",
                    requestHash: "second"
                )
            ) { _ in
                throw DevContainerError(.conflict, message: "injected failure")
            } as String
        }
        let record = try await store.project(key: key)
        #expect(record?.reconciliationState == .failed)
        try await coordinator.drainAndReset(project: key)
        #expect(try await coordinator.provider(for: key) == nil)
    }

    private func temporaryDirectory() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-core-\(UUID().uuidString)", isDirectory: true)
    }
}
