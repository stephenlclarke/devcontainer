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
    internal func beginContainerLifecycleMutation(id: String) -> UUID {
        let registration = UUID()
        containerLifecycleMutationRevision &+= 1
        containerLifecycleMutationRegistrations[id, default: []].insert(registration)
        return registration
    }

    internal func includeContainerLifecycleMutation(id: String, registration: UUID) {
        containerLifecycleMutationRegistrations[id, default: []].insert(registration)
    }

    internal func includeContainerLifecycleMutation(
        identifiers: Set<String>,
        registration: UUID
    ) {
        for identifier in identifiers {
            includeContainerLifecycleMutation(
                id: identifier,
                registration: registration
            )
        }
    }

    internal func finishContainerLifecycleMutation(
        identifiers: Set<String>,
        registration: UUID
    ) {
        for identifier in identifiers {
            containerLifecycleMutationRegistrations[identifier]?.remove(registration)
            if containerLifecycleMutationRegistrations[identifier]?.isEmpty == true {
                containerLifecycleMutationRegistrations.removeValue(forKey: identifier)
            }
        }
        containerLifecycleMutationRevision &+= 1
    }

    func startContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws {
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let resolved = try await resolveContainerID(id, context: context)
        mutationIdentifiers.insert(resolved)
        includeContainerLifecycleMutation(id: resolved, registration: mutation)
        if let operation = containerStartOperations[resolved] {
            return try await operation.task.value
        }
        let registration = UUID()
        let task = Task {
            try await self.performStartContainer(
                requestedID: id,
                runtimeID: resolved,
                context: context
            )
        }
        containerStartOperations[resolved] = ContainerStartOperation(
            registration: registration,
            kind: .start,
            task: task
        )
        do {
            try await task.value
            finishStartOperation(id: resolved, registration: registration)
        } catch {
            finishStartOperation(id: resolved, registration: registration)
            throw error
        }
    }

    private func performStartContainer(
        requestedID: String,
        runtimeID resolved: String,
        context: RuntimeRequestContext
    ) async throws {
        try await launchContainerProcess(id: resolved)
        // Runtime bootstrap recreates the guest's default /etc/hosts, even
        // when the container incarnation itself is unchanged.
        managedHostsState.removeValue(forKey: resolved)
        await signalEventPollers()
        let startedAt = Date()
        try await recordStartedContainer(
            requestedID: requestedID,
            runtimeID: resolved,
            startedAt: startedAt
        )
        let inventory = try await listContainers(
            all: true,
            labels: [:],
            context: context
        )
        let snapshot = try resolvedContainerSnapshot(id: resolved, in: inventory)
        try await startPortForwarding(
            snapshot: snapshot,
            startedAt: startedAt
        )
        try await synchronizeNetworkHosts(context: context, containers: inventory)
        await signalEventPollers()
    }

    private func finishStartOperation(id: String, registration: UUID) {
        guard containerStartOperations[id]?.registration == registration else {
            return
        }
        containerStartOperations.removeValue(forKey: id)
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
        let registration = UUID()
        containerExitTasks[id]?.cancel()
        containerExitTasks[id] = task
        containerExitRegistrations[id] = registration
        containerExits.removeValue(forKey: id)
        Task { [weak self] in
            guard let exit = try? await task.value else {
                return
            }
            await self?.handleContainerExit(
                exit,
                id: id,
                registration: registration
            )
        }
    }

    internal func handleContainerExit(
        _ exit: ContainerExit,
        id: String,
        registration: UUID? = nil
    ) async {
        if let registration,
           containerExitRegistrations[id] != registration
        {
            return
        }
        let mutation = beginContainerLifecycleMutation(id: id)
        defer {
            finishContainerLifecycleMutation(
                identifiers: [id],
                registration: mutation
            )
        }
        recordContainerExit(exit, id: id)
        containerExitTasks.removeValue(forKey: id)
        containerExitRegistrations.removeValue(forKey: id)
        await signalEventPollers()
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
        await signalEventPollers()
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

    func restorePortForwarding(
        context: RuntimeRequestContext
    ) async throws {
        let containers = try await listContainers(
            all: true,
            labels: [:],
            context: context
        )
        for snapshot in containers where snapshot.state == .running {
            try await startPortForwarding(
                snapshot: snapshot,
                startedAt: snapshot.startedAt ?? Date(),
                stopContainerOnFailure: false
            )
        }
    }

    private func startPortForwarding(
        snapshot: DevContainerModel.ContainerSnapshot,
        startedAt: Date,
        stopContainerOnFailure: Bool = true
    ) async throws {
        let resolved = snapshot.runtimeID.rawValue
        do {
            let optionSupport = try await supportedCreateOptions()
            let emulated = snapshot.spec.ports.filter {
                Self.requiresHostForwarding(
                    $0,
                    nativePublishingSupported: optionSupport.publish
                )
            }
            var replacements = try await portForwarding.start(
                containerID: resolved,
                bindings: emulated,
                networkAddresses: snapshot.networkAddresses
            ).makeIterator()
            let ports = snapshot.spec.ports.map { binding in
                guard Self.requiresHostForwarding(
                    binding,
                    nativePublishingSupported: optionSupport.publish
                ) else {
                    return binding
                }
                return replacements.next() ?? binding
            }
            guard ports != snapshot.spec.ports else {
                return
            }
            try await recordResolvedPortBindings(
                ports,
                snapshot: snapshot,
                startedAt: startedAt
            )
        } catch {
            await portForwarding.stop(containerID: resolved)
            if stopContainerOnFailure {
                _ = try? await command(["stop", "--time", "0", resolved])
            }
            throw error
        }
    }

    private func recordResolvedPortBindings(
        _ ports: [PortBinding],
        snapshot: DevContainerModel.ContainerSnapshot,
        startedAt: Date
    ) async throws {
        var spec = snapshot.spec
        spec.ports = ports
        try await metadataStore?.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: snapshot.runtimeID,
                dockerID: snapshot.dockerID,
                imageID: snapshot.imageID,
                spec: spec,
                createdAt: snapshot.createdAt,
                startedAt: startedAt
            )
        )
        let request = RequestedContainer(
            spec: spec,
            imageID: snapshot.imageID,
            createdAt: snapshot.createdAt
        )
        requestedContainers[snapshot.runtimeID.rawValue] = request
        requestedContainers[spec.name] = request
    }

    private static func requiresHostForwarding(
        _ binding: PortBinding,
        nativePublishingSupported: Bool
    ) -> Bool {
        guard binding.published != false else {
            return false
        }
        return binding.hostForwarded
            ?? (!nativePublishingSupported || (binding.hostPort ?? 0) == 0)
    }

    func stopContainer(
        id: String,
        timeout: Duration?,
        context: RuntimeRequestContext
    ) async throws {
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let resolved = try await resolveContainerID(id, context: context)
        mutationIdentifiers.insert(resolved)
        includeContainerLifecycleMutation(id: resolved, registration: mutation)
        var arguments = ["stop"]
        if let timeout {
            let components = timeout.components
            let seconds = components.seconds
                + (components.attoseconds > 0 ? 1 : 0)
            arguments += ["--time", String(seconds)]
        }
        arguments.append(resolved)
        try await requireSuccess(
            command(arguments),
            operation: "container stop"
        )
        await signalEventPollers()
        await portForwarding.stop(containerID: resolved)
        try await synchronizeNetworkHosts(context: context)
        await signalEventPollers()
    }

    func restartContainer(
        id: String,
        timeout: Duration?,
        context: RuntimeRequestContext
    ) async throws {
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let resolved = try await resolveContainerID(id, context: context)
        mutationIdentifiers.insert(resolved)
        includeContainerLifecycleMutation(id: resolved, registration: mutation)
        while let operation = containerStartOperations[resolved] {
            if operation.kind == .restart {
                return try await operation.task.value
            }
            try await operation.task.value
            finishStartOperation(
                id: resolved,
                registration: operation.registration
            )
        }
        let registration = UUID()
        let task = Task {
            try await self.performRestartContainer(
                requestedID: id,
                runtimeID: resolved,
                timeout: timeout,
                context: context
            )
        }
        containerStartOperations[resolved] = ContainerStartOperation(
            registration: registration,
            kind: .restart,
            task: task
        )
        do {
            try await task.value
            finishStartOperation(id: resolved, registration: registration)
        } catch {
            finishStartOperation(id: resolved, registration: registration)
            throw error
        }
    }

    private func performRestartContainer(
        requestedID id: String,
        runtimeID resolved: String,
        timeout: Duration?,
        context: RuntimeRequestContext
    ) async throws {
        var arguments = [useDirectProcessAPI ? "stop" : "restart"]
        if let timeout {
            let components = timeout.components
            let seconds = components.seconds
                + (components.attoseconds > 0 ? 1 : 0)
            arguments += ["--time", String(seconds)]
        }
        arguments.append(resolved)
        try await requireSuccess(
            command(arguments),
            operation: "container restart"
        )
        if useDirectProcessAPI {
            try await launchContainerProcess(id: resolved)
        }

        // Restart/bootstrap recreates the guest's default /etc/hosts.
        managedHostsState.removeValue(forKey: resolved)
        await portForwarding.stop(containerID: resolved)
        await signalEventPollers()
        let startedAt = Date()
        try await recordStartedContainer(
            requestedID: id,
            runtimeID: resolved,
            startedAt: startedAt
        )
        let inventory = try await listContainers(
            all: true,
            labels: [:],
            context: context
        )
        let snapshot = try resolvedContainerSnapshot(id: resolved, in: inventory)
        try await startPortForwarding(snapshot: snapshot, startedAt: startedAt)
        try await synchronizeNetworkHosts(context: context, containers: inventory)
        await signalEventPollers()
    }

    func killContainer(
        id: String,
        signal: String,
        context: RuntimeRequestContext
    ) async throws {
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let resolved = try await resolveContainerID(id, context: context)
        mutationIdentifiers.insert(resolved)
        includeContainerLifecycleMutation(id: resolved, registration: mutation)
        try await requireSuccess(
            command(["kill", "--signal", signal, resolved]),
            operation: "container kill"
        )
        await signalEventPollers()
        await portForwarding.stop(containerID: resolved)
        try await synchronizeNetworkHosts(context: context)
        await signalEventPollers()
    }

    func renameContainer(
        id: String,
        name: String,
        context: RuntimeRequestContext
    ) async throws {
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        guard !name.isEmpty else {
            throw DevContainerError(.invalidRequest, message: "container name is empty")
        }
        let containers = try await listContainers(all: true, labels: [:], context: context)
        let snapshot = try resolvedContainerSnapshot(id: id, in: containers)
        mutationIdentifiers.formUnion([
            snapshot.runtimeID.rawValue,
            snapshot.dockerID.rawValue,
            snapshot.spec.name,
            name
        ])
        includeContainerLifecycleMutation(
            identifiers: mutationIdentifiers,
            registration: mutation
        )
        guard
            !containers.contains(where: {
                $0.runtimeID != snapshot.runtimeID && $0.spec.name == name
            })
        else {
            throw DevContainerError(.conflict, message: "container name \(name) is already in use")
        }

        try await recordRenamedContainer(snapshot: snapshot, name: name)
        await signalEventPollers()
    }

    private func recordRenamedContainer(
        snapshot: DevContainerModel.ContainerSnapshot,
        name: String
    ) async throws {
        var spec = snapshot.spec
        let previousName = spec.name
        spec.name = name
        try await metadataStore?.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: snapshot.runtimeID,
                dockerID: snapshot.dockerID,
                imageID: snapshot.imageID,
                spec: spec,
                createdAt: snapshot.createdAt,
                startedAt: snapshot.startedAt
            )
        )
        let request = RequestedContainer(
            spec: spec,
            imageID: snapshot.imageID,
            createdAt: snapshot.createdAt
        )
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
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        mutationIdentifiers.formUnion([
            resolved,
            snapshot.dockerID.rawValue,
            snapshot.spec.name
        ])
        includeContainerLifecycleMutation(
            identifiers: mutationIdentifiers,
            registration: mutation
        )
        var arguments = ["delete"]
        if force {
            arguments.append("--force")
        }
        arguments.append(resolved)
        try await requireSuccess(
            command(arguments),
            operation: "container delete"
        )
        await signalEventPollers()
        await portForwarding.stop(containerID: resolved)
        requestedContainers.removeValue(forKey: id)
        requestedContainers.removeValue(forKey: resolved)
        requestedContainers.removeValue(forKey: snapshot.dockerID.rawValue)
        requestedContainers.removeValue(forKey: snapshot.spec.name)
        managedHostsState.removeValue(forKey: resolved)
        startedContainers.remove(id)
        startedContainers.remove(resolved)
        startedContainers.remove(snapshot.dockerID.rawValue)
        startedContainers.remove(snapshot.spec.name)
        containerStartedAt.removeValue(forKey: id)
        containerStartedAt.removeValue(forKey: resolved)
        containerStartedAt.removeValue(forKey: snapshot.dockerID.rawValue)
        containerStartedAt.removeValue(forKey: snapshot.spec.name)
        containerExitTasks.removeValue(forKey: resolved)?.cancel()
        containerExitRegistrations.removeValue(forKey: resolved)
        containerExits.removeValue(forKey: resolved)
        try await metadataStore?.removeContainerMetadata(id: resolved)
        try await synchronizeNetworkHosts(context: context)
        await signalEventPollers()
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
        if let workingDirectory = exec.spec.workingDirectory,
           !workingDirectory.isEmpty
        {
            arguments += ["--workdir", workingDirectory]
        }
        if let user = exec.spec.user, !user.isEmpty {
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
            session = try await execProcessSession(
                exec,
                arguments: arguments
            )
        } catch {
            finishExec(id: id, exitCode: 255)
            throw error
        }
        try await applyInitialTerminalSize(session, exec: exec, id: id)
        return TrackedAppleProcessSession(session: session) { [weak self] exitCode in
            await self?.finishExec(id: id, exitCode: exitCode)
        }
    }

    private func execProcessSession(
        _ exec: ExecSnapshot,
        arguments: [String]
    ) async throws -> any RuntimeProcessSession {
        // Use Apple's typed process API for non-terminal exec, including
        // duplex streams. The CLI intermittently fails to propagate stdin EOF
        // after large writes. Terminal exec instead needs the host PTY wrapper
        // so VS Code receives Docker-compatible TTY and resize behaviour.
        if useDirectProcessAPI, !exec.spec.terminal {
            return try await startDirectProcess(
                containerID: exec.containerID.rawValue,
                spec: exec.spec
            )
        }
        if useDirectProcessAPI, exec.spec.terminal {
            return try terminalProcess(arguments)
        }
        return try process(arguments)
    }

    private func applyInitialTerminalSize(
        _ session: any RuntimeProcessSession,
        exec: ExecSnapshot,
        id: ExecID
    ) async throws {
        guard exec.spec.terminal,
              let width = exec.spec.terminalWidth,
              let height = exec.spec.terminalHeight,
              width > 0,
              height > 0
        else {
            return
        }
        do {
            try await session.resize(width: width, height: height)
        } catch {
            await session.cancel()
            finishExec(id: id, exitCode: 255)
            throw error
        }
    }

    private func startDirectProcess(
        containerID: String,
        spec: ExecSpec
    ) async throws -> AppleDirectProcessSession {
        let previous = directProcessLaunchTail
        // Each direct process owns an independent XPC connection so its
        // transferred standard-I/O descriptors cannot share client state.
        let client = ContainerClient()
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
