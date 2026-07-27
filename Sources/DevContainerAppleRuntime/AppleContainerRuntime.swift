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
import Darwin
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public actor AppleContainerRuntime: DevContainerRuntime {
    private struct RequestedContainer {
        var spec: ContainerSpec
        var createdAt: Date?
    }

    private static let dockerIDLabel = "io.github.stephenlclarke.devcontainer.docker-id"

    public let executable: URL
    private let environment: [String: String]
    private let useDirectProcessAPI: Bool
    private let metadataStore: (any RuntimeMetadataStore)?
    private let managedVolumes: ManagedVolumeStore
    private let portForwarding = PortForwarding()
    private var execs: [ExecID: ExecSnapshot] = [:]
    private var requestedContainers: [String: RequestedContainer] = [:]
    private var startedContainers: Set<String> = []
    private var containerStartedAt: [String: Date] = [:]

    public init(
        executable: URL,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        useDirectProcessAPI: Bool = true,
        metadataStore: (any RuntimeMetadataStore)? = nil,
        volumeRoot: URL? = nil
    ) throws {
        let resolved = executable.standardizedFileURL
        guard resolved.isFileURL, FileManager.default.isExecutableFile(atPath: resolved.path) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "Apple container CLI is not executable at \(resolved.path)"
            )
        }
        self.executable = resolved
        self.environment = Self.filteredEnvironment(environment)
        self.useDirectProcessAPI = useDirectProcessAPI
        self.metadataStore = metadataStore
        managedVolumes = try ManagedVolumeStore(
            root: volumeRoot ?? Self.defaultVolumeRoot
        )
    }

    public func descriptor(context _: RuntimeRequestContext) async throws -> ProtocolDescriptor {
        let result = try await command(["system", "version", "--format", "json"])
        try requireSuccess(result, operation: "version probe")
        let records = try JSONDecoder().decode([AppleVersionRecord].self, from: result.standardOutput)
        guard let record = records.first(where: { $0.appName == "container" }) ?? records.first else {
            throw DevContainerError(.providerProtocolMismatch, message: "Apple container returned no version record")
        }
        return ProtocolDescriptor(
            provider: .stock,
            providerVersion: record.version,
            providerCommit: record.commit ?? "unspecified",
            distribution: record.distribution ?? "apple",
            capabilities: [
                .archive: .emulated,
                .attach: .emulated,
                .build: .native,
                .containers: .native,
                .events: .emulated,
                .exec: .native,
                .images: .native,
                .networks: .native,
                .portForwarding: .emulated,
                .registryAuthentication: .native,
                .volumes: .emulated
            ]
        )
    }

    /// Releases all host-side compatibility resources owned by this adapter.
    public func shutdown() async {
        await portForwarding.stopAll()
    }

    public func listContainers(
        all: Bool,
        labels: [String: String],
        context _: RuntimeRequestContext
    ) async throws -> [ContainerSnapshot] {
        var arguments = ["list"]
        if all {
            arguments.append("--all")
        }
        arguments += ["--format", "json"]
        let result = try await command(arguments)
        try requireSuccess(result, operation: "container list")
        let values = try parseJSONObjectArray(result.standardOutput)
        var snapshots: [ContainerSnapshot] = []
        for value in values {
            var snapshot = try containerSnapshot(value)
            if let metadataStore,
               let metadata = try await metadataStore.containerMetadata(
                   id: snapshot.runtimeID.rawValue
               )
            {
                if Self.sameContainerIncarnation(
                    metadataCreatedAt: metadata.createdAt,
                    observedCreatedAt: snapshot.createdAt
                ) {
                    snapshot = apply(metadata: metadata, to: snapshot)
                } else {
                    // Native providers may delete and recreate a container
                    // under the same stable Compose name. Old Docker identity
                    // metadata must not be projected onto that new instance.
                    try await metadataStore.removeContainerMetadata(
                        id: snapshot.runtimeID.rawValue
                    )
                }
            }
            guard labels.allSatisfy({ key, expected in
                guard let actual = snapshot.spec.labels[key] else {
                    return false
                }
                return expected.isEmpty || actual == expected
            }) else {
                continue
            }
            snapshots.append(snapshot)
        }
        return snapshots
    }

    public func inspectContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> ContainerSnapshot {
        let matches = try await listContainers(all: true, labels: [:], context: context)
        if let exact = matches.first(where: {
            $0.runtimeID.rawValue == id || $0.dockerID.rawValue == id || $0.spec.name == id
        }) {
            return exact
        }
        let prefixes = matches.filter {
            $0.runtimeID.rawValue.hasPrefix(id) || $0.dockerID.rawValue.hasPrefix(id)
        }
        guard prefixes.count == 1, let snapshot = prefixes.first else {
            throw DevContainerError(.notFound, message: "container \(id) was not found")
        }
        return snapshot
    }

    public func createContainer(
        spec: ContainerSpec,
        context: RuntimeRequestContext
    ) async throws -> ContainerSnapshot {
        var arguments = ["create", "--name", spec.name]
        for (key, value) in spec.environment.sorted(by: { $0.key < $1.key }) {
            arguments += ["--env", "\(key)=\(value)"]
        }
        for (key, value) in spec.labels.sorted(by: { $0.key < $1.key }) {
            arguments += ["--label", "\(key)=\(value)"]
        }
        if let workingDirectory = spec.workingDirectory, !workingDirectory.isEmpty {
            arguments += ["--workdir", workingDirectory]
        }
        if let user = spec.user, !user.isEmpty {
            arguments += ["--user", user]
        }
        if let hostname = spec.hostname, !hostname.isEmpty {
            arguments += ["--hostname", hostname]
        }
        if spec.terminal {
            arguments.append("--tty")
        }
        if spec.openStandardInput {
            arguments.append("--interactive")
        }
        if spec.privileged {
            arguments.append("--privileged")
        }
        if spec.initProcess {
            arguments.append("--init")
        }
        for capability in spec.capabilitiesToAdd {
            arguments += ["--cap-add", capability]
        }
        for capability in spec.capabilitiesToDrop {
            arguments += ["--cap-drop", capability]
        }
        for option in spec.securityOptions {
            arguments += ["--security-opt", option]
        }
        if let executable = spec.entrypoint.first {
            arguments += ["--entrypoint", executable]
        }
        for mount in spec.mounts {
            switch mount.type {
            case .bind:
                let value = "type=bind,source=\(mount.source),target=\(mount.destination)" +
                    (mount.readOnly ? ",readonly" : "")
                arguments += ["--mount", value]
            case .volume:
                if mount.anonymous == true {
                    // Docker image-declared volumes are private to this
                    // container unless explicitly named. Keeping the path on
                    // Apple's native writable root filesystem preserves that
                    // lifetime and, crucially, keeps storage such as
                    // BuildKit's overlay snapshotter on EXT4 rather than a
                    // VirtioFS host bind.
                    continue
                }
                if Self.requiresNativeVolume(name: mount.source) {
                    _ = try await createNativeVolumeIfNeeded(
                        spec: VolumeSpec(name: mount.source)
                    )
                    let value = "type=volume,source=\(mount.source),target=\(mount.destination)" +
                        (mount.readOnly ? ",readonly" : "")
                    arguments += ["--mount", value]
                    continue
                }
                let volume = try managedVolumes.create(
                    spec: VolumeSpec(name: mount.source)
                )
                let value = "type=bind,source=\(volume.mountpoint),target=\(mount.destination)" +
                    (mount.readOnly ? ",readonly" : "")
                arguments += ["--mount", value]
            case .tmpfs:
                arguments += ["--tmpfs", mount.destination]
            }
        }
        // Published sockets are projected after the VM starts with the same
        // SocketForwarder package used by Apple's container stack.
        for network in spec.networks {
            arguments += ["--network", network.name]
        }
        arguments.append(spec.image)
        arguments += Array(spec.entrypoint.dropFirst()) + spec.command

        let result = try await command(arguments)
        try requireSuccess(result, operation: "container create")
        requestedContainers[spec.name] = RequestedContainer(
            spec: spec,
            createdAt: nil
        )
        let snapshot = try await inspectContainer(id: spec.name, context: context)
        if let metadataStore {
            do {
                try await metadataStore.recordContainerMetadata(
                    RuntimeContainerMetadata(
                        runtimeID: snapshot.runtimeID,
                        dockerID: snapshot.dockerID,
                        spec: spec,
                        createdAt: snapshot.createdAt
                    )
                )
            } catch {
                try? await requireSuccess(
                    command(["delete", "--force", snapshot.runtimeID.rawValue]),
                    operation: "rollback container create"
                )
                throw error
            }
        }
        return snapshot
    }

    public func startContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws {
        let resolved = try await resolveContainerID(id, context: context)
        try await requireSuccess(
            command(["start", resolved]),
            operation: "container start"
        )
        let startedAt = Date()
        startedContainers.insert(id)
        startedContainers.insert(resolved)
        containerStartedAt[id] = startedAt
        containerStartedAt[resolved] = startedAt
        if let metadataStore,
           try await metadataStore.containerMetadata(id: resolved) != nil
        {
            try await metadataStore.markContainerStarted(id: resolved, at: startedAt)
        }
        let snapshot = try await inspectContainer(
            id: resolved,
            context: context
        )
        do {
            let resolvedPorts = try await portForwarding.start(
                containerID: resolved,
                bindings: snapshot.spec.ports,
                networkAddresses: snapshot.networkAddresses
            )
            if resolvedPorts != snapshot.spec.ports {
                var updatedSpec = snapshot.spec
                updatedSpec.ports = resolvedPorts
                let request = RequestedContainer(
                    spec: updatedSpec,
                    createdAt: snapshot.createdAt
                )
                requestedContainers[resolved] = request
                requestedContainers[updatedSpec.name] = request
                try await metadataStore?.recordContainerMetadata(
                    RuntimeContainerMetadata(
                        runtimeID: snapshot.runtimeID,
                        dockerID: snapshot.dockerID,
                        spec: updatedSpec,
                        createdAt: snapshot.createdAt,
                        startedAt: startedAt
                    )
                )
            }
        } catch {
            _ = try? await command(["stop", "--time", "0", resolved])
            throw error
        }
        try await synchronizeNetworkHosts(context: context)
    }

    public func stopContainer(
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

    public func killContainer(
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

    public func removeContainer(
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
        requestedContainers.removeValue(forKey: snapshot.spec.name)
        startedContainers.remove(id)
        startedContainers.remove(resolved)
        containerStartedAt.removeValue(forKey: id)
        containerStartedAt.removeValue(forKey: resolved)
        try await metadataStore?.removeContainerMetadata(id: resolved)
        try await synchronizeNetworkHosts(context: context)
    }

    public func waitContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> Int32 {
        while !Task.isCancelled {
            do {
                let snapshot = try await inspectContainer(id: id, context: context)
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

    public func containerLogs(
        id: String,
        follow: Bool,
        standardOutput _: Bool,
        standardError _: Bool,
        context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        let resolved = try await resolveContainerID(id, context: context)
        var arguments = ["logs"]
        if follow {
            arguments.append("--follow")
        }
        arguments.append(resolved)
        return try process(arguments).frames
    }

    public func attachContainer(
        id: String,
        terminal _: Bool,
        context: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession {
        ApplePollingLogSession {
            try await self.pollLogs(id: id, context: context)
        }
    }

    public func createExec(
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

    public func startExec(
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
        let session: any RuntimeProcessSession = if useDirectProcessAPI, exec.spec.terminal {
            try await AppleDirectProcessSession.create(
                containerID: exec.containerID.rawValue,
                spec: exec.spec
            )
        } else {
            try process(arguments)
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

    public func inspectExec(
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

    public func copyArchiveFromContainer(
        id: String,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> RuntimeArchive {
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        return try await withContainerRunningForArchiveTransfer(
            snapshot: snapshot,
            context: context
        ) {
            let temporary = try TemporaryDirectory(base: Self.transferDirectory)
            defer { temporary.remove() }
            let requestedName = URL(fileURLWithPath: path).lastPathComponent
            let archiveName = requestedName.isEmpty ? "root" : requestedName
            let copied = temporary.url.appendingPathComponent(archiveName)
            let copyResult = try await command([
                "cp",
                "\(resolved):\(path)",
                copied.path
            ])
            try requireSuccess(copyResult, operation: "container copy-out")
            let stat = try Self.archiveStat(
                url: copied,
                requestedName: requestedName.isEmpty ? "/" : requestedName
            )
            let tarResult = try await AppleCommandRunner.run(
                executable: URL(fileURLWithPath: "/usr/bin/tar"),
                arguments: ["-cf", "-", "-C", temporary.url.path, archiveName],
                environment: environment
            )
            try requireSuccess(tarResult, operation: "archive creation")
            return RuntimeArchive(data: tarResult.standardOutput, stat: stat)
        }
    }

    public func copyArchiveToContainer(
        id: String,
        path: String,
        archive: Data,
        context: RuntimeRequestContext
    ) async throws {
        guard archive.count <= 1_073_741_824 else {
            throw DevContainerError(.invalidRequest, message: "archive exceeds the 1 GiB request limit")
        }
        try TarArchiveValidator.validate(archive)
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        try await withContainerRunningForArchiveTransfer(
            snapshot: snapshot,
            context: context
        ) {
            let temporary = try TemporaryDirectory(base: Self.transferDirectory)
            defer { temporary.remove() }
            let extractResult = try await AppleCommandRunner.run(
                executable: URL(fileURLWithPath: "/usr/bin/tar"),
                arguments: ["-xf", "-", "-C", temporary.url.path],
                environment: environment,
                input: archive
            )
            try requireSuccess(extractResult, operation: "archive extraction")
            let staging = "/tmp/.devcontainer-copy-\(UUID().uuidString.lowercased())"
            do {
                let uploadResult = try await command([
                    "cp",
                    temporary.url.path,
                    "\(resolved):\(staging)"
                ])
                try requireSuccess(uploadResult, operation: "container archive upload")
                let copyResult = try await command([
                    "exec",
                    resolved,
                    "sh",
                    "-c",
                    "mkdir -p -- \"$1\" && cp -a -- \"$2\"/. \"$1\"/",
                    "devcontainer-copy",
                    path,
                    staging
                ])
                try requireSuccess(copyResult, operation: "container archive extraction")
            } catch {
                await removeTransferStaging(staging, containerID: resolved)
                throw error
            }
            await removeTransferStaging(staging, containerID: resolved)
        }
    }

    /// Apple currently exposes copy operations only while the container VM is
    /// running. Docker permits archive transfer for created and stopped
    /// containers, which BuildKit relies on while bootstrapping its driver.
    /// Start the VM only for the duration of the transfer and restore the
    /// externally visible stopped state afterwards.
    private func withContainerRunningForArchiveTransfer<T: Sendable>(
        snapshot: ContainerSnapshot,
        context: RuntimeRequestContext,
        operation: () async throws -> T
    ) async throws -> T {
        let resolved = snapshot.runtimeID.rawValue
        let needsTransientStart = snapshot.state != .running
        if needsTransientStart {
            try await requireSuccess(
                command(["start", resolved]),
                operation: "container archive transfer start"
            )
            try await waitForContainerState(
                id: resolved,
                expected: .running,
                context: context
            )
        }

        do {
            let value = try await operation()
            if needsTransientStart {
                try await stopTransientArchiveContainer(
                    id: resolved,
                    context: context
                )
            }
            return value
        } catch {
            if needsTransientStart {
                try? await stopTransientArchiveContainer(
                    id: resolved,
                    context: context
                )
            }
            throw error
        }
    }

    private func stopTransientArchiveContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws {
        try await requireSuccess(
            command(["stop", "--time", "10", id]),
            operation: "container archive transfer stop"
        )
        try await waitForContainerState(
            id: id,
            expected: .created,
            context: context,
            acceptsStopped: true
        )
    }

    private func waitForContainerState(
        id: String,
        expected: RuntimeContainerState,
        context: RuntimeRequestContext,
        acceptsStopped: Bool = false
    ) async throws {
        for _ in 0 ..< 100 {
            let current = try await inspectContainer(id: id, context: context).state
            if current == expected || (acceptsStopped && current == .stopped) {
                return
            }
            try await Task.sleep(for: .milliseconds(20))
        }
        throw DevContainerError(
            .deadlineExceeded,
            message: "container \(id) did not reach the required archive-transfer state"
        )
    }

    private func removeTransferStaging(
        _ staging: String,
        containerID: String
    ) async {
        _ = try? await command([
            "exec",
            containerID,
            "rm",
            "-rf",
            "--",
            staging
        ])
    }

    public func listImages(context _: RuntimeRequestContext) async throws -> [ImageSnapshot] {
        let result = try await command(["image", "list", "--format", "json"])
        try requireSuccess(result, operation: "image list")
        return try parseJSONObjectArray(result.standardOutput).compactMap(imageSnapshot)
    }

    public func inspectImage(
        reference: String,
        context: RuntimeRequestContext
    ) async throws -> ImageSnapshot {
        let images = try await listImages(context: context)
        guard let image = images.first(where: {
            $0.id == reference || $0.references.contains(where: {
                Self.equivalentImageReference($0, reference)
            })
        }) else {
            throw DevContainerError(.notFound, message: "image \(reference) was not found")
        }
        return image
    }

    public func pullImage(
        reference: String,
        context _: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error> {
        let session = try process([
            "image",
            "pull",
            "--progress",
            "plain",
            "--platform",
            Self.defaultPlatform,
            reference
        ])
        return Self.dataStream(session: session)
    }

    public func loadImage(
        archive: Data,
        context _: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error> {
        let temporary = try TemporaryDirectory()
        defer { temporary.remove() }
        let archiveURL = temporary.url.appendingPathComponent("image.tar")
        try archive.write(to: archiveURL, options: .atomic)
        let result = try await command([
            "image",
            "load",
            "--input",
            archiveURL.path
        ])
        try requireSuccess(result, operation: "image load")
        return AsyncThrowingStream { continuation in
            if !result.standardOutput.isEmpty {
                continuation.yield(result.standardOutput)
            }
            if !result.standardError.isEmpty {
                continuation.yield(result.standardError)
            }
            continuation.finish()
        }
    }

    public func buildImage(
        request: ImageBuildRequest,
        context _: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error> {
        try TarArchiveValidator.validate(request.context)
        let temporary = try TemporaryDirectory()
        let extractResult = try await AppleCommandRunner.run(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", temporary.url.path],
            environment: environment,
            input: request.context
        )
        try requireSuccess(extractResult, operation: "build context extraction")
        var arguments = ["build", "--file", request.dockerfile, "--progress", "plain"]
        for tag in request.tags {
            arguments += ["--tag", tag]
        }
        for (key, value) in request.buildArguments.sorted(by: { $0.key < $1.key }) {
            arguments += ["--build-arg", "\(key)=\(value)"]
        }
        if let target = request.target {
            arguments += ["--target", target]
        }
        for (key, value) in request.labels.sorted(by: { $0.key < $1.key }) {
            arguments += ["--label", "\(key)=\(value)"]
        }
        arguments.append(temporary.url.path)
        let result = try await command(arguments)
        temporary.remove()
        try requireSuccess(result, operation: "image build")
        return AsyncThrowingStream { continuation in
            continuation.yield(result.standardOutput)
            continuation.finish()
        }
    }

    public func tagImage(
        source: String,
        target: String,
        context _: RuntimeRequestContext
    ) async throws {
        try await requireSuccess(
            command(["image", "tag", source, target]),
            operation: "image tag"
        )
    }

    public func removeImage(
        reference: String,
        force: Bool,
        context _: RuntimeRequestContext
    ) async throws {
        var arguments = ["image", "delete"]
        if force {
            arguments.append("--force")
        }
        arguments.append(reference)
        try await requireSuccess(command(arguments), operation: "image delete")
    }

    public func listNetworks(context _: RuntimeRequestContext) async throws -> [NetworkSnapshot] {
        let result = try await command(["network", "list", "--format", "json"])
        try requireSuccess(result, operation: "network list")
        return try parseJSONObjectArray(result.standardOutput).compactMap(networkSnapshot)
    }

    public func inspectNetwork(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> NetworkSnapshot {
        let networks = try await listNetworks(context: context)
        guard let network = networks.first(where: { $0.id == id || $0.spec.name == id }) else {
            throw DevContainerError(.notFound, message: "network \(id) was not found")
        }
        return network
    }

    public func createNetwork(
        spec: NetworkSpec,
        context: RuntimeRequestContext
    ) async throws -> NetworkSnapshot {
        var arguments = ["network", "create"]
        for (key, value) in spec.labels.sorted(by: { $0.key < $1.key }) {
            arguments += ["--label", "\(key)=\(value)"]
        }
        if spec.internalNetwork {
            arguments.append("--internal")
        }
        arguments.append(spec.name)
        try await requireSuccess(command(arguments), operation: "network create")
        return try await inspectNetwork(id: spec.name, context: context)
    }

    public func connectNetwork(
        id: String,
        containerID: String,
        aliases: [String],
        context: RuntimeRequestContext
    ) async throws {
        _ = aliases
        _ = try await resolveContainerID(containerID, context: context)
        _ = try await inspectNetwork(id: id, context: context)
        throw DevContainerError(
            .unsupportedCapability,
            message: "stock Apple container requires networks and aliases at container creation"
        )
    }

    public func disconnectNetwork(
        id: String,
        containerID: String,
        force: Bool,
        context: RuntimeRequestContext
    ) async throws {
        _ = force
        _ = try await resolveContainerID(containerID, context: context)
        _ = try await inspectNetwork(id: id, context: context)
        throw DevContainerError(
            .unsupportedCapability,
            message: "stock Apple container cannot change network attachments after creation"
        )
    }

    public func removeNetwork(
        id: String,
        context _: RuntimeRequestContext
    ) async throws {
        try await requireSuccess(
            command(["network", "delete", id]),
            operation: "network delete"
        )
    }

    public func listVolumes(context _: RuntimeRequestContext) async throws -> [VolumeSnapshot] {
        let managed = try managedVolumes.list()
        let native = try await nativeBuildKitVolumes()
        return (managed + native).sorted { $0.name < $1.name }
    }

    public func inspectVolume(
        name: String,
        context: RuntimeRequestContext
    ) async throws -> VolumeSnapshot {
        _ = context
        if Self.requiresNativeVolume(name: name) {
            guard let volume = try await nativeBuildKitVolumes().first(where: {
                $0.name == name
            }) else {
                throw DevContainerError(.notFound, message: "volume \(name) was not found")
            }
            return volume
        }
        return try managedVolumes.inspect(name: name)
    }

    public func createVolume(
        spec: VolumeSpec,
        context: RuntimeRequestContext
    ) async throws -> VolumeSnapshot {
        _ = context
        if Self.requiresNativeVolume(name: spec.name) {
            return try await createNativeVolumeIfNeeded(spec: spec)
        }
        return try managedVolumes.create(spec: spec)
    }

    public func removeVolume(
        name: String,
        force _: Bool,
        context: RuntimeRequestContext
    ) async throws {
        let containers = try await listContainers(
            all: true,
            labels: [:],
            context: context
        )
        guard !containers.contains(where: { container in
            container.spec.mounts.contains {
                $0.type == .volume && $0.source == name
            }
        }) else {
            throw DevContainerError(
                .conflict,
                message: "volume \(name) is in use by a container"
            )
        }
        if Self.requiresNativeVolume(name: name) {
            try await requireSuccess(
                command(["volume", "delete", name]),
                operation: "volume delete"
            )
            return
        }
        try managedVolumes.remove(name: name)
    }

    /// BuildKit's state volume must be a Linux-native filesystem. A host
    /// directory projected through VirtioFS cannot back BuildKit's overlayfs
    /// snapshotter, so this narrowly identified Buildx-owned volume uses
    /// Apple's native EXT4 volume implementation. User-named Docker volumes
    /// continue through `ManagedVolumeStore` because Docker permits them to be
    /// attached concurrently to multiple containers.
    static func requiresNativeVolume(name: String) -> Bool {
        name.hasPrefix("buildx_buildkit_") && name.hasSuffix("_state")
    }

    private func createNativeVolumeIfNeeded(
        spec: VolumeSpec
    ) async throws -> VolumeSnapshot {
        guard spec.driver == "local" else {
            throw DevContainerError(
                .unsupportedCapability,
                message: "volume driver \(spec.driver) is not supported"
            )
        }
        if let existing = try await nativeBuildKitVolumes().first(where: {
            $0.name == spec.name
        }) {
            return existing
        }
        var arguments = ["volume", "create"]
        for (key, value) in spec.labels.sorted(by: { $0.key < $1.key }) {
            arguments += ["--label", "\(key)=\(value)"]
        }
        arguments.append(spec.name)
        try await requireSuccess(
            command(arguments),
            operation: "volume create"
        )
        guard let created = try await nativeBuildKitVolumes().first(where: {
            $0.name == spec.name
        }) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "created volume \(spec.name) was not returned by Apple container"
            )
        }
        return created
    }

    private func nativeBuildKitVolumes() async throws -> [VolumeSnapshot] {
        let result = try await command(["volume", "list", "--format", "json"])
        try requireSuccess(result, operation: "volume list")
        return try parseJSONObjectArray(result.standardOutput).compactMap { value in
            guard
                let configuration = value["configuration"] as? [String: Any],
                let name = configuration["name"] as? String,
                Self.requiresNativeVolume(name: name)
            else {
                return nil
            }
            let labels = configuration["labels"] as? [String: String] ?? [:]
            let driver = configuration["driver"] as? String ?? "local"
            let source = configuration["source"] as? String ?? ""
            let createdAt = Self.date(configuration["creationDate"]) ?? Date(timeIntervalSince1970: 0)
            return VolumeSnapshot(
                name: name,
                spec: VolumeSpec(name: name, labels: labels, driver: driver),
                mountpoint: source,
                createdAt: createdAt
            )
        }
    }

    public func events(
        since: Date?,
        until: Date?,
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<RuntimeEvent, any Error> {
        let initial = try await containerMap(labels: labels, context: context)
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var previous = initial
                    var sequence = Int64(Date().timeIntervalSince1970 * 1_000_000)
                    while !Task.isCancelled {
                        if let until, Date() >= until {
                            break
                        }
                        try await Task.sleep(for: .milliseconds(200))
                        let current = try await self.containerMap(
                            labels: labels,
                            context: context
                        )
                        let now = Date()
                        for (id, snapshot) in current where previous[id] == nil {
                            sequence += 1
                            if since.map({ now >= $0 }) ?? true {
                                continuation.yield(
                                    Self.event(
                                        sequence: sequence,
                                        timestamp: now,
                                        snapshot: snapshot,
                                        action: .create
                                    )
                                )
                                if snapshot.state == .running {
                                    sequence += 1
                                    continuation.yield(
                                        Self.event(
                                            sequence: sequence,
                                            timestamp: now,
                                            snapshot: snapshot,
                                            action: .start
                                        )
                                    )
                                }
                            }
                        }
                        for (id, snapshot) in current {
                            guard let old = previous[id], old.state != snapshot.state else {
                                continue
                            }
                            let action: RuntimeEventAction? = switch snapshot.state {
                            case .running:
                                .start
                            case .stopped:
                                .stop
                            default:
                                nil
                            }
                            if let action, since.map({ now >= $0 }) ?? true {
                                sequence += 1
                                continuation.yield(
                                    Self.event(
                                        sequence: sequence,
                                        timestamp: now,
                                        snapshot: snapshot,
                                        action: action
                                    )
                                )
                            }
                        }
                        for (id, snapshot) in previous where current[id] == nil {
                            if since.map({ now >= $0 }) ?? true {
                                sequence += 1
                                continuation.yield(
                                    Self.event(
                                        sequence: sequence,
                                        timestamp: now,
                                        snapshot: snapshot,
                                        action: .destroy
                                    )
                                )
                            }
                        }
                        previous = current
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    private func containerMap(
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> [String: ContainerSnapshot] {
        try await Dictionary(
            uniqueKeysWithValues: listContainers(
                all: true,
                labels: labels,
                context: context
            ).map { ($0.runtimeID.rawValue, $0) }
        )
    }

    private static func event(
        sequence: Int64,
        timestamp: Date,
        snapshot: ContainerSnapshot,
        action: RuntimeEventAction
    ) -> RuntimeEvent {
        var attributes = snapshot.spec.labels
        attributes["name"] = snapshot.spec.name
        attributes["image"] = snapshot.spec.image
        return RuntimeEvent(
            sequence: sequence,
            timestamp: timestamp,
            resourceID: snapshot.dockerID.rawValue,
            action: action,
            attributes: attributes
        )
    }

    private func synchronizeNetworkHosts(
        context: RuntimeRequestContext
    ) async throws {
        let running = try await listContainers(
            all: true,
            labels: [:],
            context: context
        ).filter {
            $0.state == .running
                && $0.spec.networks.contains {
                    Self.isUserDefinedNetwork($0.name)
                }
        }
        guard !running.isEmpty else {
            return
        }

        for target in running {
            let targetNetworks = Set(
                target.spec.networks
                    .map(\.name)
                    .filter(Self.isUserDefinedNetwork)
            )
            var lines = Set<String>()
            for source in running {
                for attachment in source.spec.networks
                    where targetNetworks.contains(attachment.name)
                {
                    guard
                        let address = Self.networkAddress(
                            source,
                            network: attachment.name
                        )
                    else {
                        continue
                    }
                    let names = Set(
                        [source.spec.name, source.spec.hostname]
                            .compactMap(\.self)
                            + attachment.aliases
                    ).filter(Self.isSafeHostName)
                    guard !names.isEmpty else {
                        continue
                    }
                    lines.insert("\(address) \(names.sorted().joined(separator: " "))")
                }
            }

            let hosts = """
            # BEGIN devcontainer managed network hosts
            \(lines.sorted().joined(separator: "\n"))
            # END devcontainer managed network hosts

            """
            let temporary = try TemporaryDirectory(base: Self.transferDirectory)
            defer { temporary.remove() }
            let localHosts = temporary.url.appendingPathComponent("hosts")
            let download = try await command([
                "cp",
                "\(target.runtimeID.rawValue):/etc/hosts",
                localHosts.path
            ])
            if download.exitCode != 0,
               await (try? inspectContainer(
                   id: target.runtimeID.rawValue,
                   context: context
               ).state) != .running
            {
                continue
            }
            try requireSuccess(download, operation: "container hosts download")
            let current = try String(contentsOf: localHosts, encoding: .utf8)
            let updated = Self.replacingManagedHosts(in: current, with: hosts)
            try Data(updated.utf8).write(to: localHosts, options: .atomic)
            let upload = try await command([
                "cp",
                localHosts.path,
                "\(target.runtimeID.rawValue):/etc/hosts"
            ])
            try requireSuccess(upload, operation: "container hosts upload")
        }
    }

    private static func isUserDefinedNetwork(_ name: String) -> Bool {
        !["bridge", "default", "host", "none"].contains(name)
    }

    private static var defaultVolumeRoot: URL {
        let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return applicationSupport
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("volumes", isDirectory: true)
    }

    static func replacingManagedHosts(
        in value: String,
        with managed: String
    ) -> String {
        let startMarker = "# BEGIN devcontainer managed network hosts"
        let endMarker = "# END devcontainer managed network hosts"
        var unmanaged = value
        if let start = unmanaged.range(of: startMarker),
           let end = unmanaged.range(
               of: endMarker,
               range: start.upperBound ..< unmanaged.endIndex
           )
        {
            unmanaged.removeSubrange(start.lowerBound ..< end.upperBound)
        }
        unmanaged = unmanaged.trimmingCharacters(in: .newlines)
        return unmanaged.isEmpty
            ? managed
            : "\(unmanaged)\n\(managed)"
    }

    private static func networkAddress(
        _ snapshot: ContainerSnapshot,
        network: String
    ) -> String? {
        let raw = snapshot.networkAddresses[network]
            ?? (
                snapshot.spec.networks.count == 1
                    && snapshot.networkAddresses.count == 1
                    ? snapshot.networkAddresses.values.first
                    : nil
            )
        guard let raw else {
            return nil
        }
        let address = String(raw.split(separator: "/", maxSplits: 1)[0])
        guard
            !address.isEmpty,
            address.utf8.allSatisfy({
                ($0 >= 48 && $0 <= 57)
                    || ($0 >= 65 && $0 <= 70)
                    || ($0 >= 97 && $0 <= 102)
                    || $0 == 46
                    || $0 == 58
            })
        else {
            return nil
        }
        return address
    }

    private static func isSafeHostName(_ value: String) -> Bool {
        !value.isEmpty
            && value.utf8.count <= 253
            && value.utf8.allSatisfy {
                ($0 >= 48 && $0 <= 57)
                    || ($0 >= 65 && $0 <= 90)
                    || ($0 >= 97 && $0 <= 122)
                    || $0 == 45
                    || $0 == 46
                    || $0 == 95
            }
    }

    static func sameContainerIncarnation(
        metadataCreatedAt: Date,
        observedCreatedAt: Date
    ) -> Bool {
        abs(metadataCreatedAt.timeIntervalSince(observedCreatedAt)) < 0.001
    }

    private func command(
        _ arguments: [String],
        input: Data? = nil
    ) async throws -> AppleCommandResult {
        try await AppleCommandRunner.run(
            executable: executable,
            arguments: arguments,
            environment: environment,
            input: input
        )
    }

    private func process(_ arguments: [String]) throws -> AppleProcessSession {
        try AppleProcessSession(
            executable: executable,
            arguments: arguments,
            environment: environment
        )
    }

    private func requireSuccess(
        _ result: AppleCommandResult,
        operation: String
    ) throws {
        guard result.exitCode == 0 else {
            let error = String(
                bytes: result.standardError.prefix(4096),
                encoding: .utf8
            )?.trimmingCharacters(in: .whitespacesAndNewlines)
                ?? "non-UTF-8 diagnostic output"
            throw DevContainerError(
                .runtimeUnavailable,
                message: "\(operation) failed with exit \(result.exitCode): \(error.isEmpty ? "no diagnostic output" : error)"
            )
        }
    }

    private func parseJSONObjectArray(_ data: Data) throws -> [[String: Any]] {
        guard let values = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw DevContainerError(.providerProtocolMismatch, message: "Apple container returned non-array JSON")
        }
        return values
    }

    private func containerSnapshot(_ value: [String: Any]) throws -> ContainerSnapshot {
        guard
            let id = value["id"] as? String,
            let configuration = value["configuration"] as? [String: Any]
        else {
            throw DevContainerError(.providerProtocolMismatch, message: "invalid Apple container record")
        }
        let status = value["status"] as? [String: Any]
        let stateText = status?["state"] as? String ?? "unknown"
        let image = (configuration["image"] as? [String: Any])?["reference"] as? String ?? ""
        let process = configuration["initProcess"] as? [String: Any] ?? [:]
        let executable = process["executable"] as? String ?? ""
        let arguments = process["arguments"] as? [String] ?? []
        let environment = Self.environmentDictionary(process["environment"] as? [String] ?? [])
        let labels = configuration["labels"] as? [String: String] ?? [:]
        let mounts = (configuration["mounts"] as? [[String: Any]] ?? []).compactMap(Self.mount)
        let ports = (configuration["publishedPorts"] as? [[String: Any]] ?? []).compactMap(Self.port)
        let rawNetworks = configuration["networks"] as? [[String: Any]] ?? []
        let networks = rawNetworks.compactMap { network -> NetworkAttachment? in
            guard let name = network["network"] as? String else {
                return nil
            }
            let options = network["options"] as? [String: Any]
            return NetworkAttachment(
                name: name,
                aliases: options?["aliases"] as? [String] ?? []
            )
        }
        let creationDate = Self.date(configuration["creationDate"]) ?? Date(timeIntervalSince1970: 0)
        let dockerID = labels[Self.dockerIDLabel] ?? id
        let wasStarted = startedContainers.contains(id) || startedContainers.contains(dockerID)
        let requestKey = requestedContainers[id] != nil ? id : dockerID
        var request = requestedContainers[requestKey]
        if let requestedCreatedAt = request?.createdAt {
            if !Self.sameContainerIncarnation(
                metadataCreatedAt: requestedCreatedAt,
                observedCreatedAt: creationDate
            ) {
                requestedContainers.removeValue(forKey: id)
                requestedContainers.removeValue(forKey: dockerID)
                startedContainers.remove(id)
                startedContainers.remove(dockerID)
                containerStartedAt.removeValue(forKey: id)
                containerStartedAt.removeValue(forKey: dockerID)
                request = nil
            }
        } else if request != nil {
            request?.createdAt = creationDate
            requestedContainers[requestKey] = request
        }
        let createdByThisEngine = request != nil
        let state: RuntimeContainerState = switch stateText {
        case "created":
            .created
        case "running":
            .running
        case "stopped":
            createdByThisEngine && !wasStarted ? .created : .stopped
        default:
            .unknown
        }
        let command = executable.isEmpty ? arguments : [executable] + arguments
        let startedAt = containerStartedAt[id]
            ?? containerStartedAt[dockerID]
            ?? (state == .running ? creationDate : nil)
        var snapshot = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: id),
            dockerID: DockerID(rawValue: dockerID),
            spec: ContainerSpec(
                name: id,
                image: image,
                command: command,
                environment: environment,
                labels: labels,
                workingDirectory: process["workingDirectory"] as? String,
                user: Self.user(process["user"]),
                hostname: configuration["hostname"] as? String,
                mounts: mounts,
                ports: ports,
                networks: networks,
                terminal: process["terminal"] as? Bool ?? false,
                openStandardInput: false,
                privileged: process["privileged"] as? Bool ?? false,
                initProcess: configuration["useInit"] as? Bool ?? false,
                capabilitiesToAdd: configuration["capAdd"] as? [String] ?? [],
                capabilitiesToDrop: configuration["capDrop"] as? [String] ?? [],
                securityOptions: Self.securityOptions(configuration)
            ),
            state: state,
            createdAt: creationDate,
            startedAt: Self.date(status?["startedDate"]) ?? startedAt,
            finishedAt: Self.date(value["exitedDate"]),
            exitCode: Self.number(value["exitCode"] ?? status?["exitCode"])
                .flatMap(Int32.init(exactly:)),
            networkAddresses: Self.networkAddresses(status)
        )
        if var requested = request?.spec {
            requested.labels.merge(snapshot.spec.labels) { _, observedValue in observedValue }
            snapshot.spec = requested
        }
        return snapshot
    }

    private func apply(
        metadata: RuntimeContainerMetadata,
        to observed: ContainerSnapshot
    ) -> ContainerSnapshot {
        var snapshot = observed
        var spec = metadata.spec
        spec.labels.merge(observed.spec.labels) { _, observedValue in observedValue }
        snapshot.spec = spec
        snapshot.dockerID = metadata.dockerID
        snapshot.createdAt = metadata.createdAt
        snapshot.startedAt = metadata.startedAt ?? observed.startedAt
        if observed.state == .stopped, metadata.startedAt == nil {
            snapshot.state = .created
            snapshot.exitCode = nil
        }
        return snapshot
    }

    private func imageSnapshot(_ value: [String: Any]) -> ImageSnapshot? {
        guard
            let id = value["id"] as? String,
            let configuration = value["configuration"] as? [String: Any]
        else {
            return nil
        }
        let name = configuration["name"] as? String
        let variants = value["variants"] as? [[String: Any]] ?? []
        let arm = variants.first(where: {
            (($0["platform"] as? [String: Any])?["architecture"] as? String) == "arm64"
        }) ?? variants.first
        let platform = arm?["platform"] as? [String: Any]
        let imageConfiguration = (arm?["config"] as? [String: Any])?["config"] as? [String: Any] ?? [:]
        return ImageSnapshot(
            id: "sha256:\(id)",
            references: name.map { [$0] } ?? [],
            createdAt: Self.date(configuration["creationDate"]) ?? Date(timeIntervalSince1970: 0),
            size: Self.number(arm?["size"]).flatMap(UInt64.init(exactly:)) ?? 0,
            architecture: platform?["architecture"] as? String ?? "arm64",
            operatingSystem: platform?["os"] as? String ?? "linux",
            user: imageConfiguration["User"] as? String ?? "",
            environment: imageConfiguration["Env"] as? [String] ?? [],
            entrypoint: imageConfiguration["Entrypoint"] as? [String] ?? [],
            command: imageConfiguration["Cmd"] as? [String] ?? [],
            labels: imageConfiguration["Labels"] as? [String: String] ?? [:]
        )
    }

    private func networkSnapshot(_ value: [String: Any]) -> NetworkSnapshot? {
        guard
            let id = value["id"] as? String,
            let configuration = value["configuration"] as? [String: Any],
            let name = configuration["name"] as? String
        else {
            return nil
        }
        return NetworkSnapshot(
            id: id,
            spec: NetworkSpec(
                name: name,
                labels: configuration["labels"] as? [String: String] ?? [:],
                driver: configuration["plugin"] as? String ?? "bridge",
                internalNetwork: configuration["mode"] as? String == "isolated"
            ),
            createdAt: Self.date(configuration["creationDate"]) ?? Date(timeIntervalSince1970: 0)
        )
    }

    private static func mount(_ value: [String: Any]) -> RuntimeMount? {
        guard let destination = value["destination"] as? String else {
            return nil
        }
        let source = value["source"] as? String ?? ""
        let typeObject = value["type"] as? [String: Any] ?? [:]
        let type: RuntimeMountType
        if typeObject["virtiofs"] != nil {
            type = .bind
        } else if typeObject["volume"] != nil {
            type = .volume
        } else if typeObject["tmpfs"] != nil {
            type = .tmpfs
        } else {
            return nil
        }
        let options = value["options"] as? [String] ?? []
        return RuntimeMount(
            type: type,
            source: source,
            destination: destination,
            readOnly: options.contains("ro")
        )
    }

    private static func port(_ value: [String: Any]) -> PortBinding? {
        guard
            let containerPort = number(value["containerPort"]).flatMap({ UInt16(exactly: $0) })
        else {
            return nil
        }
        return PortBinding(
            containerPort: containerPort,
            hostPort: number(value["hostPort"]).flatMap { UInt16(exactly: $0) },
            protocolName: value["protocol"] as? String ?? "tcp",
            hostAddress: value["hostAddress"] as? String ?? "127.0.0.1"
        )
    }

    private static func networkAddresses(_ status: [String: Any]?) -> [String: String] {
        let networks = status?["networks"] as? [[String: Any]] ?? []
        return Dictionary(uniqueKeysWithValues: networks.compactMap { network in
            guard
                let name = network["network"] as? String,
                let address = (
                    network["ipv4Address"] as? String
                        ?? network["address"] as? String
                )
            else {
                return nil
            }
            return (name, address)
        })
    }

    private static func number(_ value: Any?) -> Int? {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        if let value = value as? String {
            return Int(value)
        }
        return nil
    }

    private static func user(_ value: Any?) -> String? {
        if let value = value as? String {
            return value
        }
        guard let value = value as? [String: Any] else {
            return nil
        }
        if let raw = value["raw"] as? [String: Any],
           let userString = raw["userString"] as? String
        {
            return userString
        }
        if let id = value["id"] as? [String: Any],
           let uid = number(id["uid"]),
           let gid = number(id["gid"])
        {
            return "\(uid):\(gid)"
        }
        return nil
    }

    private static func equivalentImageReference(_ lhs: String, _ rhs: String) -> Bool {
        normalizedImageReference(lhs) == normalizedImageReference(rhs)
    }

    private static func normalizedImageReference(_ reference: String) -> String {
        var value = reference.lowercased()
        for prefix in ["docker.io/", "index.docker.io/"] where value.hasPrefix(prefix) {
            value.removeFirst(prefix.count)
        }
        if value.hasPrefix("library/") {
            value.removeFirst("library/".count)
        }
        if !value.contains(":"), !value.contains("@") {
            value += ":latest"
        }
        return value
    }

    private static func date(_ value: Any?) -> Date? {
        guard let value = value as? String else {
            return nil
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }

    private static func archiveStat(
        url: URL,
        requestedName: String
    ) throws -> ArchivePathStat {
        var status = Darwin.stat()
        guard lstat(url.path, &status) == 0 else {
            throw DevContainerError(
                .notFound,
                message: "copied archive path is unavailable: \(url.lastPathComponent)"
            )
        }
        let linkTarget: String = if status.st_mode & S_IFMT == S_IFLNK {
            try FileManager.default.destinationOfSymbolicLink(
                atPath: url.path
            )
        } else {
            ""
        }
        return ArchivePathStat(
            name: requestedName,
            size: Int64(status.st_size),
            mode: dockerFileMode(status.st_mode),
            modificationTime: Date(
                timeIntervalSince1970: TimeInterval(status.st_mtimespec.tv_sec)
                    + TimeInterval(status.st_mtimespec.tv_nsec) / 1_000_000_000
            ),
            linkTarget: linkTarget
        )
    }

    private static func dockerFileMode(_ mode: mode_t) -> UInt32 {
        var result = UInt32(mode & (S_IRWXU | S_IRWXG | S_IRWXO))
        switch mode & S_IFMT {
        case S_IFDIR:
            result |= 1 << 31
        case S_IFLNK:
            result |= 1 << 27
        case S_IFBLK:
            result |= 1 << 26
        case S_IFIFO:
            result |= 1 << 25
        case S_IFSOCK:
            result |= 1 << 24
        case S_IFCHR:
            result |= (1 << 26) | (1 << 21)
        case S_IFREG:
            break
        default:
            result |= 1 << 19
        }
        if mode & S_ISUID != 0 {
            result |= 1 << 23
        }
        if mode & S_ISGID != 0 {
            result |= 1 << 22
        }
        if mode & S_ISVTX != 0 {
            result |= 1 << 20
        }
        return result
    }

    private static var transferDirectory: URL {
        let cache = FileManager.default.urls(
            for: .cachesDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return cache.appendingPathComponent("devcontainer/transfers", isDirectory: true)
    }

    private static var defaultPlatform: String {
        #if arch(arm64)
            "linux/arm64"
        #else
            "linux/amd64"
        #endif
    }

    private static func environmentDictionary(_ values: [String]) -> [String: String] {
        Dictionary(uniqueKeysWithValues: values.map { value in
            let parts = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            return (String(parts[0]), parts.count == 2 ? String(parts[1]) : "")
        })
    }

    private static func securityOptions(_ configuration: [String: Any]) -> [String] {
        var options: [String] = []
        if let process = configuration["initProcess"] as? [String: Any],
           process["noNewPrivileges"] as? Bool == true
        {
            options.append("no-new-privileges=true")
        }
        if configuration["unconfinedSystemPaths"] as? Bool == true {
            options.append("systempaths=unconfined")
        }
        return options
    }

    private static func filteredEnvironment(_ values: [String: String]) -> [String: String] {
        let allowed = [
            "CONTAINER_APP_ROOT",
            "CONTAINER_HOST",
            "CONTAINER_INSTALL_ROOT",
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "TMPDIR",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME"
        ]
        return Dictionary(uniqueKeysWithValues: allowed.compactMap { key in
            values[key].map { (key, $0) }
        })
    }

    private static func dataStream(
        session: AppleProcessSession
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await frame in session.frames {
                        continuation.yield(frame.data)
                    }
                    let exitCode = try await session.wait()
                    if exitCode == 0 {
                        continuation.finish()
                    } else {
                        continuation.finish(
                            throwing: DevContainerError(
                                .runtimeUnavailable,
                                message: "Apple container command exited with \(exitCode)"
                            )
                        )
                    }
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
                Task {
                    session.cancel()
                }
            }
        }
    }

    private func pollLogs(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> AppleLogPoll {
        do {
            let snapshot = try await inspectContainer(id: id, context: context)
            guard
                snapshot.state == .running
                || (
                    snapshot.state == .stopped
                        && wasStarted(id: id, snapshot: snapshot)
                )
            else {
                return AppleLogPoll(
                    standardOutput: Data(),
                    standardError: Data(),
                    finished: false,
                    exitCode: 0
                )
            }
            let result = try await command([
                "logs",
                snapshot.runtimeID.rawValue
            ])
            if result.exitCode != 0, snapshot.state == .running {
                return AppleLogPoll(
                    standardOutput: Data(),
                    standardError: Data(),
                    finished: false,
                    exitCode: 0
                )
            }
            try requireSuccess(result, operation: "container attach logs")
            return AppleLogPoll(
                standardOutput: result.standardOutput,
                standardError: result.standardError,
                finished: snapshot.state == .stopped,
                exitCode: snapshot.exitCode ?? 0
            )
        } catch let error as DevContainerError where error.code == .notFound {
            return AppleLogPoll(
                standardOutput: Data(),
                standardError: Data(),
                finished: true,
                exitCode: 0
            )
        }
    }

    private func scheduleAutomaticRemoval(id: String) {
        Task {
            try? await Task.sleep(for: .seconds(1))
            try? await self.removeContainer(
                id: id,
                force: true,
                context: RuntimeRequestContext()
            )
        }
    }

    private func wasStarted(id: String, snapshot: ContainerSnapshot) -> Bool {
        snapshot.startedAt != nil
            || startedContainers.contains(id)
            || startedContainers.contains(snapshot.runtimeID.rawValue)
            || startedContainers.contains(snapshot.spec.name)
    }

    private func resolveContainerID(
        _ id: String,
        context: RuntimeRequestContext
    ) async throws -> String {
        try await inspectContainer(id: id, context: context).runtimeID.rawValue
    }
}

private struct AppleVersionRecord: Decodable {
    let appName: String
    let version: String
    let commit: String?
    let distribution: String?
}

private final class TemporaryDirectory {
    let url: URL

    init(base: URL = FileManager.default.temporaryDirectory) throws {
        try FileManager.default.createDirectory(
            at: base,
            withIntermediateDirectories: true,
            attributes: nil
        )
        let root = base
            .appendingPathComponent("devcontainer-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        guard chmod(root.path, S_IRWXU) == 0 else {
            try? FileManager.default.removeItem(at: root)
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        var status = Darwin.stat()
        guard
            lstat(root.path, &status) == 0,
            status.st_uid == getuid(),
            status.st_mode & S_IFMT == S_IFDIR,
            status.st_mode & (S_IRWXG | S_IRWXO) == 0
        else {
            try? FileManager.default.removeItem(at: root)
            throw DevContainerError(
                .invalidRequest,
                message: "temporary directory must be private and owned by the current user"
            )
        }
        url = root
    }

    func remove() {
        try? FileManager.default.removeItem(at: url)
    }

    deinit {
        remove()
    }
}
