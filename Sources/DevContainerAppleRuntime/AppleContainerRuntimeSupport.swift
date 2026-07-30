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
import ContainerizationError
import ContainerResource
import Darwin
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension AppleContainerRuntime {
    func containerRecord(
        _ value: ContainerResource.ContainerSnapshot
    ) throws -> AppleContainerRecord {
        let configuration = value.configuration
        let process = configuration.initProcess
        let labels = configuration.labels
        let id = value.id
        return AppleContainerRecord(
            id: id,
            dockerID: labels[Self.dockerIDLabel] ?? id,
            spec: ContainerSpec(
                name: id,
                image: configuration.image.reference,
                command: process.executable.isEmpty
                    ? process.arguments
                    : [process.executable] + process.arguments,
                environment: Self.environmentDictionary(process.environment),
                labels: labels,
                workingDirectory: process.workingDirectory,
                user: process.user.description,
                mounts: configuration.mounts.compactMap(Self.mount),
                ports: configuration.publishedPorts.flatMap(Self.ports),
                networks: configuration.networks.map {
                    NetworkAttachment(name: $0.network)
                },
                terminal: process.terminal,
                openStandardInput: false,
                privileged: false,
                initProcess: configuration.useInit,
                capabilitiesToAdd: configuration.capAdd,
                capabilitiesToDrop: configuration.capDrop
            ),
            state: value.status.rawValue,
            createdAt: configuration.creationDate,
            startedAt: value.startedDate,
            finishedAt: nil,
            exitCode: nil,
            networkAddresses: Dictionary(
                uniqueKeysWithValues: value.networks.map {
                    ($0.network, $0.ipv4Address.address.description)
                }
            )
        )
    }

    static func mount(_ value: Filesystem) -> RuntimeMount? {
        let type: RuntimeMountType
        switch value.type {
        case .virtiofs:
            type = .bind
        case .volume:
            type = .volume
        case .tmpfs:
            type = .tmpfs
        case .block:
            return nil
        }
        return RuntimeMount(
            type: type,
            source: value.source,
            destination: value.destination,
            readOnly: value.options.contains("ro")
        )
    }

    static func ports(_ value: PublishPort) -> [PortBinding] {
        (0 ..< value.count).map { offset in
            PortBinding(
                containerPort: value.containerPort + offset,
                hostPort: value.hostPort + offset,
                protocolName: value.proto.rawValue,
                hostAddress: value.hostAddress.description
            )
        }
    }

    func containerRecord(_ value: [String: Any]) throws -> AppleContainerRecord {
        guard
            let id = value["id"] as? String,
            let configuration = value["configuration"] as? [String: Any]
        else {
            throw DevContainerError(.providerProtocolMismatch, message: "invalid Apple container record")
        }
        let status = value["status"] as? [String: Any]
        let labels = configuration["labels"] as? [String: String] ?? [:]
        let creationDate = Self.date(configuration["creationDate"]) ?? Date(timeIntervalSince1970: 0)
        let dockerID = labels[Self.dockerIDLabel] ?? id
        return AppleContainerRecord(
            id: id,
            dockerID: dockerID,
            spec: Self.observedContainerSpec(
                id: id,
                configuration: configuration,
                labels: labels
            ),
            state: status?["state"] as? String ?? "unknown",
            createdAt: creationDate,
            startedAt: Self.date(status?["startedDate"]),
            finishedAt: Self.date(value["exitedDate"]),
            exitCode: Self.number(value["exitCode"] ?? status?["exitCode"])
                .flatMap(Int32.init(exactly:)),
            networkAddresses: Self.networkAddresses(status)
        )
    }

    func requestedContainer(for record: AppleContainerRecord) -> RequestedContainer? {
        let id = record.id
        let dockerID = record.dockerID
        let requestKey = requestedContainers[id] != nil ? id : dockerID
        var request = requestedContainers[requestKey]
        if let requestedCreatedAt = request?.createdAt {
            if !Self.sameContainerIncarnation(
                metadataCreatedAt: requestedCreatedAt,
                observedCreatedAt: record.createdAt
            ) {
                discardContainerState(
                    id: id,
                    dockerID: dockerID,
                    name: request?.spec.name
                )
                return nil
            }
        } else if request != nil {
            request?.createdAt = record.createdAt
            requestedContainers[requestKey] = request
        }
        return request
    }

    func directAPIError(
        _ error: any Error,
        operation: String
    ) -> DevContainerError {
        if let error = error as? DevContainerError {
            return error
        }
        if let error = error as? ContainerizationError {
            let code: DevContainerErrorCode =
                switch error.code {
                case .invalidArgument:
                    .invalidRequest
                case .exists, .invalidState:
                    .conflict
                case .notFound:
                    .notFound
                case .cancelled, .interrupted:
                    .cancelled
                case .timeout:
                    .deadlineExceeded
                case .unsupported:
                    .unsupportedCapability
                default:
                    .runtimeUnavailable
                }
            return DevContainerError(
                code,
                message: "\(operation) failed: \(error)"
            )
        }
        return DevContainerError(
            .runtimeUnavailable,
            message: "\(operation) failed: \(error)"
        )
    }

    func discardContainerState(
        id: String,
        dockerID: String,
        name: String? = nil
    ) {
        requestedContainers.removeValue(forKey: id)
        requestedContainers.removeValue(forKey: dockerID)
        if let name {
            requestedContainers.removeValue(forKey: name)
        }
        startedContainers.remove(id)
        startedContainers.remove(dockerID)
        if let name {
            startedContainers.remove(name)
        }
        containerStartedAt.removeValue(forKey: id)
        containerStartedAt.removeValue(forKey: dockerID)
        if let name {
            containerStartedAt.removeValue(forKey: name)
        }
        managedHostsState.removeValue(forKey: id)
        containerExitTasks.removeValue(forKey: id)?.cancel()
        containerExits.removeValue(forKey: id)
    }

    static func containerState(
        _ state: String,
        createdByThisEngine: Bool,
        wasStarted: Bool
    ) -> RuntimeContainerState {
        switch state {
        case "created":
            .created
        case "running":
            .running
        case "stopped":
            createdByThisEngine && !wasStarted ? .created : .stopped
        default:
            .unknown
        }
    }

    static func observedContainerSpec(
        id: String,
        configuration: [String: Any],
        labels: [String: String]
    ) -> ContainerSpec {
        let process = configuration["initProcess"] as? [String: Any] ?? [:]
        let executable = process["executable"] as? String ?? ""
        let arguments = process["arguments"] as? [String] ?? []
        return ContainerSpec(
            name: id,
            image: (configuration["image"] as? [String: Any])?["reference"] as? String ?? "",
            command: executable.isEmpty ? arguments : [executable] + arguments,
            environment: environmentDictionary(process["environment"] as? [String] ?? []),
            labels: labels,
            workingDirectory: process["workingDirectory"] as? String,
            user: user(process["user"]),
            hostname: configuration["hostname"] as? String,
            mounts: (configuration["mounts"] as? [[String: Any]] ?? []).compactMap(mount),
            ports: (configuration["publishedPorts"] as? [[String: Any]] ?? []).compactMap(port),
            networks: networkAttachments(configuration),
            terminal: process["terminal"] as? Bool ?? false,
            openStandardInput: false,
            privileged: process["privileged"] as? Bool ?? false,
            initProcess: configuration["useInit"] as? Bool ?? false,
            capabilitiesToAdd: configuration["capAdd"] as? [String] ?? [],
            capabilitiesToDrop: configuration["capDrop"] as? [String] ?? [],
            securityOptions: securityOptions(configuration)
        )
    }

    static func effectiveContainerSpec(
        requested: ContainerSpec,
        observed: ContainerSpec
    ) -> ContainerSpec {
        var spec = requested
        spec.environment = observed.environment
        spec.environment.merge(requested.environment) { _, requestedValue in
            requestedValue
        }
        if requested.user?.isEmpty ?? true {
            spec.user = observed.user
        }
        if requested.hostname?.isEmpty ?? true {
            spec.hostname = observed.hostname
        }
        spec.labels.merge(observed.labels) { _, observedValue in observedValue }
        return spec
    }

    static func networkAttachments(
        _ configuration: [String: Any]
    ) -> [NetworkAttachment] {
        (configuration["networks"] as? [[String: Any]] ?? []).compactMap { network in
            guard let name = network["network"] as? String else {
                return nil
            }
            let options = network["options"] as? [String: Any]
            return NetworkAttachment(
                name: name,
                aliases: options?["aliases"] as? [String] ?? []
            )
        }
    }

    func apply(
        metadata: RuntimeContainerMetadata,
        to observed: DevContainerModel.ContainerSnapshot
    ) -> DevContainerModel.ContainerSnapshot {
        var snapshot = observed
        snapshot.spec = Self.effectiveContainerSpec(
            requested: metadata.spec,
            observed: observed.spec
        )
        snapshot.dockerID = metadata.dockerID
        snapshot.imageID = metadata.imageID
        snapshot.createdAt = metadata.createdAt
        snapshot.startedAt = metadata.startedAt ?? observed.startedAt
        if observed.state == .stopped, metadata.startedAt == nil {
            snapshot.state = .created
            snapshot.exitCode = nil
        }
        return snapshot
    }

    func imageSnapshot(_ value: [String: Any]) -> ImageSnapshot? {
        guard
            let id = value["id"] as? String,
            let configuration = value["configuration"] as? [String: Any]
        else {
            return nil
        }
        let name = configuration["name"] as? String
        let variants = value["variants"] as? [[String: Any]] ?? []
        let arm =
            variants.first(where: {
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

    func networkSnapshot(_ value: [String: Any]) -> NetworkSnapshot? {
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

    func networkSnapshot(
        _ value: NetworkResource
    ) -> NetworkSnapshot {
        NetworkSnapshot(
            id: value.id,
            spec: NetworkSpec(
                name: value.name,
                labels: value.configuration.labels.dictionary,
                driver: value.configuration.plugin,
                internalNetwork: value.configuration.mode == .hostOnly
            ),
            createdAt: value.creationDate
        )
    }

    static func mount(_ value: [String: Any]) -> RuntimeMount? {
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

    static func port(_ value: [String: Any]) -> PortBinding? {
        guard
            let containerPort = number(value["containerPort"]).flatMap({ UInt16(exactly: $0) })
        else {
            return nil
        }
        return PortBinding(
            containerPort: containerPort,
            hostPort: number(value["hostPort"]).flatMap { UInt16(exactly: $0) },
            protocolName: value["protocol"] as? String ?? "tcp",
            hostAddress: value["hostAddress"] as? String ?? "0.0.0.0"
        )
    }

    static func networkAddresses(_ status: [String: Any]?) -> [String: String] {
        let networks = status?["networks"] as? [[String: Any]] ?? []
        return Dictionary(
            uniqueKeysWithValues: networks.compactMap { network in
                guard
                    let name = network["network"] as? String,
                    let address =
                    (network["ipv4Address"] as? String
                        ?? network["address"] as? String)
                else {
                    return nil
                }
                return (name, address)
            }
        )
    }

    static func number(_ value: Any?) -> Int? {
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

    static func user(_ value: Any?) -> String? {
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

    static func equivalentImageReference(_ lhs: String, _ rhs: String) -> Bool {
        normalizedImageReference(lhs) == normalizedImageReference(rhs)
    }

    static func imageDigest(_ reference: String) -> String? {
        guard let separator = reference.lastIndex(of: "@") else {
            return nil
        }
        let digest = String(reference[reference.index(after: separator)...])
        return digest.hasPrefix("sha256:") ? digest : nil
    }

    static func normalizedImageReference(_ reference: String) -> String {
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

    static func date(_ value: Any?) -> Date? {
        guard let value = value as? String else {
            return nil
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }

    static func archiveStat(
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
        let linkTarget: String =
            if status.st_mode & S_IFMT == S_IFLNK {
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

    static func dockerFileMode(_ mode: mode_t) -> UInt32 {
        UInt32(mode & (S_IRWXU | S_IRWXG | S_IRWXO))
            | dockerFileTypeMode(mode)
            | dockerModeBit(mode, mask: S_ISUID, bit: 1 << 23)
            | dockerModeBit(mode, mask: S_ISGID, bit: 1 << 22)
            | dockerModeBit(mode, mask: S_ISVTX, bit: 1 << 20)
    }

    static func dockerFileTypeMode(_ mode: mode_t) -> UInt32 {
        switch mode & S_IFMT {
        case S_IFDIR:
            1 << 31
        case S_IFLNK:
            1 << 27
        case S_IFBLK:
            1 << 26
        case S_IFIFO:
            1 << 25
        case S_IFSOCK:
            1 << 24
        case S_IFCHR:
            (1 << 26) | (1 << 21)
        case S_IFREG:
            0
        default:
            1 << 19
        }
    }

    static func dockerModeBit(
        _ mode: mode_t,
        mask: mode_t,
        bit: UInt32
    ) -> UInt32 {
        mode & mask == 0 ? 0 : bit
    }

    static var transferDirectory: URL {
        let cache =
            FileManager.default.urls(
                for: .cachesDirectory,
                in: .userDomainMask
            ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return cache.appendingPathComponent("devcontainer/transfers", isDirectory: true)
    }

    static var defaultPlatform: String {
        #if arch(arm64)
            "linux/arm64"
        #else
            "linux/amd64"
        #endif
    }

    static func environmentDictionary(_ values: [String]) -> [String: String] {
        values.reduce(into: [:]) { environment, value in
            let parts = value.split(
                separator: "=",
                maxSplits: 1,
                omittingEmptySubsequences: false
            )
            environment[String(parts[0])] =
                parts.count == 2 ? String(parts[1]) : ""
        }
    }

    static func securityOptions(_ configuration: [String: Any]) -> [String] {
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

    static func filteredEnvironment(_ values: [String: String]) -> [String: String] {
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
        return Dictionary(
            uniqueKeysWithValues: allowed.compactMap { key in
                values[key].map { (key, $0) }
            }
        )
    }

    static func dataStream(
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
                Self.cancelStreamTask(task, session: session)
            }
        }
    }

    private static func cancelStreamTask(
        _ task: Task<Void, Never>,
        session: AppleProcessSession
    ) {
        task.cancel()
        Task {
            session.cancel()
        }
    }

    func pollLogs(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> AppleLogPoll {
        do {
            let snapshot = try await inspectContainer(id: id, context: context)
            guard
                snapshot.state == .running
                || (snapshot.state == .stopped
                    && wasStarted(id: id, snapshot: snapshot))
            else {
                return AppleLogPoll(
                    standardOutput: Data(),
                    standardError: Data(),
                    finished: false,
                    exitCode: 0
                )
            }
            let (standardOutput, standardError) = try await attachLogData(
                snapshot: snapshot
            )
            return AppleLogPoll(
                standardOutput: standardOutput,
                standardError: standardError,
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

    private func attachLogData(
        snapshot: DevContainerModel.ContainerSnapshot
    ) async throws -> (Data, Data) {
        if useDirectProcessAPI {
            return try await (
                exactContainerLog(id: snapshot.runtimeID.rawValue),
                Data()
            )
        }
        let result = try await command([
            "logs",
            snapshot.runtimeID.rawValue
        ])
        if result.exitCode != 0, snapshot.state == .running {
            return (Data(), Data())
        }
        try requireSuccess(result, operation: "container attach logs")
        return (result.standardOutput, result.standardError)
    }

    func exactContainerLog(id: String) async throws -> Data {
        let handles = try await apiClient.logs(id: id)
        defer {
            for handle in handles {
                try? handle.close()
            }
        }
        guard let handle = handles.first else {
            return Data()
        }
        return try Self.readAvailableLogData(from: handle)
    }

    static func readAvailableLogData(from handle: FileHandle) throws -> Data {
        let descriptor = handle.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        defer {
            _ = fcntl(descriptor, F_SETFL, flags)
        }

        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            let count = buffer.withUnsafeMutableBytes { bytes in
                Darwin.read(descriptor, bytes.baseAddress, bytes.count)
            }
            switch count {
            case let value where value > 0:
                result.append(contentsOf: buffer.prefix(value))
            case 0:
                return result
            default:
                switch errno {
                case EINTR:
                    continue
                case EAGAIN, EWOULDBLOCK:
                    return result
                default:
                    throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
                }
            }
        }
    }

    func scheduleAutomaticRemoval(id: String) {
        Task {
            try? await Task.sleep(for: .seconds(1))
            try? await self.removeContainer(
                id: id,
                force: true,
                context: RuntimeRequestContext()
            )
        }
    }

    func wasStarted(
        id: String,
        snapshot: DevContainerModel.ContainerSnapshot
    ) -> Bool {
        snapshot.startedAt != nil
            || startedContainers.contains(id)
            || startedContainers.contains(snapshot.runtimeID.rawValue)
            || startedContainers.contains(snapshot.spec.name)
    }

    func resolveContainerID(
        _ id: String,
        context: RuntimeRequestContext
    ) async throws -> String {
        try await inspectContainer(id: id, context: context).runtimeID.rawValue
    }
}
