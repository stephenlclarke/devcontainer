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

extension AppleContainerRuntime {
    func createNativeVolumeIfNeeded(
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

    func nativeBuildKitVolumes() async throws -> [VolumeSnapshot] {
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
        let request = try await AppleEventPollRequest(
            initial: containerMap(labels: labels, context: context),
            since: since,
            until: until,
            labels: labels,
            context: context
        )
        return AsyncThrowingStream { continuation in
            let task = Task {
                await self.pollEvents(request, continuation: continuation)
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    func pollEvents(
        _ request: AppleEventPollRequest,
        continuation: AsyncThrowingStream<RuntimeEvent, any Error>.Continuation
    ) async {
        do {
            var previous = request.initial
            var sequence = Int64(Date().timeIntervalSince1970 * 1_000_000)
            while !Task.isCancelled, request.until.map({ Date() < $0 }) ?? true {
                try await Task.sleep(for: .milliseconds(200))
                let current = try await containerMap(
                    labels: request.labels,
                    context: request.context
                )
                for event in Self.lifecycleEvents(
                    previous: previous,
                    current: current,
                    timestamp: Date(),
                    since: request.since,
                    sequence: &sequence
                ) {
                    continuation.yield(event)
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

    static func lifecycleEvents(
        previous: [String: ContainerSnapshot],
        current: [String: ContainerSnapshot],
        timestamp: Date,
        since: Date?,
        sequence: inout Int64
    ) -> [RuntimeEvent] {
        guard since.map({ timestamp >= $0 }) ?? true else {
            return []
        }
        return createdEvents(
            previous: previous,
            current: current,
            timestamp: timestamp,
            sequence: &sequence
        ) + transitionEvents(
            previous: previous,
            current: current,
            timestamp: timestamp,
            sequence: &sequence
        ) + destroyedEvents(
            previous: previous,
            current: current,
            timestamp: timestamp,
            sequence: &sequence
        )
    }

    static func createdEvents(
        previous: [String: ContainerSnapshot],
        current: [String: ContainerSnapshot],
        timestamp: Date,
        sequence: inout Int64
    ) -> [RuntimeEvent] {
        current.sorted { $0.key < $1.key }.flatMap { element -> [RuntimeEvent] in
            let (id, snapshot) = element
            guard previous[id] == nil else {
                return []
            }
            sequence += 1
            var events = [
                event(
                    sequence: sequence,
                    timestamp: timestamp,
                    snapshot: snapshot,
                    action: .create
                )
            ]
            if snapshot.state == .running {
                sequence += 1
                events.append(
                    event(
                        sequence: sequence,
                        timestamp: timestamp,
                        snapshot: snapshot,
                        action: .start
                    )
                )
            }
            return events
        }
    }

    static func transitionEvents(
        previous: [String: ContainerSnapshot],
        current: [String: ContainerSnapshot],
        timestamp: Date,
        sequence: inout Int64
    ) -> [RuntimeEvent] {
        current.sorted { $0.key < $1.key }.compactMap { id, snapshot in
            guard
                let old = previous[id],
                old.state != snapshot.state,
                let action = eventAction(for: snapshot.state)
            else {
                return nil
            }
            sequence += 1
            return event(
                sequence: sequence,
                timestamp: timestamp,
                snapshot: snapshot,
                action: action
            )
        }
    }

    static func eventAction(
        for state: RuntimeContainerState
    ) -> RuntimeEventAction? {
        switch state {
        case .running:
            .start
        case .stopped:
            .stop
        default:
            nil
        }
    }

    static func destroyedEvents(
        previous: [String: ContainerSnapshot],
        current: [String: ContainerSnapshot],
        timestamp: Date,
        sequence: inout Int64
    ) -> [RuntimeEvent] {
        previous.sorted { $0.key < $1.key }.compactMap { id, snapshot in
            guard current[id] == nil else {
                return nil
            }
            sequence += 1
            return event(
                sequence: sequence,
                timestamp: timestamp,
                snapshot: snapshot,
                action: .destroy
            )
        }
    }

    func containerMap(
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

    static func event(
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

    func synchronizeNetworkHosts(
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
            try await synchronizeNetworkHosts(
                target: target,
                containers: running,
                context: context
            )
        }
    }

    func synchronizeNetworkHosts(
        target: ContainerSnapshot,
        containers: [ContainerSnapshot],
        context: RuntimeRequestContext
    ) async throws {
        let hosts = Self.managedHosts(target: target, containers: containers)
        let temporary = try TemporaryDirectory(base: Self.transferDirectory)
        defer { temporary.remove() }
        let localHosts = temporary.url.appendingPathComponent("hosts")
        let download = try await command([
            "cp",
            "\(target.runtimeID.rawValue):/etc/hosts",
            localHosts.path
        ])
        let observedState = await (try? inspectContainer(
            id: target.runtimeID.rawValue,
            context: context
        ).state)
        if download.exitCode != 0 {
            if observedState != .running
                || Self.isTransientContainerCopyFailure(download)
            {
                return
            }
            try requireSuccess(download, operation: "container hosts download")
        }
        let current = try String(contentsOf: localHosts, encoding: .utf8)
        let updated = Self.replacingManagedHosts(in: current, with: hosts)
        try Data(updated.utf8).write(to: localHosts, options: .atomic)
        let upload = try await command([
            "cp",
            localHosts.path,
            "\(target.runtimeID.rawValue):/etc/hosts"
        ])
        if upload.exitCode != 0 {
            let observedState = await (try? inspectContainer(
                id: target.runtimeID.rawValue,
                context: context
            ).state)
            if observedState != .running
                || Self.isTransientContainerCopyFailure(upload)
            {
                return
            }
        }
        try requireSuccess(upload, operation: "container hosts upload")
    }

    static func isTransientContainerCopyFailure(
        _ result: AppleCommandResult
    ) -> Bool {
        guard result.exitCode != 0 else {
            return false
        }
        let diagnostic = String(
            bytes: result.standardError,
            encoding: .utf8
        )?.lowercased() ?? ""
        return diagnostic.contains("container is not running")
            || diagnostic.contains("container was not found")
    }

    static func managedHosts(
        target: ContainerSnapshot,
        containers: [ContainerSnapshot]
    ) -> String {
        let targetNetworks = Set(
            target.spec.networks.map(\.name).filter(isUserDefinedNetwork)
        )
        let lines = Set(containers.flatMap { source in
            source.spec.networks.compactMap { attachment in
                managedHostLine(
                    source: source,
                    attachment: attachment,
                    targetNetworks: targetNetworks
                )
            }
        })
        return """
        # BEGIN devcontainer managed network hosts
        \(lines.sorted().joined(separator: "\n"))
        # END devcontainer managed network hosts

        """
    }

    static func managedHostLine(
        source: ContainerSnapshot,
        attachment: NetworkAttachment,
        targetNetworks: Set<String>
    ) -> String? {
        guard
            targetNetworks.contains(attachment.name),
            let address = networkAddress(source, network: attachment.name)
        else {
            return nil
        }
        let names = Set(
            [source.spec.name, source.spec.hostname].compactMap(\.self)
                + attachment.aliases
        ).filter(isSafeHostName)
        guard !names.isEmpty else {
            return nil
        }
        return "\(address) \(names.sorted().joined(separator: " "))"
    }

    static func isUserDefinedNetwork(_ name: String) -> Bool {
        !["bridge", "default", "host", "none"].contains(name)
    }

    static var defaultVolumeRoot: URL {
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

    static func networkAddress(
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

    static func isSafeHostName(_ value: String) -> Bool {
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

    func command(
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

    func process(_ arguments: [String]) throws -> AppleProcessSession {
        try AppleProcessSession(
            executable: executable,
            arguments: arguments,
            environment: environment
        )
    }

    func requireSuccess(
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

    func parseJSONObjectArray(_ data: Data) throws -> [[String: Any]] {
        guard let values = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw DevContainerError(.providerProtocolMismatch, message: "Apple container returned non-array JSON")
        }
        return values
    }

    func containerSnapshot(_ value: [String: Any]) throws -> ContainerSnapshot {
        let record = try containerRecord(value)
        let wasStarted = startedContainers.contains(record.id)
            || startedContainers.contains(record.dockerID)
        let requestedSpec = requestedSpec(for: record)
        let state = Self.containerState(
            record.state,
            createdByThisEngine: requestedSpec != nil,
            wasStarted: wasStarted
        )
        let inferredStartedAt = containerStartedAt[record.id]
            ?? containerStartedAt[record.dockerID]
            ?? (state == .running ? record.createdAt : nil)
        var snapshot = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: record.id),
            dockerID: DockerID(rawValue: record.dockerID),
            spec: record.spec,
            state: state,
            createdAt: record.createdAt,
            startedAt: record.startedAt ?? inferredStartedAt,
            finishedAt: record.finishedAt,
            exitCode: record.exitCode,
            networkAddresses: record.networkAddresses
        )
        if var requestedSpec {
            requestedSpec.labels.merge(snapshot.spec.labels) { _, observedValue in observedValue }
            snapshot.spec = requestedSpec
        }
        return snapshot
    }
}
