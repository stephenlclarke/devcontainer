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

import ContainerAPIClient
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public extension AppleContainerRuntime {
    func startContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws {
        let resolved = try await resolveContainerID(id, context: context)
        try await launchContainerProcess(id: resolved)
        let startedAt = Date()
        try await recordStartedContainer(
            requestedID: id,
            runtimeID: resolved,
            startedAt: startedAt
        )
        let snapshot = try await inspectContainer(
            id: resolved,
            context: context
        )
        try await startPortForwarding(
            snapshot: snapshot,
            startedAt: startedAt
        )
        try await synchronizeNetworkHosts(context: context)
    }

    private func launchContainerProcess(id: String) async throws {
        guard useDirectProcessAPI else {
            try await requireSuccess(
                command(["start", id]),
                operation: "container start"
            )
            return
        }
        let process = try await apiClient.bootstrap(
            id: id,
            stdio: [nil, nil, nil]
        )
        try await process.start()
        let task = Task {
            try await ContainerExit(
                code: process.wait(),
                finishedAt: Date()
            )
        }
        containerExitTasks[id]?.cancel()
        containerExitTasks[id] = task
        containerExits.removeValue(forKey: id)
        Task { [weak self] in
            guard let exit = try? await task.value else {
                return
            }
            await self?.handleContainerExit(exit, id: id)
        }
    }

    internal func handleContainerExit(_ exit: ContainerExit, id: String) async {
        recordContainerExit(exit, id: id)
        containerExitTasks.removeValue(forKey: id)
        await portForwarding.stop(containerID: id)
        try? await synchronizeNetworkHosts(context: RuntimeRequestContext())

        var autoRemove = requestedContainers[id]?.spec.autoRemove ?? false
        if !autoRemove,
           let metadataStore,
           let metadata = try? await metadataStore.containerMetadata(id: id)
        {
            autoRemove = metadata.spec.autoRemove
        }
        if autoRemove {
            scheduleAutomaticRemoval(id: id)
        }
    }

    private func recordStartedContainer(
        requestedID: String,
        runtimeID: String,
        startedAt: Date
    ) async throws {
        startedContainers.insert(requestedID)
        startedContainers.insert(runtimeID)
        containerStartedAt[requestedID] = startedAt
        containerStartedAt[runtimeID] = startedAt
        if let metadataStore,
           try await metadataStore.containerMetadata(id: runtimeID) != nil
        {
            try await metadataStore.markContainerStarted(
                id: runtimeID,
                at: startedAt
            )
        }
    }

    private func startPortForwarding(
        snapshot: ContainerSnapshot,
        startedAt: Date
    ) async throws {
        let resolved = snapshot.runtimeID.rawValue
        do {
            let ports = try await portForwarding.start(
                containerID: resolved,
                bindings: snapshot.spec.ports,
                networkAddresses: snapshot.networkAddresses
            )
            guard ports != snapshot.spec.ports else {
                return
            }
            var spec = snapshot.spec
            spec.ports = ports
            try await metadataStore?.recordContainerMetadata(
                RuntimeContainerMetadata(
                    runtimeID: snapshot.runtimeID,
                    dockerID: snapshot.dockerID,
                    spec: spec,
                    createdAt: snapshot.createdAt,
                    startedAt: startedAt
                )
            )
            let request = RequestedContainer(
                spec: spec,
                createdAt: snapshot.createdAt
            )
            requestedContainers[resolved] = request
            requestedContainers[spec.name] = request
        } catch {
            await portForwarding.stop(containerID: resolved)
            _ = try? await command(["stop", "--time", "0", resolved])
            throw error
        }
    }

    func stopContainer(
        id: String,
        timeout: Duration?,
        context: RuntimeRequestContext
    ) async throws {
        let resolved = try await resolveContainerID(id, context: context)
        var arguments = ["stop"]
        if let timeout {
            let components = timeout.components
            let seconds = components.seconds + (components.attoseconds > 0 ? 1 : 0)
            arguments += ["--time", String(seconds)]
        }
        arguments.append(resolved)
        try await requireSuccess(command(arguments), operation: "container stop")
        await portForwarding.stop(containerID: resolved)
        try await synchronizeNetworkHosts(context: context)
    }

    func killContainer(
        id: String,
        signal: String,
        context: RuntimeRequestContext
    ) async throws {
        let resolved = try await resolveContainerID(id, context: context)
        try await requireSuccess(
            command(["kill", "--signal", signal, resolved]),
            operation: "container kill"
        )
        await portForwarding.stop(containerID: resolved)
        try await synchronizeNetworkHosts(context: context)
    }

    func renameContainer(
        id: String,
        name: String,
        context: RuntimeRequestContext
    ) async throws {
        guard !name.isEmpty else {
            throw DevContainerError(.invalidRequest, message: "container name is empty")
        }
        let snapshot = try await inspectContainer(id: id, context: context)
        let containers = try await listContainers(all: true, labels: [:], context: context)
        guard
            !containers.contains(where: {
                $0.runtimeID != snapshot.runtimeID && $0.spec.name == name
            })
        else {
            throw DevContainerError(.conflict, message: "container name \(name) is already in use")
        }

        var spec = snapshot.spec
        let previousName = spec.name
        spec.name = name
        try await metadataStore?.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: snapshot.runtimeID,
                dockerID: snapshot.dockerID,
                spec: spec,
                createdAt: snapshot.createdAt,
                startedAt: snapshot.startedAt
            )
        )
        let request = RequestedContainer(spec: spec, createdAt: snapshot.createdAt)
        requestedContainers.removeValue(forKey: previousName)
        requestedContainers[snapshot.runtimeID.rawValue] = request
        requestedContainers[snapshot.dockerID.rawValue] = request
        requestedContainers[name] = request
    }

    func removeContainer(
        id: String,
        force: Bool,
        context: RuntimeRequestContext
    ) async throws {
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        var arguments = ["delete"]
        if force {
            arguments.append("--force")
        }
        arguments.append(resolved)
        try await requireSuccess(command(arguments), operation: "container delete")
        await portForwarding.stop(containerID: resolved)
        requestedContainers.removeValue(forKey: id)
        requestedContainers.removeValue(forKey: resolved)
        requestedContainers.removeValue(forKey: snapshot.dockerID.rawValue)
        requestedContainers.removeValue(forKey: snapshot.spec.name)
        startedContainers.remove(id)
        startedContainers.remove(resolved)
        startedContainers.remove(snapshot.dockerID.rawValue)
        startedContainers.remove(snapshot.spec.name)
        containerStartedAt.removeValue(forKey: id)
        containerStartedAt.removeValue(forKey: resolved)
        containerStartedAt.removeValue(forKey: snapshot.dockerID.rawValue)
        containerStartedAt.removeValue(forKey: snapshot.spec.name)
        containerExitTasks.removeValue(forKey: resolved)?.cancel()
        containerExits.removeValue(forKey: resolved)
        try await metadataStore?.removeContainerMetadata(id: resolved)
        try await synchronizeNetworkHosts(context: context)
    }

    func waitContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> Int32 {
        while !Task.isCancelled {
            do {
                let snapshot = try await inspectContainer(id: id, context: context)
                if wasStarted(id: id, snapshot: snapshot),
                   let exit = containerExits[snapshot.runtimeID.rawValue]
                {
                    await portForwarding.stop(
                        containerID: snapshot.runtimeID.rawValue
                    )
                    try await synchronizeNetworkHosts(context: context)
                    if snapshot.spec.autoRemove {
                        scheduleAutomaticRemoval(id: id)
                    }
                    return exit.code
                }
                if wasStarted(id: id, snapshot: snapshot),
                   let task = containerExitTasks[snapshot.runtimeID.rawValue]
                {
                    let exit = try await task.value
                    recordContainerExit(exit, id: snapshot.runtimeID.rawValue)
                    await portForwarding.stop(
                        containerID: snapshot.runtimeID.rawValue
                    )
                    try await synchronizeNetworkHosts(context: context)
                    if snapshot.spec.autoRemove {
                        scheduleAutomaticRemoval(id: id)
                    }
                    return exit.code
                }
                if snapshot.state == .stopped, wasStarted(id: id, snapshot: snapshot) {
                    await portForwarding.stop(
                        containerID: snapshot.runtimeID.rawValue
                    )
                    let exitCode = snapshot.exitCode ?? 0
                    try await synchronizeNetworkHosts(context: context)
                    if snapshot.spec.autoRemove {
                        scheduleAutomaticRemoval(id: id)
                    }
                    return exitCode
                }
            } catch let error as DevContainerError where error.code == .notFound {
                if requestedContainers[id] == nil {
                    return 0
                }
            }
            try await Task.sleep(for: .milliseconds(200))
        }
        throw DevContainerError(.cancelled, message: "container wait was cancelled")
    }

    private func recordContainerExit(_ exit: ContainerExit, id: String) {
        containerExits[id] = exit
    }

    func containerLogs(
        id: String,
        follow: Bool,
        standardOutput: Bool,
        standardError: Bool,
        context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        let resolved = try await resolveContainerID(id, context: context)
        if useDirectProcessAPI, !follow {
            let data = try await exactContainerLog(id: resolved)
            let channel: RuntimeIOChannel? =
                if standardOutput {
                    .standardOutput
                } else if standardError {
                    .standardError
                } else {
                    nil
                }
            return AsyncThrowingStream { continuation in
                if let channel, !data.isEmpty {
                    continuation.yield(RuntimeIOFrame(channel: channel, data: data))
                }
                continuation.finish()
            }
        }
        var arguments = ["logs"]
        if follow {
            arguments.append("--follow")
        }
        arguments.append(resolved)
        return try process(arguments).frames
    }

    func attachContainer(
        id: String,
        terminal _: Bool,
        context: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession {
        ApplePollingLogSession {
            try await self.pollLogs(id: id, context: context)
        }
    }

    func createExec(
        containerID: String,
        spec: ExecSpec,
        context: RuntimeRequestContext
    ) async throws -> ExecSnapshot {
        let container = try await inspectContainer(id: containerID, context: context)
        guard container.state == .running else {
            throw DevContainerError(.conflict, message: "container \(containerID) is not running")
        }
        let exec = ExecSnapshot(
            id: .random(),
            containerID: container.runtimeID,
            spec: spec
        )
        execs[exec.id] = exec
        return exec
    }

    func startExec(
        id: ExecID,
        context _: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession {
        guard var exec = execs[id] else {
            throw DevContainerError(.notFound, message: "exec \(id) was not found")
        }
        guard !exec.running, exec.exitCode == nil else {
            throw DevContainerError(.conflict, message: "exec \(id) has already started")
        }
        var arguments = ["exec"]
        for (key, value) in exec.spec.environment.sorted(by: { $0.key < $1.key }) {
            arguments += ["--env", "\(key)=\(value)"]
        }
        if let workingDirectory = exec.spec.workingDirectory {
            arguments += ["--workdir", workingDirectory]
        }
        if let user = exec.spec.user {
            arguments += ["--user", user]
        }
        if exec.spec.terminal {
            arguments.append("--tty")
        }
        if exec.spec.attachStandardInput {
            arguments.append("--interactive")
        }
        arguments.append(exec.containerID.rawValue)
        arguments += exec.spec.command
        exec.running = true
        execs[id] = exec
        let session: any RuntimeProcessSession
        do {
            session =
                if useDirectProcessAPI {
                    try await startDirectProcess(
                        containerID: exec.containerID.rawValue,
                        spec: exec.spec
                    )
                } else {
                    try process(arguments)
                }
        } catch {
            finishExec(id: id, exitCode: 255)
            throw error
        }
        Task {
            do {
                let exitCode = try await session.wait()
                self.finishExec(id: id, exitCode: exitCode)
            } catch {
                self.finishExec(id: id, exitCode: 255)
            }
        }
        return session
    }

    private func startDirectProcess(
        containerID: String,
        spec: ExecSpec
    ) async throws -> AppleDirectProcessSession {
        let previous = directProcessLaunchTail
        let client = apiClient
        let launch = Task {
            await previous?.value
            return try await AppleDirectProcessSession.create(
                containerID: containerID,
                spec: spec,
                client: client
            )
        }
        directProcessLaunchTail = Task {
            _ = try? await launch.value
        }
        return try await launch.value
    }

    func inspectExec(
        id: ExecID,
        context _: RuntimeRequestContext
    ) async throws -> ExecSnapshot {
        guard let exec = execs[id] else {
            throw DevContainerError(.notFound, message: "exec \(id) was not found")
        }
        return exec
    }

    private func finishExec(id: ExecID, exitCode: Int32) {
        guard var exec = execs[id] else {
            return
        }
        exec.running = false
        exec.exitCode = exitCode
        execs[id] = exec
    }
}
