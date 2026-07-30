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
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleContainerRuntimeDirectTests {
    @Test
    func `direct inventory filters state labels and internal builders`() async throws {
        let fixture = try FakeAppleCLI()
        let inventory = FakeContainerInventory(
            snapshots: [
                nativeSnapshot(id: "running", labels: ["fixture": "yes"], status: .running),
                nativeSnapshot(id: "stopped", labels: ["fixture": "yes"], status: .stopped),
                nativeSnapshot(id: "mismatch", labels: ["fixture": "no"], status: .running),
                nativeSnapshot(
                    id: "builder",
                    labels: [
                        "com.apple.container.resource.role": "builder",
                        "com.apple.container.plugin": "builder",
                        "fixture": "yes"
                    ],
                    status: .running
                )
            ]
        )
        let runtime = try directRuntime(fixture: fixture, inventory: inventory)
        let context = RuntimeRequestContext()

        let running = try await runtime.listContainers(
            all: false,
            labels: ["fixture": "yes"],
            context: context
        )
        #expect(running.map(\.runtimeID.rawValue) == ["running"])
        #expect(running.first?.imageID == "sha256:abc123")

        let all = try await runtime.listContainers(
            all: true,
            labels: ["fixture": ""],
            context: context
        )
        #expect(
            Set(all.map(\.runtimeID.rawValue))
                == ["running", "stopped", "mismatch"]
        )
    }

    @Test
    func `direct inspect resolves exact identities and not found results`() async throws {
        let fixture = try FakeAppleCLI()
        let snapshot = nativeSnapshot(
            id: "fixture",
            labels: [AppleContainerRuntime.dockerIDLabel: "docker-fixture"],
            status: .running
        )
        let inventory = FakeContainerInventory(
            snapshots: [snapshot],
            returnFirstForUnknownID: true
        )
        let runtime = try directRuntime(fixture: fixture, inventory: inventory)
        let context = RuntimeRequestContext()

        let exact = try #require(
            try await runtime.inspectContainerDirect(id: "fixture", context: context)
        )
        #expect(exact.dockerID.rawValue == "docker-fixture")
        #expect(exact.imageID == "sha256:abc123")
        #expect(
            try await runtime.inspectContainerDirect(
                id: "unrelated",
                context: context
            )
                == nil
        )

        await inventory.setGetFailure(.notFound)
        #expect(
            try await runtime.inspectContainerDirect(
                id: "missing",
                context: context
            )
                == nil
        )
    }

    @Test
    func `direct inventory normalises client failures and honours deadlines`() async throws {
        let fixture = try FakeAppleCLI()
        let inventory = FakeContainerInventory(snapshots: [])
        let runtime = try directRuntime(fixture: fixture, inventory: inventory)
        await inventory.setListFailure(true)

        do {
            _ = try await runtime.listContainers(
                all: true,
                labels: [:],
                context: RuntimeRequestContext()
            )
            Issue.record("failing inventory unexpectedly succeeded")
        } catch let error as DevContainerError {
            #expect(error.code == .runtimeUnavailable)
        }

        let expired = RuntimeRequestContext(
            deadline: Date().addingTimeInterval(-1)
        )
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.listContainers(
                all: true,
                labels: [:],
                context: expired
            )
        }
    }

    @Test
    func `custom distributions retain enhanced inventory through the CLI schema`() async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        try fixture.setState("stopped")
        let inventory = FakeContainerInventory(
            snapshots: [
                nativeSnapshot(
                    id: "fixture",
                    labels: [:],
                    status: .stopped
                )
            ]
        )
        let networks = FakeNetworkClient()
        let runtime = try directRuntime(
            fixture: fixture,
            inventory: inventory,
            networks: networks
        )

        let snapshots = try await runtime.listContainers(
            all: true,
            labels: [:],
            context: RuntimeRequestContext()
        )
        let snapshot = try #require(snapshots.first)

        #expect(snapshot.spec.hostname == "fixture-host")
        #expect(snapshot.spec.privileged)
        #expect(snapshot.spec.networks == [
            NetworkAttachment(name: "bridge", aliases: ["workspace"])
        ])
        #expect(snapshot.spec.securityOptions.contains("no-new-privileges=true"))
        #expect(snapshot.spec.securityOptions.contains("systempaths=unconfined"))
        #expect(snapshot.exitCode == 17)
        #expect(await inventory.listCallCount() == 0)

        _ = try await runtime.listNetworks(context: RuntimeRequestContext())
        #expect(await networks.listCallCount() == 1)
    }

    @Test
    // The full restart sequence is kept together as one regression scenario.
    // swiftlint:disable:next function_body_length
    func `managed hosts cache is invalidated after container bootstrap`() async throws {
        let fixture = try FakeAppleCLI()
        let inventory = FakeContainerInventory(
            snapshots: [
                nativeSnapshot(
                    id: "fixture",
                    labels: [:],
                    status: .running
                )
            ]
        )
        let files = FakeContainerFileClient()
        let runtime = try directRuntime(
            fixture: fixture,
            inventory: inventory,
            files: files
        )
        let context = RuntimeRequestContext()
        let target = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "fixture"),
            dockerID: DockerID(rawValue: "fixture"),
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                networks: [NetworkAttachment(name: "direct-network")]
            ),
            state: .running,
            createdAt: Date(timeIntervalSince1970: 1),
            networkAddresses: ["direct-network": "192.0.2.2"]
        )

        try await runtime.synchronizeNetworkHosts(
            target: target,
            containers: [target],
            context: context
        )
        try await runtime.synchronizeNetworkHosts(
            target: target,
            containers: [target],
            context: context
        )
        #expect(await files.copyOutCallCount() == 1)
        #expect(await files.copyInCallCount() == 1)

        await files.resetHostsForBootstrap()
        try await runtime.startContainer(id: "fixture", context: context)
        try await runtime.synchronizeNetworkHosts(
            target: target,
            containers: [target],
            context: context
        )

        #expect(await files.copyOutCallCount() == 2)
        #expect(await files.copyInCallCount() == 2)
        #expect(
            await files.hosts().contains(
                "# BEGIN devcontainer managed network hosts"
            )
        )
    }

    @Test
    // Keep lifecycle and all translated failure operations in one matrix.
    // swiftlint:disable:next function_body_length
    func `direct network client handles lifecycle and failures`() async throws {
        let fixture = try FakeAppleCLI()
        let network = NetworkSnapshot(
            id: "network-id",
            spec: NetworkSpec(
                name: "fixture-network",
                labels: ["fixture": "yes"],
                driver: "container-network-vmnet",
                internalNetwork: true
            ),
            createdAt: Date(timeIntervalSince1970: 1)
        )
        let networks = FakeNetworkClient(snapshot: network)
        let runtime = try directRuntime(
            fixture: fixture,
            inventory: FakeContainerInventory(snapshots: []),
            networks: networks
        )
        let context = RuntimeRequestContext()

        #expect(try await runtime.listNetworks(context: context) == [network])
        #expect(
            try await runtime.inspectNetwork(id: network.id, context: context)
                == network
        )
        #expect(
            try await runtime.createNetwork(
                spec: network.spec,
                context: context
            ) == network
        )
        try await runtime.removeNetwork(id: network.id, context: context)
        #expect(await networks.deletedIDs() == [network.id])

        for operation in NetworkFailureOperation.allCases {
            await networks.setFailure(operation)
            await #expect(throws: DevContainerError.self) {
                switch operation {
                case .list:
                    _ = try await runtime.listNetworks(context: context)
                case .get:
                    _ = try await runtime.inspectNetwork(
                        id: network.id,
                        context: context
                    )
                case .create:
                    _ = try await runtime.createNetwork(
                        spec: network.spec,
                        context: context
                    )
                case .delete:
                    try await runtime.removeNetwork(
                        id: network.id,
                        context: context
                    )
                }
            }
        }
    }

    @Test
    // This is the conversion boundary's complete branch table.
    // swiftlint:disable:next function_body_length
    func `direct support maps Apple resources and client errors`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try directRuntime(
            fixture: fixture,
            inventory: FakeContainerInventory(snapshots: [])
        )
        let mappings: [(ContainerizationError.Code, DevContainerErrorCode)] = [
            (.invalidArgument, .invalidRequest),
            (.exists, .conflict),
            (.invalidState, .conflict),
            (.notFound, .notFound),
            (.cancelled, .cancelled),
            (.interrupted, .cancelled),
            (.timeout, .deadlineExceeded),
            (.unsupported, .unsupportedCapability),
            (.internalError, .runtimeUnavailable)
        ]
        for (source, expected) in mappings {
            let mapped = await runtime.directAPIError(
                ContainerizationError(source, message: "fixture"),
                operation: "test"
            )
            #expect(mapped.code == expected)
        }
        let original = DevContainerError(.notFound, message: "fixture")
        #expect(
            await runtime.directAPIError(original, operation: "test").code
                == .notFound
        )
        #expect(
            await runtime.directAPIError(
                DirectInventoryFailure.failed,
                operation: "test"
            ).code == .runtimeUnavailable
        )

        let mounts = [
            Filesystem(
                type: .virtiofs,
                source: "/source",
                destination: "/bind",
                options: ["ro"]
            ),
            Filesystem(
                type: .volume(
                    name: "volume",
                    format: "ext4",
                    cache: .on,
                    sync: .fsync
                ),
                source: "/volume",
                destination: "/volume",
                options: []
            ),
            Filesystem(
                type: .tmpfs,
                source: "tmpfs",
                destination: "/tmpfs",
                options: []
            ),
            Filesystem(
                type: .block(format: "ext4", cache: .on, sync: .fsync),
                source: "/block",
                destination: "/block",
                options: []
            )
        ]
        #expect(mounts.compactMap(AppleContainerRuntime.mount).map(\.type) == [
            .bind,
            .volume,
            .tmpfs
        ])
        #expect(
            AppleContainerRuntime.environmentDictionary([
                "A=first",
                "A=last",
                "EMPTY"
            ]) == ["A": "last", "EMPTY": ""]
        )
        #expect(
            AppleContainerRuntime.securityOptions([
                "initProcess": ["noNewPrivileges": true],
                "unconfinedSystemPaths": true
            ]) == [
                "no-new-privileges=true",
                "systempaths=unconfined"
            ]
        )
        let effective = AppleContainerRuntime.effectiveContainerSpec(
            requested: ContainerSpec(
                name: "requested",
                image: "fixture:latest",
                environment: ["REQUESTED": "yes"],
                labels: ["requested": "yes"]
            ),
            observed: ContainerSpec(
                name: "native",
                image: "fixture:latest",
                environment: ["PATH": "/usr/bin", "REQUESTED": "yes"],
                labels: ["native": "yes"],
                workingDirectory: "/workspace",
                user: "1000:1000",
                hostname: "native-host"
            )
        )
        #expect(effective.name == "requested")
        #expect(effective.environment == [
            "PATH": "/usr/bin",
            "REQUESTED": "yes"
        ])
        #expect(effective.workingDirectory == nil)
        #expect(effective.user == "1000:1000")
        #expect(effective.hostname == "native-host")
        #expect(effective.labels == [
            "native": "yes",
            "requested": "yes"
        ])
        let requestedOverrides = AppleContainerRuntime.effectiveContainerSpec(
            requested: ContainerSpec(
                name: "requested",
                image: "fixture:latest",
                environment: ["PATH": "/requested/bin"],
                workingDirectory: "/requested",
                user: "requested-user",
                hostname: "requested-host"
            ),
            observed: ContainerSpec(
                name: "native",
                image: "fixture:latest",
                environment: ["PATH": "/native/bin", "IMAGE_ONLY": "yes"],
                workingDirectory: "/native",
                user: "native-user",
                hostname: "native-host"
            )
        )
        #expect(requestedOverrides.environment == [
            "IMAGE_ONLY": "yes",
            "PATH": "/requested/bin"
        ])
        #expect(requestedOverrides.workingDirectory == "/requested")
        #expect(requestedOverrides.user == "requested-user")
        #expect(requestedOverrides.hostname == "requested-host")
        #expect(
            AppleContainerRuntime.filteredEnvironment([
                "HOME": "/fixture",
                "SECRET": "excluded"
            ]) == ["HOME": "/fixture"]
        )
        #expect(
            AppleContainerRuntime.containerState(
                "created",
                createdByThisEngine: false,
                wasStarted: false
            ) == .created
        )
        #expect(
            AppleContainerRuntime.containerState(
                "stopped",
                createdByThisEngine: true,
                wasStarted: false
            ) == .created
        )
        #expect(
            AppleContainerRuntime.containerState(
                "future",
                createdByThisEngine: false,
                wasStarted: false
            ) == .unknown
        )
        #expect(
            AppleContainerRuntime.networkAttachments([
                "networks": [
                    ["options": ["aliases": ["missing"]]],
                    [
                        "network": "bridge",
                        "options": ["aliases": ["fixture"]]
                    ]
                ]
            ]) == [
                NetworkAttachment(name: "bridge", aliases: ["fixture"])
            ]
        )
        #expect(AppleContainerRuntime.mount([:]) == nil)
        #expect(
            AppleContainerRuntime.mount([
                "destination": "/fixture",
                "type": ["volume": [:]]
            ])?.type == .volume
        )
        #expect(
            AppleContainerRuntime.mount([
                "destination": "/fixture",
                "type": ["tmpfs": [:]]
            ])?.type == .tmpfs
        )
        #expect(
            AppleContainerRuntime.mount([
                "destination": "/fixture",
                "type": ["unknown": true]
            ]) == nil
        )
        #expect(AppleContainerRuntime.port(["containerPort": "invalid"]) == nil)
        #expect(
            AppleContainerRuntime.networkAddresses([
                "networks": [
                    ["network": "missing"],
                    ["network": "bridge", "address": "192.0.2.2/24"]
                ]
            ]) == ["bridge": "192.0.2.2/24"]
        )
        #expect(AppleContainerRuntime.number(NSNumber(value: 42)) == 42)
        #expect(AppleContainerRuntime.number("43") == 43)
        #expect(AppleContainerRuntime.number(Date()) == nil)
        #expect(AppleContainerRuntime.user("developer") == "developer")
        #expect(
            AppleContainerRuntime.user([
                "raw": ["userString": "developer"]
            ]) == "developer"
        )
        #expect(
            AppleContainerRuntime.user([
                "id": ["uid": 501, "gid": 20]
            ]) == "501:20"
        )
        #expect(AppleContainerRuntime.user(["unexpected": true]) == nil)
        #expect(await runtime.imageSnapshot([:]) == nil)
        #expect(await runtime.networkSnapshot([:]) == nil)
        #expect(AppleContainerRuntime.date(false) == nil)
        #expect(
            AppleContainerRuntime.dockerFileTypeMode(S_IFBLK)
                == 1 << 26
        )
        #expect(
            AppleContainerRuntime.dockerFileTypeMode(S_IFIFO)
                == 1 << 25
        )
        #expect(
            AppleContainerRuntime.dockerFileTypeMode(S_IFSOCK)
                == 1 << 24
        )
        #expect(
            AppleContainerRuntime.dockerFileTypeMode(S_IFCHR)
                == (1 << 26) | (1 << 21)
        )
        #expect(
            AppleContainerRuntime.dockerFileTypeMode(0)
                == 1 << 19
        )
        #expect(throws: DevContainerError.self) {
            _ = try AppleContainerRuntime.archiveStat(
                url: fixture.root.appendingPathComponent("missing"),
                requestedName: "missing"
            )
        }
    }
}

private actor FakeContainerInventory: AppleContainerInventoryClient {
    private let snapshots: [ContainerResource.ContainerSnapshot]
    private let returnFirstForUnknownID: Bool
    private var listFails = false
    private var getFailure: DirectInventoryFailure?
    private var listCalls = 0

    init(
        snapshots: [ContainerResource.ContainerSnapshot],
        returnFirstForUnknownID: Bool = false
    ) {
        self.snapshots = snapshots
        self.returnFirstForUnknownID = returnFirstForUnknownID
    }

    func list() throws -> [ContainerResource.ContainerSnapshot] {
        listCalls += 1
        if listFails {
            throw DirectInventoryFailure.failed
        }
        return snapshots
    }

    func get(id: String) throws -> ContainerResource.ContainerSnapshot {
        if let getFailure {
            switch getFailure {
            case .failed:
                throw getFailure
            case .notFound:
                throw ContainerizationError(.notFound, message: id)
            }
        }
        if let match = snapshots.first(where: { $0.id == id }) {
            return match
        }
        if returnFirstForUnknownID, let first = snapshots.first {
            return first
        }
        throw ContainerizationError(.notFound, message: id)
    }

    func setListFailure(_ value: Bool) {
        listFails = value
    }

    func setGetFailure(_ value: DirectInventoryFailure?) {
        getFailure = value
    }

    func listCallCount() -> Int {
        listCalls
    }
}

private actor FakeNetworkClient: AppleNetworkClient {
    private let snapshot: NetworkSnapshot
    private var failure: NetworkFailureOperation?
    private var deleted: [String] = []
    private var listCalls = 0

    init(snapshot: NetworkSnapshot? = nil) {
        self.snapshot = snapshot ?? NetworkSnapshot(
            id: "unused",
            spec: NetworkSpec(name: "unused"),
            createdAt: Date(timeIntervalSince1970: 0)
        )
    }

    func list() throws -> [NetworkSnapshot] {
        listCalls += 1
        try check(.list)
        return [snapshot]
    }

    func get(id _: String) throws -> NetworkSnapshot {
        try check(.get)
        return snapshot
    }

    func create(spec _: NetworkSpec) throws -> NetworkSnapshot {
        try check(.create)
        return snapshot
    }

    func delete(id: String) throws {
        try check(.delete)
        deleted.append(id)
    }

    func setFailure(_ operation: NetworkFailureOperation?) {
        failure = operation
    }

    func deletedIDs() -> [String] {
        deleted
    }

    func listCallCount() -> Int {
        listCalls
    }

    private func check(_ operation: NetworkFailureOperation) throws {
        if failure == operation {
            throw DirectInventoryFailure.failed
        }
    }
}

private actor FakeContainerFileClient: AppleContainerFileClient {
    private var currentHosts = "127.0.0.1 localhost\n"
    private var copyOutCalls = 0
    private var copyInCalls = 0

    func copyIn(
        id _: String,
        source: String,
        destination _: String
    ) throws {
        copyInCalls += 1
        currentHosts = try String(contentsOfFile: source, encoding: .utf8)
    }

    func copyOut(
        id _: String,
        source _: String,
        destination: String
    ) throws {
        copyOutCalls += 1
        try Data(currentHosts.utf8).write(
            to: URL(fileURLWithPath: destination)
        )
    }

    func resetHostsForBootstrap() {
        currentHosts = "127.0.0.1 localhost\n"
    }

    func copyOutCallCount() -> Int {
        copyOutCalls
    }

    func copyInCallCount() -> Int {
        copyInCalls
    }

    func hosts() -> String {
        currentHosts
    }
}

private enum NetworkFailureOperation: CaseIterable {
    case list
    case get
    case create
    case delete
}

private enum DirectInventoryFailure: Error {
    case failed
    case notFound
}

private func directRuntime(
    fixture: FakeAppleCLI,
    inventory: any AppleContainerInventoryClient,
    files: any AppleContainerFileClient = FakeContainerFileClient(),
    networks: any AppleNetworkClient = FakeNetworkClient()
) throws -> AppleContainerRuntime {
    try AppleContainerRuntime(
        executable: fixture.executable,
        environment: [:],
        useDirectProcessAPI: false,
        useDirectContainerAPI: true,
        metadataStore: nil,
        volumeRoot: fixture.root.appendingPathComponent("volumes"),
        apiClient: ContainerClient(),
        inventoryClient: inventory,
        fileClient: files,
        networkClient: networks
    )
}

private func nativeSnapshot(
    id: String,
    labels: [String: String],
    status: RuntimeStatus
) -> ContainerResource.ContainerSnapshot {
    let image = ImageDescription(
        reference: "fixture:latest",
        descriptor: .init(
            mediaType: "application/vnd.oci.image.manifest.v1+json",
            digest: "sha256:" + String(repeating: "a", count: 64),
            size: 1
        )
    )
    let process = ProcessConfiguration(
        executable: "/bin/sh",
        arguments: ["-c", "sleep infinity"],
        environment: ["FIXTURE=yes"]
    )
    var configuration = ContainerConfiguration(
        id: id,
        image: image,
        process: process
    )
    configuration.labels = labels
    configuration.creationDate = Date(timeIntervalSince1970: 1)
    return ContainerResource.ContainerSnapshot(
        configuration: configuration,
        status: status,
        networks: [],
        startedDate: status == .running
            ? Date(timeIntervalSince1970: 2)
            : nil
    )
}
