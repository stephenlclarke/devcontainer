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

import ArgumentParser
import ContainerEngineRuntimeSPI
import Darwin
import DevContainerAppleRuntime
import DevContainerCore
import DevContainerDockerAPI
import DevContainerModel
import DevContainerState
import Dispatch
import Foundation
import Logging

@main
struct DevContainerServiceCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "devcontainer-engine",
        abstract: "Docker Engine compatibility service for Apple container"
    )

    @Option(name: .long, help: "User-owned Unix socket path.")
    var socket: String = DefaultPaths.socket

    @Option(name: .long, help: "Apple container CLI path.")
    var container: String = DefaultPaths.containerExecutable

    @Option(name: .long, help: "Crash-recovery SQLite database path.")
    var state: String = DefaultPaths.stateDatabase

    @Option(name: .long, help: "Backend provider recorded for resource ownership.")
    var provider: String = BackendProvider.stock.rawValue

    // Service bootstrap remains linear so ownership and rollback order are
    // reviewable in one place.
    // swiftlint:disable:next function_body_length
    mutating func run() async throws {
        var logger = Logger(label: "devcontainer-engine")
        logger.logLevel = .info

        let stateURL = URL(fileURLWithPath: state)
        let store = try SQLiteStateStore(path: stateURL)
        let retention = try await store.pruneRetainedState()
        logger.info(
            "State retention completed",
            metadata: [
                "deleted-events": .stringConvertible(retention.deletedEvents),
                "deleted-operations": .stringConvertible(retention.deletedOperations),
                "retained-events": .stringConvertible(retention.retainedEvents),
                "retained-operations": .stringConvertible(retention.retainedOperations)
            ]
        )
        let coordinator = ProjectCoordinator(store: store)
        guard let selectedProvider = BackendProvider(rawValue: provider) else {
            throw ValidationError(
                "provider must be \(BackendProvider.stock.rawValue) or "
                    + BackendProvider.containerCompose.rawValue
            )
        }
        let recovery = try await coordinator
            .failUnfinishedOperationsForManualRecovery()
        if !recovery.isEmpty {
            logger.warning(
                "Unfinished mutations require manual recovery",
                metadata: ["operations": .stringConvertible(recovery.count)]
            )
        }
        let runtime = try AppleContainerRuntime(
            executable: URL(fileURLWithPath: container),
            metadataStore: store,
            volumeRoot: stateURL.deletingLastPathComponent()
                .appendingPathComponent("volumes", isDirectory: true)
        )
        let runtimeDescriptor = try await runtime.descriptor(
            context: RuntimeRequestContext(
                deadline: Date().addingTimeInterval(5 * 60)
            )
        )
        let providerDeclaration = try ContainerEngineProviderDeclaration(
            profile: .stock,
            kind: .devcontainerStock,
            implementationVersion: BuildInfo.current.version,
            runtimeRevisions: [
                "apple-container": runtimeDescriptor.providerVersion,
                "apple-container-commit": runtimeDescriptor.providerCommit,
                "devcontainer": BuildInfo.current.commit,
                "resource-owner": selectedProvider.rawValue
            ],
            stateSchemaVersion: UInt64(SQLiteStateStore.schemaVersion),
            capabilities: runtimeDescriptor.capabilities.map { capability, status in
                let sharedStatus: ContainerEngineCapabilityStatus = switch status {
                case .native:
                    .native
                case .emulated:
                    .emulated
                case .unsupported:
                    .unavailable
                }
                return try ContainerEngineProviderCapability(
                    identifier: "engine.\(capability.rawValue)",
                    status: sharedStatus
                )
            }
        )
        let providerFingerprint = try ContainerEngineProviderSelectionStore(
            path: stateURL.deletingLastPathComponent()
                .appendingPathComponent("engine-provider.json")
        ).select(providerDeclaration)
        logger.info(
            "Engine provider selected",
            metadata: [
                "fingerprint": .string(providerFingerprint.digest),
                "profile": .string(providerDeclaration.profile.rawValue)
            ]
        )
        try await runtime.restorePortForwarding(
            context: RuntimeRequestContext(
                deadline: Date().addingTimeInterval(5 * 60)
            )
        )
        let router = DockerRouter(
            runtime: runtime,
            coordinator: coordinator,
            provider: selectedProvider,
            providerFingerprint: providerFingerprint.digest
        )
        let server = EngineServer(router: router, socketPath: socket, logger: logger)
        try await server.start()
        let signals = Self.terminationSignals()
        try await withThrowingTaskGroup(of: ServiceCompletion.self) { group in
            group.addTask {
                try await server.wait()
                return .serverClosed
            }
            group.addTask {
                for await signalNumber in signals {
                    return .signal(signalNumber)
                }
                return .signal(SIGTERM)
            }

            if case let .signal(signalNumber) = try await group.next() {
                logger.info(
                    "Engine shutdown requested",
                    metadata: ["signal": .stringConvertible(signalNumber)]
                )
            }
            await runtime.shutdown()
            try await server.shutdown()
            group.cancelAll()
            while try await group.next() != nil {
                // Drain cancelled child tasks before leaving the structured scope.
            }
        }
    }

    private static func terminationSignals() -> AsyncStream<Int32> {
        Darwin.signal(SIGINT, SIG_IGN)
        Darwin.signal(SIGTERM, SIG_IGN)

        return AsyncStream { continuation in
            let interrupt = DispatchSource.makeSignalSource(
                signal: SIGINT,
                queue: DispatchQueue.global()
            )
            let terminate = DispatchSource.makeSignalSource(
                signal: SIGTERM,
                queue: DispatchQueue.global()
            )
            interrupt.setEventHandler {
                continuation.yield(SIGINT)
                continuation.finish()
            }
            terminate.setEventHandler {
                continuation.yield(SIGTERM)
                continuation.finish()
            }
            continuation.onTermination = { _ in
                interrupt.cancel()
                terminate.cancel()
            }
            interrupt.resume()
            terminate.resume()
        }
    }
}

private enum ServiceCompletion: Sendable {
    case serverClosed
    case signal(Int32)
}

enum DefaultPaths {
    static var socket: String {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("docker.sock")
            .path
    }

    static var stateDatabase: String {
        let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return applicationSupport
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("state.sqlite")
            .path
    }

    static var containerExecutable: String {
        if let configured = ProcessInfo.processInfo.environment["DEVCONTAINER_CONTAINER_BIN"] {
            return configured
        }
        for candidate in [
            "/usr/local/bin/container",
            "/opt/homebrew/bin/container",
            "/usr/bin/container"
        ] where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return "/usr/local/bin/container"
    }
}
