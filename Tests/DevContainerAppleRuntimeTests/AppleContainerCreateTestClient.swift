// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import Containerization
import ContainerizationError
import ContainerizationOS
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension AppleContainerCreateTests {
    actor Creator: AppleContainerCreateClient, AppleContainerInventoryClient, AppleContainerBootstrapClient,
        ClientProcess
    {
        nonisolated let id = "fixture"
        var prepared: [ContainerConfiguration] = []
        var created: [ContainerConfiguration] = []
        var runningPeers: [ContainerResource.ContainerSnapshot] = []
        let failCreate: Bool
        let failAfterCreate: Bool
        let replacement: Bool
        var starts = 0
        var bootstraps = 0
        var hostsAtBootstrap: String?
        var hostsAtStart: String?
        var startedAt: Date?
        var running = false

        init(
            failCreate: Bool = false, failAfterCreate: Bool = false, replacement: Bool = false
        ) {
            self.failCreate = failCreate
            self.failAfterCreate = failAfterCreate
            self.replacement = replacement
        }

        func prepare(
            spec: ContainerSpec, image: ResolvedAppleImage, context: RuntimeRequestContext
        ) throws -> ContainerConfiguration {
            try context.checkActive()
            let (description, platform) = try image.nativeIdentity()
            let configuration = try AppleContainerCreateProjection.configuration(
                spec: spec, identity: (description, platform), imageConfig: nil,
                system: .init(), builtinNetwork: "default"
            )
            prepared.append(configuration)
            return configuration
        }

        func create(
            configuration: ContainerConfiguration, mountOptions _: [String], context: RuntimeRequestContext,
            recordIntent: @Sendable (ContainerConfiguration) async throws -> RuntimeContainerCreation
        ) async throws -> RuntimeContainerCreation {
            try context.checkActive()
            let creation = try await recordIntent(configuration)
            guard !failCreate else {
                throw ContainerizationError(.notFound, message: "Captured image content missing")
            }
            created.append(configuration)
            if failAfterCreate {
                throw DevContainerError(
                    .providerProtocolMismatch,
                    message: "Injected post-create verification failure"
                )
            }
            return creation
        }

        func list() -> [ContainerResource.ContainerSnapshot] {
            created.map {
                .init(
                    configuration: $0,
                    status: running ? .running : .stopped,
                    networks: [],
                    startedDate: startedAt
                )
            } + runningPeers
        }

        func addRunningPeer(configuration: ContainerConfiguration, attachment: ContainerResource.Attachment) {
            runningPeers.append(.init(
                configuration: configuration, status: .running, networks: [attachment], startedDate: Date()
            ))
        }

        func get(id _: String) throws -> ContainerResource.ContainerSnapshot {
            guard var configuration = created.last else {
                throw ContainerizationError(.notFound, message: "No fake native container")
            }
            if replacement {
                configuration.creationDate = configuration.creationDate.addingTimeInterval(1)
            }
            return .init(
                configuration: configuration, status: running ? .running : .stopped,
                networks: [], startedDate: startedAt
            )
        }

        func bootstrap(id _: String, stdio _: [FileHandle?]) throws -> any ClientProcess {
            bootstraps += 1
            hostsAtBootstrap = try mountedHosts()
            return self
        }

        func start() throws {
            hostsAtStart = try mountedHosts()
            starts += 1
            startedAt = Date()
            running = true
        }

        private func mountedHosts() throws -> String? {
            guard let mount = created.last?.mounts.first(where: { $0.destination == "/etc/hosts" }) else {
                return nil
            }
            return try String(contentsOfFile: mount.source, encoding: .utf8)
        }

        func recordPriorStart() {
            startedAt = Date(timeIntervalSince1970: 100)
        }

        func recordExit() {
            running = false
        }

        func replaceIncarnation() {
            created[created.count - 1].creationDate.addTimeInterval(10)
            startedAt = nil
            running = false
        }

        func wait() async throws -> Int32 {
            // This lifecycle fixture stays running until engine shutdown cancels
            // its wait; a transport error must not impersonate a live process.
            try await Task.sleep(for: .seconds(3600))
            return 0
        }

        func resize(_: Terminal.Size) {
            // Headless lifecycle tests never request terminal resizing.
        }

        func kill(_: Int32) {
            // These tests assert CLI stop is not issued for a created VM.
        }

        nonisolated func disconnect() {
            // This in-memory lifecycle fixture owns no transport connection.
        }
    }
}
