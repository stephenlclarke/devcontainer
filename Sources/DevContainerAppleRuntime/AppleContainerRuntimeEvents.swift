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

// Event polling, Docker-compatible events, and managed network-host recovery
// share actor-isolated state; the runtime is split across focused extension
// files, while this cohesive event boundary exceeds the default line limit.
// swiftlint:disable file_length

actor AppleEventPoller {
    typealias SnapshotProvider = @Sendable (RuntimeRequestContext) async throws
        -> [String: ContainerSnapshot]

    private struct Subscription {
        let continuation: AsyncThrowingStream<RuntimeEvent, any Error>.Continuation
        let since: Date?
        let until: Date?
        let labels: [String: String]
        var previous: [String: ContainerSnapshot]
        var sequence: Int64
    }

    private let snapshotProvider: SnapshotProvider
    private var latestSnapshot: [String: ContainerSnapshot]?
    private var initialSnapshotTask: Task<[String: ContainerSnapshot], Error>?
    private var subscriptions: [UUID: Subscription] = [:]
    private var pollingTask: Task<Void, Never>?
    private var pollingID: UUID?
    private var changeGeneration: UInt64 = 0
    private var changeWaiters: [UUID: CheckedContinuation<Void, Never>] = [:]

    init(snapshotProvider: @escaping SnapshotProvider) {
        self.snapshotProvider = snapshotProvider
    }

    func subscribe(
        continuation: AsyncThrowingStream<RuntimeEvent, any Error>.Continuation,
        since: Date?,
        until: Date?,
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> UUID {
        let identifier = UUID()
        let initial = try await snapshot(context: context)
        subscriptions[identifier] = Subscription(
            continuation: continuation,
            since: since,
            until: until,
            labels: labels,
            previous: Self.filtered(initial, labels: labels),
            sequence: Int64(Date().timeIntervalSince1970 * 1_000_000)
        )
        startPollingIfNeeded()
        return identifier
    }

    func unsubscribe(_ identifier: UUID) {
        subscriptions.removeValue(forKey: identifier)
        if subscriptions.isEmpty {
            stopPolling()
        }
    }

    func shutdown() {
        for subscription in subscriptions.values {
            subscription.continuation.finish()
        }
        subscriptions.removeAll()
        stopPolling()
    }

    func notifyChanged() {
        changeGeneration &+= 1
        let waiters = changeWaiters.values
        changeWaiters.removeAll(keepingCapacity: true)
        for waiter in waiters {
            waiter.resume()
        }
    }

    private func snapshot(
        context: RuntimeRequestContext
    ) async throws -> [String: ContainerSnapshot] {
        if let latestSnapshot {
            return latestSnapshot
        }
        if let initialSnapshotTask {
            return try await initialSnapshotTask.value
        }
        let provider = snapshotProvider
        let task = Task {
            try await provider(context)
        }
        initialSnapshotTask = task
        do {
            let snapshot = try await task.value
            latestSnapshot = snapshot
            initialSnapshotTask = nil
            return snapshot
        } catch {
            initialSnapshotTask = nil
            throw error
        }
    }

    private func startPollingIfNeeded() {
        guard pollingTask == nil else {
            return
        }
        let identifier = UUID()
        pollingID = identifier
        let observedGeneration = changeGeneration
        pollingTask = Task { [weak self] in
            await self?.poll(
                identifier: identifier,
                observedGeneration: observedGeneration
            )
        }
    }

    private func stopPolling() {
        pollingTask?.cancel()
        pollingTask = nil
        pollingID = nil
        latestSnapshot = nil
    }

    private func poll(
        identifier: UUID,
        observedGeneration initialGeneration: UInt64
    ) async {
        var observedGeneration = initialGeneration
        defer {
            if pollingID == identifier {
                pollingTask = nil
                pollingID = nil
                if subscriptions.isEmpty {
                    latestSnapshot = nil
                } else {
                    startPollingIfNeeded()
                }
            }
        }
        do {
            while !Task.isCancelled {
                guard !activeSubscriptionIDs(at: Date()).isEmpty else {
                    return
                }
                await waitForChange(after: observedGeneration)
                guard
                    !Task.isCancelled,
                    !subscriptions.isEmpty
                else {
                    continue
                }
                // The subscription set can change while the poller sleeps.
                // Re-evaluate immediately before reading the inventory so a
                // late subscriber sees this snapshot and an expired one never
                // receives an event beyond its requested deadline.
                let active = activeSubscriptionIDs(at: Date())
                guard !active.isEmpty else {
                    return
                }
                let current = try await snapshotProvider(RuntimeRequestContext())
                latestSnapshot = current
                publish(current, to: active, timestamp: Date())
                observedGeneration = changeGeneration
            }
        } catch is CancellationError {
            return
        } catch {
            for subscription in subscriptions.values {
                subscription.continuation.finish(throwing: error)
            }
            subscriptions.removeAll()
        }
    }

    private func waitForChange(after observedGeneration: UInt64) async {
        guard changeGeneration == observedGeneration else {
            return
        }
        let identifier = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                guard changeGeneration == observedGeneration else {
                    continuation.resume()
                    return
                }
                changeWaiters[identifier] = continuation
                scheduleChangeWaiterTimeout(identifier)
            }
        } onCancel: {
            Task {
                await self.resumeChangeWaiter(identifier)
            }
        }
    }

    private func scheduleChangeWaiterTimeout(_ identifier: UUID) {
        Task {
            try? await Task.sleep(for: .milliseconds(200))
            self.resumeChangeWaiter(identifier)
        }
    }

    private func resumeChangeWaiter(_ identifier: UUID) {
        changeWaiters.removeValue(forKey: identifier)?.resume()
    }

    private func activeSubscriptionIDs(at timestamp: Date) -> [UUID] {
        var active: [UUID] = []
        var expired: [UUID] = []
        for (identifier, subscription) in subscriptions {
            guard subscription.until.map({ timestamp < $0 }) ?? true else {
                expired.append(identifier)
                continue
            }
            active.append(identifier)
        }
        for identifier in expired {
            subscriptions.removeValue(forKey: identifier)?.continuation.finish()
        }
        return active
    }

    private func publish(
        _ snapshot: [String: ContainerSnapshot],
        to identifiers: [UUID],
        timestamp: Date
    ) {
        for identifier in identifiers {
            guard var subscription = subscriptions[identifier] else {
                continue
            }
            let current = Self.filtered(snapshot, labels: subscription.labels)
            let events = AppleContainerRuntime.lifecycleEvents(
                previous: subscription.previous,
                current: current,
                timestamp: timestamp,
                since: subscription.since,
                sequence: &subscription.sequence
            )
            for event in events {
                subscription.continuation.yield(event)
            }
            subscription.previous = current
            subscriptions[identifier] = subscription
        }
    }

    private static func filtered(
        _ snapshots: [String: ContainerSnapshot],
        labels: [String: String]
    ) -> [String: ContainerSnapshot] {
        var filtered: [String: ContainerSnapshot] = [:]
        filtered.reserveCapacity(snapshots.count)
        for (identifier, snapshot) in snapshots {
            guard labels.allSatisfy({ key, expected in
                guard let actual = snapshot.spec.labels[key] else {
                    return false
                }
                return expected.isEmpty || actual == expected
            }) else {
                continue
            }
            filtered[identifier] = snapshot
        }
        return filtered
    }
}

extension AppleContainerRuntime {
    static let maximumManagedHostsBytes = 1024 * 1024
    static let maximumManagedHostsBase64Bytes =
        ((maximumManagedHostsBytes + 2) / 3) * 4
    static let maximumManagedHostsEncodedOutputBytes =
        maximumManagedHostsBase64Bytes + 8 * 1024

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
        let poller = eventPoller()
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        let identifier = try await poller.subscribe(
            continuation: stream.continuation,
            since: since,
            until: until,
            labels: labels,
            context: context
        )
        stream.continuation.onTermination = { _ in
            Task {
                await poller.unsubscribe(identifier)
            }
        }
        return stream.stream
    }

    private func eventPoller() -> AppleEventPoller {
        if let eventPollerState {
            return eventPollerState
        }
        let poller = AppleEventPoller { [weak self] context in
            guard let self else {
                throw CancellationError()
            }
            return try await containerMap(context: context)
        }
        eventPollerState = poller
        return poller
    }

    func signalEventPollers() async {
        await eventPollerState?.notifyChanged()
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
        var events: [RuntimeEvent] = []
        events.reserveCapacity(current.count + previous.count)
        let sortedCurrent = current.sorted(by: { $0.key < $1.key })
        Self.appendCreatedEvents(
            sortedCurrent,
            previous: previous,
            timestamp: timestamp,
            sequence: &sequence,
            events: &events
        )
        Self.appendTransitionEvents(
            sortedCurrent,
            previous: previous,
            timestamp: timestamp,
            sequence: &sequence,
            events: &events
        )
        Self.appendDestroyedEvents(
            previous.sorted(by: { $0.key < $1.key }),
            current: current,
            timestamp: timestamp,
            sequence: &sequence,
            events: &events
        )
        return events
    }

    private static func appendCreatedEvents(
        _ current: [(key: String, value: ContainerSnapshot)],
        previous: [String: ContainerSnapshot],
        timestamp: Date,
        sequence: inout Int64,
        events: inout [RuntimeEvent]
    ) {
        for (identifier, snapshot) in current where previous[identifier] == nil {
            sequence += 1
            events.append(
                event(
                    sequence: sequence,
                    timestamp: timestamp,
                    snapshot: snapshot,
                    action: .create
                )
            )
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
        }
    }

    private static func appendTransitionEvents(
        _ current: [(key: String, value: ContainerSnapshot)],
        previous: [String: ContainerSnapshot],
        timestamp: Date,
        sequence: inout Int64,
        events: inout [RuntimeEvent]
    ) {
        for (identifier, snapshot) in current {
            if previous[identifier] == nil {
                continue
            }
            guard
                let previousSnapshot = previous[identifier],
                previousSnapshot.state != snapshot.state,
                let action = eventAction(for: snapshot.state)
            else {
                continue
            }
            sequence += 1
            events.append(
                event(
                    sequence: sequence,
                    timestamp: timestamp,
                    snapshot: snapshot,
                    action: action
                )
            )
        }
    }

    private static func appendDestroyedEvents(
        _ previous: [(key: String, value: ContainerSnapshot)],
        current: [String: ContainerSnapshot],
        timestamp: Date,
        sequence: inout Int64,
        events: inout [RuntimeEvent]
    ) {
        for (identifier, snapshot) in previous where current[identifier] == nil {
            sequence += 1
            events.append(
                event(
                    sequence: sequence,
                    timestamp: timestamp,
                    snapshot: snapshot,
                    action: .destroy
                )
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

    func containerMap(
        context: RuntimeRequestContext
    ) async throws -> [String: ContainerSnapshot] {
        let snapshots = try await listContainers(
            all: true,
            labels: [:],
            context: context
        )
        var containers: [String: ContainerSnapshot] = [:]
        containers.reserveCapacity(snapshots.count)
        for snapshot in snapshots {
            containers[snapshot.runtimeID.rawValue] = snapshot
        }
        return containers
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
        context: RuntimeRequestContext,
        containers: [ContainerSnapshot]? = nil
    ) async throws {
        let inventory: [ContainerSnapshot] = if let containers {
            containers
        } else {
            try await listContainers(
                all: true,
                labels: [:],
                context: context
            )
        }
        let observed = Dictionary(
            uniqueKeysWithValues: inventory.map {
                ($0.runtimeID.rawValue, $0.createdAt)
            }
        )
        managedHostsState = managedHostsState.filter { id, state in
            observed[id] == state.createdAt
        }
        let running = inventory.filter {
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

    // Direct and CLI fallback copies intentionally retain identical
    // state-race handling.
    // swiftlint:disable:next function_body_length
    func synchronizeNetworkHosts(
        target: ContainerSnapshot,
        containers: [ContainerSnapshot],
        context: RuntimeRequestContext
    ) async throws {
        let hosts = Self.managedHosts(target: target, containers: containers)
        let targetID = target.runtimeID.rawValue
        let nextState = AppleManagedHostsState(
            createdAt: target.createdAt,
            managedHosts: hosts
        )
        guard managedHostsState[targetID] != nextState else {
            return
        }
        let temporary = try TemporaryDirectory(base: Self.transferDirectory)
        defer { temporary.remove() }
        let localHosts = temporary.url.appendingPathComponent("hosts")
        if useDirectContainerAPI {
            guard try await copyHostsAfterBootstrap(
                id: targetID,
                destination: localHosts.path,
                context: context
            ) else {
                return
            }
        } else {
            let download = try await command([
                "cp",
                "\(targetID):/etc/hosts",
                localHosts.path
            ])
            let observedState = await (try? inspectContainer(
                id: targetID,
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
        }
        try Self.validateManagedHostsFileSize(localHosts)
        let current = try String(contentsOf: localHosts, encoding: .utf8)
        let updated = Self.replacingManagedHosts(in: current, with: hosts)
        if current != updated {
            try Data(updated.utf8).write(to: localHosts, options: .atomic)
            if useDirectContainerAPI {
                guard try await copyHostsIntoContainer(
                    id: targetID,
                    source: localHosts,
                    context: context
                ) else {
                    return
                }
            } else {
                let upload = try await command([
                    "cp",
                    localHosts.path,
                    "\(targetID):/etc/hosts"
                ])
                if upload.exitCode != 0 {
                    let observedState = await (try? inspectContainer(
                        id: targetID,
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
        }
        managedHostsState[targetID] = nextState
    }

    /// Apple's copy API cannot read the guest-generated `/etc/hosts` on the
    /// stock runtime. Prefer the API and use a native exec only for this
    /// runtime-owned file, retaining state-race handling in both paths.
    private func copyHostsAfterBootstrap(
        id: String,
        destination: String,
        context: RuntimeRequestContext
    ) async throws -> Bool {
        // Stock Apple Container exposes inventory through the public client,
        // but its generated hosts file is not a copyable filesystem resource.
        // Avoid entering that indefinitely blocking API path when the version
        // probe has identified the stock distribution.
        if directContainerInventorySupported != true {
            do {
                try context.checkActive()
                try await fileClient.copyOut(
                    id: id,
                    source: "/etc/hosts",
                    destination: destination
                )
                try context.checkActive()
                return true
            } catch let error as DevContainerError
                where error.code == .cancelled || error.code == .deadlineExceeded
            {
                throw error
            } catch {
                let observedState = await (try? inspectContainer(
                    id: id,
                    context: context
                ).state)
                guard observedState == .running else {
                    return false
                }
            }
        }
        let download = try await downloadHostsWithExec(id: id)
        if download.exitCode != 0 {
            let observedState = await (try? inspectContainer(
                id: id,
                context: context
            ).state)
            guard observedState == .running else {
                return false
            }
            try requireSuccess(download, operation: "container hosts download")
        }
        try download.standardOutput.write(to: URL(fileURLWithPath: destination))
        return true
    }

    private func copyHostsIntoContainer(
        id: String,
        source: URL,
        context: RuntimeRequestContext
    ) async throws -> Bool {
        if directContainerInventorySupported != true {
            do {
                try context.checkActive()
                try await fileClient.copyIn(
                    id: id,
                    source: source.path,
                    destination: "/etc/hosts"
                )
                try context.checkActive()
                return true
            } catch let error as DevContainerError
                where error.code == .cancelled || error.code == .deadlineExceeded
            {
                throw error
            } catch {
                let observedState = await (try? inspectContainer(
                    id: id,
                    context: context
                ).state)
                guard observedState == .running else {
                    return false
                }
            }
        }
        try Self.validateManagedHostsFileSize(source)
        let upload = try await uploadHostsWithExec(
            id: id,
            contents: Data(contentsOf: source)
        )
        if upload.exitCode != 0 {
            let observedState = await (try? inspectContainer(
                id: id,
                context: context
            ).state)
            guard observedState == .running else {
                return false
            }
            try requireSuccess(upload, operation: "container hosts upload")
        }
        return true
    }

    private func downloadHostsWithExec(id: String) async throws -> AppleCommandResult {
        guard useDirectProcessAPI else {
            return try await executeHostsCommand(
                id: id,
                command: ["cat", "/etc/hosts"],
                maximumStandardOutputBytes: Self.maximumManagedHostsBytes
            )
        }
        let startMarker = "__DEVCONTAINER_HOSTS_BEGIN__"
        let endMarker = "__DEVCONTAINER_HOSTS_END__"
        let script =
            "printf '\(startMarker)'; "
                + "base64 /etc/hosts | tr -d '\\n'; status=$?; "
                + "printf '\(endMarker)'; exit $status"
        let result = try await terminalCommand(
            ["exec", id, "/bin/sh", "-c", script],
            maximumStandardOutputBytes: Self.maximumManagedHostsEncodedOutputBytes
        )
        guard result.exitCode == 0 else {
            return result
        }
        guard let encoded = Self.framedPayload(
            in: result.standardOutput,
            startMarker: startMarker,
            endMarker: endMarker
        ),
            encoded.count <= Self.maximumManagedHostsBase64Bytes,
            let decoded = Data(base64Encoded: encoded),
            decoded.count <= Self.maximumManagedHostsBytes
        else {
            return AppleCommandResult(
                standardOutput: Data(),
                standardError: Data("Apple container returned an invalid hosts payload".utf8),
                exitCode: 255
            )
        }
        return AppleCommandResult(
            standardOutput: decoded,
            standardError: result.standardError,
            exitCode: result.exitCode
        )
    }

    private func uploadHostsWithExec(
        id: String,
        contents: Data
    ) async throws -> AppleCommandResult {
        guard useDirectProcessAPI else {
            return try await executeHostsCommand(
                id: id,
                command: ["/bin/sh", "-c", "cat > /etc/hosts"],
                input: contents
            )
        }
        let encoded = contents.base64EncodedString()
        return try await terminalCommand([
            "exec",
            id,
            "/bin/sh",
            "-c",
            "printf '%s' \"$1\" | base64 -d > /etc/hosts",
            "devcontainer-hosts",
            encoded
        ])
    }

    private func executeHostsCommand(
        id: String,
        command arguments: [String],
        input: Data? = nil,
        maximumStandardOutputBytes: Int? = 64 * 1024
    ) async throws -> AppleCommandResult {
        var commandArguments = ["exec"]
        if input != nil {
            commandArguments.append("--interactive")
        }
        commandArguments.append(id)
        commandArguments += arguments
        return try await command(
            commandArguments,
            input: input,
            maximumStandardOutputBytes: maximumStandardOutputBytes
        )
    }

    private func terminalCommand(
        _ arguments: [String],
        maximumStandardOutputBytes: Int? = 64 * 1024
    ) async throws -> AppleCommandResult {
        // Apple's `container exec` requires a controlling terminal for this
        // runtime-owned file on the stock distribution. macOS ships `script`,
        // which supplies that PTY while presenting finite pipes to our command
        // runner; framed payloads above discard its terminal control bytes.
        try await AppleCommandRunner.run(
            executable: URL(fileURLWithPath: "/usr/bin/script"),
            arguments: ["-q", "/dev/null", executable.path] + arguments,
            environment: environment,
            options: AppleCommandRunner.Options(
                maximumStandardOutputBytes: maximumStandardOutputBytes,
                maximumStandardErrorBytes: 64 * 1024
            )
        )
    }

    static func framedPayload(
        in data: Data,
        startMarker: String,
        endMarker: String
    ) -> Data? {
        guard let start = data.range(of: Data(startMarker.utf8)),
              let end = data.range(
                  of: Data(endMarker.utf8),
                  in: start.upperBound ..< data.endIndex
              )
        else {
            return nil
        }
        return data[start.upperBound ..< end.lowerBound]
    }

    static func validateManagedHostsFileSize(_ source: URL) throws {
        let sourceSize = try source.resourceValues(forKeys: [.fileSizeKey]).fileSize
        guard let sourceSize,
              sourceSize <= maximumManagedHostsBytes
        else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "container hosts file exceeds the \(maximumManagedHostsBytes)-byte safety limit"
            )
        }
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
        input: Data? = nil,
        maximumStandardOutputBytes: Int? = nil
    ) async throws -> AppleCommandResult {
        try await AppleCommandRunner.run(
            executable: executable,
            arguments: arguments,
            environment: environment,
            options: AppleCommandRunner.Options(
                input: input,
                maximumStandardOutputBytes: maximumStandardOutputBytes,
                maximumStandardErrorBytes: maximumStandardOutputBytes == nil
                    ? nil
                    : 64 * 1024
            )
        )
    }

    func process(_ arguments: [String]) throws -> AppleProcessSession {
        try AppleProcessSession(
            executable: executable,
            arguments: arguments,
            environment: environment
        )
    }

    func terminalProcess(
        _ arguments: [String]
    ) throws -> AppleTerminalProcessSession {
        try AppleTerminalProcessSession(
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
        return containerSnapshot(record)
    }

    func containerSnapshot(_ record: AppleContainerRecord) -> ContainerSnapshot {
        let wasStarted = startedContainers.contains(record.id)
            || startedContainers.contains(record.dockerID)
        let requestedContainer = requestedContainer(for: record)
        let requestedSpec = requestedContainer?.spec
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
            imageID: requestedContainer?.imageID,
            spec: record.spec,
            state: state,
            createdAt: record.createdAt,
            startedAt: record.startedAt ?? inferredStartedAt,
            finishedAt: record.finishedAt,
            exitCode: record.exitCode,
            networkAddresses: record.networkAddresses
        )
        if let requestedSpec {
            snapshot.spec = Self.effectiveContainerSpec(
                requested: requestedSpec,
                observed: snapshot.spec
            )
        }
        return snapshot
    }
}
