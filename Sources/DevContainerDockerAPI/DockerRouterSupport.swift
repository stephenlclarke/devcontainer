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
import DevContainerRuntimeSPI
import Foundation

extension DockerRouter {
    func container(
        _ snapshot: ContainerSnapshot,
        matches expectedLabels: [String: String]
    ) throws -> Bool {
        let actualLabels = try RuntimeLabels.projectComposeLabels(snapshot.spec.labels)
        return expectedLabels.allSatisfy { key, expected in
            guard let actual = actualLabels[key] else {
                return false
            }
            return expected.isEmpty || actual == expected
        }
    }

    func stripAPIVersion(_ path: String) -> String {
        let components = path.split(separator: "/", omittingEmptySubsequences: true)
        guard let first = components.first, first.hasPrefix("v"), first.dropFirst().contains(".") else {
            return path
        }
        return "/" + components.dropFirst().joined(separator: "/")
    }

    func parseLabelFilters(_ value: String?) throws -> [String: String] {
        try labelFilters(parseFilters(value)["label"] ?? [])
    }

    func parseFilters(_ value: String?) throws -> [String: [String]] {
        guard let value, !value.isEmpty else {
            return [:]
        }
        let object = try JSONSerialization.jsonObject(with: Data(value.utf8))
        guard let filters = object as? [String: Any] else {
            throw DevContainerError(.invalidRequest, message: "filters must be a JSON object")
        }
        return try filters.mapValues { rawValue in
            if let values = rawValue as? [String] {
                return values
            }
            if let values = rawValue as? [String: Any] {
                return values.compactMap { key, enabled in
                    if let enabled = enabled as? Bool {
                        return enabled ? key : nil
                    }
                    if let enabled = enabled as? NSNumber {
                        return enabled.boolValue ? key : nil
                    }
                    return nil
                }.sorted()
            }
            throw DevContainerError(.invalidRequest, message: "filter values must be arrays or maps")
        }
    }

    func labelFilters(_ labels: [String]) throws -> [String: String] {
        var result: [String: String] = [:]
        for label in labels {
            let parts = label.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            let key = String(parts[0])
            let value = parts.count == 2 ? String(parts[1]) : ""
            if let existing = result[key], existing != value {
                throw DevContainerError(.invalidRequest, message: "conflicting label filter \(key)")
            }
            result[key] = value
        }
        return result
    }

    func stringDictionary(_ value: String?, name: String) throws -> [String: String] {
        guard let value, !value.isEmpty else {
            return [:]
        }
        guard
            let object = try JSONSerialization.jsonObject(with: Data(value.utf8))
            as? [String: String]
        else {
            throw DevContainerError(.invalidRequest, message: "\(name) must be a string dictionary")
        }
        return object
    }

    func dateQuery(_ value: String?, name: String) throws -> Date? {
        guard let value, !value.isEmpty else {
            return nil
        }
        if let seconds = Double(value) {
            return Date(timeIntervalSince1970: seconds)
        }
        if let date = ISO8601DateFormatter().date(from: value) {
            return date
        }
        throw DevContainerError(.invalidRequest, message: "\(name) is not a valid timestamp")
    }

    func identifier(in path: String, prefix: String, suffix: String?) -> String? {
        guard path.hasPrefix(prefix) else {
            return nil
        }
        var value = String(path.dropFirst(prefix.count))
        if let suffix {
            guard value.hasSuffix(suffix) else {
                return nil
            }
            value.removeLast(suffix.count)
        } else if value.contains("/") {
            return nil
        }
        guard !value.isEmpty else {
            return nil
        }
        return value.removingPercentEncoding ?? value
    }

    func unsigned16(_ value: String?, name: String) throws -> UInt16 {
        guard let value, let result = UInt16(value) else {
            throw DevContainerError(.invalidRequest, message: "\(name) must be a 16-bit unsigned integer")
        }
        return result
    }

    func validateSecurityOptions(_ options: [String]) throws -> [String] {
        var result: [String] = []
        for option in options {
            let normalized = option.lowercased().replacingOccurrences(of: ":", with: "=")
            if normalized == "seccomp=unconfined" {
                // Apple containers do not install Docker's default seccomp
                // profile, so this Docker request is already the native state.
                continue
            }
            if normalized == "no-new-privileges=true" || normalized == "no-new-privileges=false"
                || normalized == "systempaths=unconfined"
            {
                result.append(option)
                continue
            }
            throw DevContainerError(
                .unsupportedCapability,
                message: "security option \(option) cannot be represented by Apple container"
            )
        }
        return result
    }

    func archiveStatHeader(_ value: ArchivePathStat) throws -> String {
        let stat = DockerArchivePathStat(
            name: value.name,
            size: value.size,
            mode: Int64(value.mode),
            modificationTime: ISO8601DateFormatter().string(
                from: value.modificationTime
            ),
            linkTarget: value.linkTarget
        )
        return try DockerJSON.encoder.encode(stat).base64EncodedString()
    }

    func waitForContainerRemoval(
        id: String,
        context: RuntimeRequestContext
    ) async throws {
        while !Task.isCancelled {
            do {
                _ = try await runtime.inspectContainer(id: id, context: context)
            } catch let error as DevContainerError where error.code == .notFound {
                return
            }
            try await Task.sleep(for: .milliseconds(100))
        }
        throw DevContainerError(.cancelled, message: "container removal wait was cancelled")
    }

    func containerWaitStream(
        id: String,
        condition: String?,
        context: RuntimeRequestContext
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let exitCode = try await runtime.waitContainer(id: id, context: context)
                    if condition == "removed" {
                        try await waitForContainerRemoval(id: id, context: context)
                    }
                    try continuation.yield(
                        DockerJSON.encoder.encode(
                            DockerWaitResponse(statusCode: exitCode)
                        )
                    )
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func dockerProgressStream(
        _ stream: AsyncThrowingStream<Data, any Error>,
        status: String?
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await data in stream {
                        let text = String(data: data, encoding: .utf8)
                            ?? "non-UTF-8 progress output"
                        let object: [String: String] = if let status {
                            ["status": status, "progress": text]
                        } else {
                            ["stream": text]
                        }
                        var encoded = try DockerJSON.encoder.encode(object)
                        encoded.append(0x0A)
                        continuation.yield(encoded)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func eventStream(
        _ stream: AsyncThrowingStream<RuntimeEvent, any Error>,
        acceptedActions: Set<String>
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await event in stream
                        where acceptedActions.isEmpty || acceptedActions.contains(event.action.rawValue)
                    {
                        let nanoseconds = Int64(event.timestamp.timeIntervalSince1970 * 1_000_000_000)
                        let message = DockerEventMessage(
                            status: event.action.rawValue,
                            id: event.resourceID,
                            type: event.resourceType,
                            action: event.action.rawValue,
                            actor: DockerEventActor(id: event.resourceID, attributes: event.attributes),
                            time: Int64(event.timestamp.timeIntervalSince1970),
                            timeNano: nanoseconds
                        )
                        var data = try DockerJSON.encoder.encode(message)
                        data.append(0x0A)
                        continuation.yield(data)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func containerSpec(
        from request: DockerCreateContainerRequest,
        requestedName: String
    ) throws -> ContainerSpec {
        try ContainerSpec(
            name: requestedName.isEmpty ? "devcontainer-\(UUID().uuidString.prefix(12).lowercased())" : requestedName,
            image: request.image,
            command: request.cmd ?? [],
            entrypoint: request.entrypoint?.values ?? [],
            environment: environmentDictionary(request.env ?? []),
            labels: request.labels ?? [:],
            workingDirectory: request.workingDir,
            user: request.user,
            hostname: request.hostname,
            mounts: containerMounts(request),
            ports: portBindings(request.hostConfig?.portBindings ?? [:]),
            networks: networkAttachments(request),
            terminal: request.tty ?? false,
            openStandardInput: request.openStdin ?? false,

            privileged: request.hostConfig?.privileged ?? false,
            initProcess: request.hostConfig?.initProcess ?? false,
            autoRemove: request.hostConfig?.autoRemove ?? false,
            capabilitiesToAdd: request.hostConfig?.capabilitiesToAdd ?? [],
            capabilitiesToDrop: request.hostConfig?.capabilitiesToDrop ?? [],
            securityOptions: validateSecurityOptions(request.hostConfig?.securityOptions ?? []),
            healthcheck: request.healthcheck.map {
                ContainerHealthcheck(
                    test: $0.test ?? [],
                    intervalNanoseconds: $0.interval ?? 30_000_000_000,
                    timeoutNanoseconds: $0.timeout ?? 30_000_000_000,
                    retries: $0.retries ?? 3,
                    startPeriodNanoseconds: $0.startPeriod ?? 0
                )
            }
        )
    }

    func containerMounts(
        _ request: DockerCreateContainerRequest
    ) throws -> [RuntimeMount] {
        var mounts = try bindMounts(request.hostConfig?.binds ?? [])
        mounts += try structuredMounts(
            (request.hostConfig?.mounts ?? []) + (request.mounts ?? [])
        )
        let destinations = Set(mounts.map(\.destination))
        for destination in (request.volumes ?? [:]).keys.sorted()
            where !destinations.contains(destination)
        {
            mounts.append(
                RuntimeMount(
                    type: .volume,
                    source: Self.anonymousVolumeName(),
                    destination: destination,
                    anonymous: true
                )
            )
        }
        return mounts
    }

    func bindMounts(_ values: [String]) throws -> [RuntimeMount] {
        try values.map { bind in
            let parts = bind.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
            guard parts.count >= 2 else {
                throw DevContainerError(.invalidRequest, message: "invalid bind mount \(bind)")
            }
            return RuntimeMount(
                type: Self.bindSourceIsHostPath(String(parts[0])) ? .bind : .volume,
                source: String(parts[0]),
                destination: String(parts[1]),
                readOnly: parts.count == 3 && parts[2].split(separator: ",").contains("ro")
            )
        }
    }

    func structuredMounts(
        _ values: [DockerMountRequest]
    ) throws -> [RuntimeMount] {
        try values.map { mount in
            guard let type = RuntimeMountType(rawValue: mount.type) else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "unsupported mount type \(mount.type)"
                )
            }
            let anonymous = type == .volume && (mount.source?.isEmpty ?? true)
            return RuntimeMount(
                type: type,
                source: anonymous ? Self.anonymousVolumeName() : mount.source ?? "",
                destination: mount.target,
                readOnly: mount.readOnly ?? false,
                anonymous: anonymous
            )
        }
    }

    func portBindings(
        _ values: [String: [DockerPortBindingRequest]]
    ) throws -> [PortBinding] {
        var result: [PortBinding] = []
        for (containerKey, hostBindings) in values {
            let keyParts = containerKey.split(separator: "/", maxSplits: 1)
            guard let containerPort = UInt16(keyParts[0]) else {
                throw DevContainerError(.invalidRequest, message: "invalid port \(containerKey)")
            }
            let protocolName = keyParts.count == 2 ? String(keyParts[1]) : "tcp"
            if hostBindings.isEmpty {
                result.append(
                    PortBinding(
                        containerPort: containerPort,
                        protocolName: protocolName
                    )
                )
                continue
            }
            for binding in hostBindings {
                let hostAddress: String = if let requested = binding.hostIP, !requested.isEmpty {
                    requested
                } else {
                    "127.0.0.1"
                }
                result.append(
                    PortBinding(
                        containerPort: containerPort,
                        hostPort: binding.hostPort.flatMap(UInt16.init),
                        protocolName: protocolName,
                        hostAddress: hostAddress
                    )
                )
            }
        }
        return result
    }

    func networkAttachments(
        _ request: DockerCreateContainerRequest
    ) -> [NetworkAttachment] {
        var networks = (request.networkingConfig?.endpointsConfig ?? [:]).map {
            NetworkAttachment(
                name: $0.key,
                aliases: ($0.value.aliases ?? []).sorted()
            )
        }
        if networks.isEmpty,
           let mode = request.hostConfig?.networkMode,
           !mode.isEmpty,
           !["bridge", "default"].contains(mode)
        {
            networks.append(NetworkAttachment(name: mode))
        }
        return networks.sorted { $0.name < $1.name }
    }

    func environmentDictionary(_ values: [String]) -> [String: String] {
        var result: [String: String] = [:]
        for value in values {
            let parts = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            result[String(parts[0])] = parts.count == 2 ? String(parts[1]) : ""
        }
        return result
    }

    func containerSummary(_ snapshot: ContainerSnapshot) throws -> DockerContainerSummary {
        let labels = try RuntimeLabels.projectComposeLabels(snapshot.spec.labels)
        return DockerContainerSummary(
            id: snapshot.dockerID.rawValue,
            names: ["/\(snapshot.spec.name)"],
            image: snapshot.spec.image,
            imageID: snapshot.spec.image,
            command: (snapshot.spec.entrypoint + snapshot.spec.command).joined(separator: " "),
            created: Int64(snapshot.createdAt.timeIntervalSince1970),
            state: snapshot.state.rawValue,
            status: snapshot.state.rawValue,
            ports: snapshot.spec.ports.map {
                DockerPortSummary(
                    address: $0.hostAddress,
                    privatePort: $0.containerPort,
                    publicPort: $0.hostPort,
                    type: $0.protocolName
                )
            },
            labels: labels,
            mounts: snapshot.spec.mounts.map(mountSummary)
        )
    }

    func containerInspect(
        _ snapshot: ContainerSnapshot,
        health: DockerContainerHealth?
    ) throws -> DockerContainerInspect {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let running = snapshot.state == .running
        let env = environmentList(snapshot.spec.environment)
        let volumeEntries = volumeEntries(snapshot.spec.mounts)
        let networkSettings = networkSettings(snapshot)
        let (executable, args) = containerCommand(snapshot.spec)
        return try DockerContainerInspect(
            id: snapshot.dockerID.rawValue,
            created: formatter.string(from: snapshot.createdAt),
            path: executable,
            args: args,
            name: "/\(snapshot.spec.name)",
            state: DockerContainerState(
                status: snapshot.state.rawValue,
                running: running,
                pid: running ? 1 : 0,
                exitCode: snapshot.exitCode ?? 0,
                startedAt: snapshot.startedAt.map(formatter.string) ?? "",
                finishedAt: snapshot.finishedAt.map(formatter.string) ?? "",
                health: health
            ),
            image: snapshot.spec.image,
            config: DockerContainerConfig(
                hostname: snapshot.spec.hostname ?? snapshot.spec.name,
                user: snapshot.spec.user ?? "",
                attachStdin: snapshot.spec.openStandardInput,
                attachStdout: true,
                attachStderr: true,
                tty: snapshot.spec.terminal,
                openStdin: snapshot.spec.openStandardInput,
                env: env,
                cmd: snapshot.spec.command,
                image: snapshot.spec.image,
                volumes: volumeEntries,
                workingDir: snapshot.spec.workingDirectory ?? "",
                entrypoint: snapshot.spec.entrypoint,
                labels: RuntimeLabels.projectComposeLabels(snapshot.spec.labels),
                healthcheck: dockerHealthcheck(snapshot.spec.healthcheck)
            ),
            hostConfig: DockerInspectHostConfig(
                binds: snapshot.spec.mounts.filter { $0.type == .bind }.map {
                    "\($0.source):\($0.destination)\($0.readOnly ? ":ro" : "")"
                }
            ),
            mounts: snapshot.spec.mounts.map(mountSummary),
            networkSettings: networkSettings
        )
    }

    func dockerHealthcheck(
        _ value: ContainerHealthcheck?
    ) -> DockerHealthcheck? {
        value.map {
            DockerHealthcheck(
                test: $0.test,
                interval: $0.intervalNanoseconds,
                timeout: $0.timeoutNanoseconds,
                retries: $0.retries,
                startPeriod: $0.startPeriodNanoseconds
            )
        }
    }

    func volumeEntries(
        _ mounts: [RuntimeMount]
    ) -> [String: [String: String]] {
        mounts.reduce(into: [:]) { $0[$1.destination] = [:] }
    }

    func networkSettings(
        _ snapshot: ContainerSnapshot
    ) -> DockerNetworkSettings {
        let ports: [String: [DockerNetworkPortBinding]?] = Dictionary(
            grouping: snapshot.spec.ports,
            by: { "\($0.containerPort)/\($0.protocolName)" }
        ).mapValues { values -> [DockerNetworkPortBinding]? in
            values.compactMap { binding -> DockerNetworkPortBinding? in
                guard let hostPort = binding.hostPort else {
                    return nil
                }
                return DockerNetworkPortBinding(
                    hostIP: binding.hostAddress,
                    hostPort: String(hostPort)
                )
            }
        }
        let networks = Dictionary(
            uniqueKeysWithValues: snapshot.networkAddresses.map { name, value in
                let address = Self.networkAddress(value)
                let attachment = snapshot.spec.networks.first {
                    $0.name == name
                }
                let aliases = Set(
                    [snapshot.spec.name] + (attachment?.aliases ?? [])
                ).sorted()
                return (
                    name,
                    DockerEndpointSettings(
                        aliases: aliases,
                        networkID: "",
                        endpointID: "",
                        gateway: "",
                        ipAddress: address.address,
                        ipPrefixLen: address.prefixLength,
                        macAddress: ""
                    )
                )
            }
        )
        return DockerNetworkSettings(ports: ports, networks: networks)
    }

    func containerCommand(_ spec: ContainerSpec) -> (String, [String]) {
        let executable = spec.entrypoint.first ?? spec.command.first ?? ""
        let args: [String] = if spec.entrypoint.isEmpty {
            Array(spec.command.dropFirst())
        } else {
            Array(spec.entrypoint.dropFirst()) + spec.command
        }
        return (executable, args)
    }

    func environmentList(_ values: [String: String]) -> [String] {
        values.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
    }

    func containerHealth(
        snapshot: ContainerSnapshot,
        context: RuntimeRequestContext
    ) async throws -> DockerContainerHealth? {
        guard
            snapshot.state == .running,
            let healthcheck = snapshot.spec.healthcheck,
            !healthcheck.test.isEmpty,
            healthcheck.test.first != "NONE"
        else {
            return nil
        }
        let identifier = snapshot.dockerID.rawValue
        let started = Date()
        let decision = await healthChecks.decision(
            id: identifier,
            startedAt: snapshot.startedAt,
            healthcheck: healthcheck,
            now: started
        )
        if case let .cached(value) = decision {
            return value
        }

        let exitCode = await executeHealthCheck(
            containerID: identifier,
            command: healthCommand(healthcheck.test),
            timeoutNanoseconds: healthcheck.timeoutNanoseconds,
            context: context
        )
        return await healthChecks.record(
            id: identifier,
            startedAt: snapshot.startedAt,
            healthcheck: healthcheck,
            observation: ContainerHealthObservation(
                exitCode: exitCode,
                started: started,
                ended: Date()
            )
        )
    }

    func healthCommand(_ test: [String]) -> [String] {
        switch test.first {
        case "CMD":
            Array(test.dropFirst())
        case "CMD-SHELL":
            [
                "/bin/sh",
                "-c",
                test.dropFirst().joined(separator: " ")
            ]
        default:
            test
        }
    }

    func executeHealthCheck(
        containerID: String,
        command: [String],
        timeoutNanoseconds: Int64,
        context: RuntimeRequestContext
    ) async -> Int32 {
        do {
            let exec = try await runtime.createExec(
                containerID: containerID,
                spec: ExecSpec(
                    command: command,
                    attachStandardInput: false,
                    attachStandardOutput: false,
                    attachStandardError: false
                ),
                context: context
            )
            let session = try await runtime.startExec(
                id: exec.id,
                context: context
            )
            try await session.closeStandardInput()
            return try await waitForHealthSession(
                session,
                timeoutNanoseconds: timeoutNanoseconds
            )
        } catch {
            return -1
        }
    }

    func waitForHealthSession(
        _ session: any RuntimeProcessSession,
        timeoutNanoseconds: Int64
    ) async throws -> Int32 {
        guard timeoutNanoseconds > 0 else {
            return try await session.wait()
        }
        return try await withThrowingTaskGroup(of: Int32.self) { group in
            group.addTask {
                try await session.wait()
            }
            group.addTask {
                try await Task.sleep(
                    for: .nanoseconds(timeoutNanoseconds)
                )
                await session.cancel()
                return -1
            }
            guard let result = try await group.next() else {
                throw CancellationError()
            }
            group.cancelAll()
            return result
        }
    }

    func mountSummary(_ mount: RuntimeMount) -> DockerMountSummary {
        DockerMountSummary(
            type: mount.type.rawValue,
            name: mount.type == .volume ? mount.source : "",
            source: mount.source,
            destination: mount.destination,
            driver: mount.type == .volume ? "local" : "",
            mode: mount.readOnly ? "ro" : "rw",
            readWrite: !mount.readOnly,
            propagation: ""
        )
    }

    static func networkAddress(
        _ value: String
    ) -> (address: String, prefixLength: Int) {
        let components = value.split(
            separator: "/",
            maxSplits: 1,
            omittingEmptySubsequences: false
        )
        guard
            components.count == 2,
            !components[0].isEmpty,
            let prefixLength = Int(components[1]),
            (0 ... (components[0].contains(":") ? 128 : 32)).contains(prefixLength)
        else {
            return (value, 0)
        }
        return (String(components[0]), prefixLength)
    }

    func execInspect(_ snapshot: ExecSnapshot) -> DockerExecInspect {
        DockerExecInspect(
            id: snapshot.id.rawValue,
            running: snapshot.running,
            exitCode: snapshot.exitCode ?? 0,
            processConfig: DockerExecProcessConfig(
                tty: snapshot.spec.terminal,
                entrypoint: snapshot.spec.command.first ?? "",
                arguments: Array(snapshot.spec.command.dropFirst()),
                user: snapshot.spec.user ?? ""
            ),
            containerID: snapshot.containerID.rawValue
        )
    }

    func imageSummary(_ image: ImageSnapshot) -> DockerImageSummary {
        DockerImageSummary(
            created: Int64(image.createdAt.timeIntervalSince1970),
            id: image.id,
            repoDigests: image.references.filter { $0.contains("@sha256:") },
            repoTags: image.references.filter { !$0.contains("@sha256:") },
            size: image.size,
            virtualSize: image.size
        )
    }

    func imageInspect(_ image: ImageSnapshot) -> DockerImageInspect {
        DockerImageInspect(
            id: image.id,
            repoTags: image.references.filter { !$0.contains("@sha256:") },
            repoDigests: image.references.filter { $0.contains("@sha256:") },
            created: ISO8601DateFormatter().string(from: image.createdAt),
            size: image.size,
            virtualSize: image.size,
            architecture: image.architecture,
            variant: image.architecture == "arm64" ? "v8" : "",
            operatingSystem: image.operatingSystem,
            config: DockerImageConfig(
                user: image.user,
                environment: image.environment,
                entrypoint: image.entrypoint.isEmpty ? nil : image.entrypoint,
                command: image.command.isEmpty ? nil : image.command,
                labels: image.labels
            )
        )
    }

    func networkInspect(_ network: NetworkSnapshot) -> DockerNetworkInspect {
        DockerNetworkInspect(
            name: network.spec.name,
            id: network.id,
            created: ISO8601DateFormatter().string(from: network.createdAt),
            driver: network.spec.driver,
            internalNetwork: network.spec.internalNetwork,
            containers: network.containers.reduce(into: [:]) { result, entry in
                result[entry.key.rawValue] = DockerNetworkContainer(
                    name: entry.key.rawValue,
                    ipv4Address: entry.value
                )
            },
            labels: network.spec.labels
        )
    }

    func volumeInspect(_ volume: VolumeSnapshot) -> DockerVolumeInspect {
        DockerVolumeInspect(
            createdAt: ISO8601DateFormatter().string(from: volume.createdAt),
            driver: volume.spec.driver,
            labels: volume.spec.labels,
            mountpoint: volume.mountpoint,
            name: volume.name
        )
    }

    static func boolValue(_ value: String) -> Bool {
        switch value.lowercased() {
        case "1", "true", "yes":
            true
        default:
            false
        }
    }

    static func dockerIdentifier() -> String {
        let first = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let second = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return first + second
    }

    static func anonymousVolumeName() -> String {
        "devcontainer-\(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())"
    }

    static func bindSourceIsHostPath(_ source: String) -> Bool {
        source.hasPrefix("/")
    }
}
