// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

struct AppleContainerIOClosure: Sendable {
    let id: UUID
    let task: Task<Void, Never>
    var waiters = 0
}

extension AppleContainerRuntime {
    func launchContainerProcess(id: String, context: RuntimeRequestContext) async throws -> UUID? {
        try await requireCompletedCreation(id: id)
        try await requireRetainedOutputLogPolicy(id: id)
        let hostsConfiguration = try await managedHostsConfiguration(id: id)
        guard useDirectProcessAPI else {
            guard hostsConfiguration == nil else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "Managed hosts startup requires direct process APIs"
                )
            }
            try await requireSuccess(
                command(["start", id]),
                operation: "container start"
            )
            return nil
        }
        if let hostsConfiguration {
            try await populateManagedHostsBeforeProcess(
                configuration: hostsConfiguration, includeAllocatedSelf: false, context: context
            )
        }
        let snapshot = try await inspectContainer(id: id, context: context)
        if snapshot.state == .running {
            return containerExitRegistrations[id]
        }
        if let previousExit = containerExitTasks[id] {
            _ = try await previousExit.value
        }
        let channel = try await prepareContainerIO(snapshot: snapshot, context: context)
        let process = try await bootstrapContainerProcess(id: id, channel: channel)
        if let hostsConfiguration {
            // A failed preparation retains this created container and its owned
            // bootstrapped VM for retry/removal; never launch the user's process
            // with incomplete hosts or delete its root filesystem on failure.
            try await populateManagedHostsBeforeProcess(
                configuration: hostsConfiguration, includeAllocatedSelf: true, context: context
            )
        }
        try await startPreparedProcess(process, channel: channel)
        let registration = trackContainerProcess(process, id: id, channel: channel)
        do {
            try await confirmContainerProcess(id: id, channel: channel)
        } catch {
            await channel.fail(error)
            throw error
        }
        return registration
    }

    private func startPreparedProcess(_ process: any ClientProcess, channel: AppleContainerIO) async throws {
        do {
            try channel.reserveStart()
            try await process.start()
        } catch {
            await channel.fail(error)
            throw DevContainerError(
                .runtimeUnavailable,
                message: "Native process start failed; remove and recreate this container before retrying: \(error)"
            )
        }
    }

    private func confirmContainerProcess(id: String, channel: AppleContainerIO) async throws {
        let native = try await inventoryClient.get(id: id)
        guard native.configuration.creationDate == channel.createdAt, let startedAt = native.startedDate else {
            throw DevContainerError(.conflict, message: "Cannot verify the started container generation")
        }
        let inventory = inventoryClient
        let createdAt = channel.createdAt
        try await channel.didStart(at: startedAt) {
            let observed = try await inventory.get(id: id)
            guard observed.configuration.creationDate == createdAt, observed.startedDate == startedAt,
                  observed.status == .running
            else {
                throw DevContainerError(.conflict, message: "Container process generation changed")
            }
        }
    }

    private func bootstrapContainerProcess(id: String, channel: AppleContainerIO) async throws -> any ClientProcess {
        // Retain descriptors and the prepared VM across a host-preparation retry.
        if let process = channel.boundProcess {
            return process
        }
        do {
            let process = try await bootstrapClient.bootstrap(id: id, stdio: channel.bootstrapHandles())
            try await channel.bind(process)
            return process
        } catch {
            await channel.fail(error)
            throw error
        }
    }

    private func trackContainerProcess(_ process: any ClientProcess, id: String, channel: AppleContainerIO) -> UUID {
        let task = Task {
            do {
                let code = try await process.wait()
                let exit = ContainerExit(code: code, finishedAt: Date())
                await channel.finish(exitCode: code)
                return exit
            } catch {
                await channel.fail(error)
                throw error
            }
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
        return registration
    }

    public func attachContainer(
        id: String,
        terminal: Bool,
        context: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession {
        guard useDirectProcessAPI else {
            return ApplePollingLogSession {
                try await self.pollLogs(id: id, context: context)
            }
        }
        let snapshot = try await inspectContainer(id: id, context: context)
        try await requireCompletedCreation(id: snapshot.runtimeID.rawValue)
        guard snapshot.spec.terminal == terminal else {
            throw DevContainerError(.invalidRequest, message: "Attachment terminal mode does not match the container")
        }
        return try await prepareContainerIO(snapshot: snapshot, context: context).attach()
    }

    public func resizeContainer(
        id: String, width: UInt16, height: UInt16, context: RuntimeRequestContext
    ) async throws {
        let snapshot = try await inspectContainer(id: id, context: context)
        try await requireCompletedCreation(id: snapshot.runtimeID.rawValue)
        guard snapshot.state == .running, snapshot.spec.terminal,
              let channel = containerIO[snapshot.runtimeID.rawValue],
              channel.createdAt == snapshot.createdAt, channel.ownsProcess(startedAt: snapshot.startedAt)
        else {
            throw DevContainerError(.conflict, message: "Container has no owned running terminal")
        }
        // The broker validates the native generation again and joins admitted
        // control work before allowing a replacement init to claim this ID.
        try await channel.resize(width: width, height: height)
    }

    public var supportsContainerExitWaitRegistration: Bool {
        useDirectProcessAPI
    }

    public func prepareContainerExitWait(
        id: String, context: RuntimeRequestContext
    ) async throws -> any RuntimeContainerExitWait {
        guard useDirectProcessAPI else {
            throw DevContainerError(.unsupportedCapability, message: "Exit registration requires direct process APIs")
        }
        let initial = try await inspectContainer(id: id, context: context)
        while true {
            try context.checkActive()
            let snapshot = try await inspectContainer(id: initial.dockerID.rawValue, context: context)
            guard snapshot.createdAt == initial.createdAt, snapshot.runtimeID == initial.runtimeID else {
                throw DevContainerError(.conflict, message: "Container changed during exit registration")
            }
            try await requireCompletedCreation(id: snapshot.runtimeID.rawValue)
            let channel = try await prepareContainerIO(snapshot: snapshot, context: context)
            if let waiter = channel.prepareExitWait(snapshot: snapshot) {
                return waiter
            }
            // Native exit can precede output drainage. Join the old generation
            // before fresh registration without cancelling its wait or readers.
            if let previous = containerExitTasks[snapshot.runtimeID.rawValue] {
                _ = try await previous.value
            } else if !channel.hasExited {
                throw DevContainerError(.conflict, message: "Container exit authority is unavailable")
            }
        }
    }

    func prepareContainerIO(
        snapshot: ContainerSnapshot, context: RuntimeRequestContext
    ) async throws -> AppleContainerIO {
        let id = snapshot.runtimeID.rawValue
        if containerIOClosures[id] != nil {
            await joinContainerIOClosures(id: id)
            let refreshed = try await inspectContainer(id: id, context: context)
            guard refreshed.createdAt == snapshot.createdAt, refreshed.dockerID == snapshot.dockerID,
                  refreshed.spec.terminal == snapshot.spec.terminal
            else {
                throw DevContainerError(.conflict, message: "Resolved container changed during I/O cleanup")
            }
            try await requireCompletedCreation(id: id)
            return try await prepareContainerIO(snapshot: refreshed, context: context)
        }
        if snapshot.state == .running,
           containerIO[id]?.ownsProcess(startedAt: snapshot.startedAt) != true
        {
            throw DevContainerError(
                .unsupportedCapability, message: "Live attachment requires a verified process owned by this engine"
            )
        }
        if let channel = containerIO[id] {
            let sameIncarnation = Self.sameContainerIncarnation(
                metadataCreatedAt: channel.createdAt, observedCreatedAt: snapshot.createdAt
            )
            if sameIncarnation, !channel.hasExited {
                try await channel.prepareOutputCapture()
                return channel
            }
            containerIO.removeValue(forKey: id)
            scheduleContainerIOClosure(id: id, channel: channel)
            return try await prepareContainerIO(snapshot: snapshot, context: context)
        }
        guard snapshot.state != .running else {
            throw DevContainerError(.conflict, message: "Container init descriptors belong to another generation")
        }
        let capture: (@Sendable () async throws -> any RuntimeContainerOutputJournal)? = if let store =
            metadataStore as? any RuntimeContainerOutputStore
        {
            { try await store.beginContainerOutputCapture(snapshot: snapshot) }
        } else {
            nil
        }
        let channel = try AppleContainerIO(
            createdAt: snapshot.createdAt, terminal: snapshot.spec.terminal,
            openStandardInput: snapshot.spec.openStandardInput, outputCapture: capture
        )
        // Reentrant attach/start calls join this exact preparation.
        containerIO[id] = channel
        try await channel.prepareOutputCapture()
        return channel
    }

    func scheduleContainerIOClosure(id: String, channel: AppleContainerIO) {
        channel.cancel()
        let previous = containerIOClosures[id]?.task
        let task = Task {
            await previous?.value
            await channel.shutdown()
        }
        let registration = UUID()
        containerIOClosures[id] = AppleContainerIOClosure(id: registration, task: task)
        Task { [weak self] in
            await task.value
            await self?.finishContainerIOClosure(id: id, registration: registration)
        }
    }

    private func joinContainerIOClosures(id: String) async {
        while let closing = containerIOClosures[id] {
            containerIOClosures[id]?.waiters += 1
            await closing.task.value
            finishContainerIOClosure(id: id, registration: closing.id)
        }
    }

    private func finishContainerIOClosure(id: String, registration: UUID) {
        if containerIOClosures[id]?.id == registration {
            containerIOClosures.removeValue(forKey: id)
        }
    }
}
