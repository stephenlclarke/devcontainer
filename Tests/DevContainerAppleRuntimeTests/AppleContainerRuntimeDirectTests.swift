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

import ContainerizationError
import ContainerizationExtras
import ContainerizationOCI
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

extension AppleContainerRuntimeTests {
    @Test
    func `direct Apple snapshot maps without a CLI inventory process`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let createdAt = Date(timeIntervalSince1970: 1_800_000_000)
        let configuration = try directContainerConfiguration(
            fixture: fixture,
            createdAt: createdAt
        )
        let record = try await runtime.containerRecord(
            ContainerResource.ContainerSnapshot(
                configuration: configuration,
                status: .running,
                networks: directNetworkAttachments(),
                startedDate: createdAt.addingTimeInterval(2)
            )
        )
        let snapshot = await runtime.containerSnapshot(record)

        assertDirectSnapshot(snapshot, createdAt: createdAt)
        #expect(!FileManager.default.fileExists(atPath: fixture.logURL.path))
    }

    @Test
    func `direct resource clients avoid CLI inventory and repeated hosts copies`() async throws {
        let fixture = try FakeAppleCLI()
        let resourceClient = try DirectContainerClientFake(
            snapshots: [directContainerSnapshot(fixture: fixture)]
        )
        let networkClient = DirectNetworkClientFake()
        let store = TestMetadataStore()
        await store.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: RuntimeID(rawValue: "direct-fixture"),
                dockerID: DockerID(rawValue: "docker-direct-fixture"),
                spec: ContainerSpec(
                    name: "direct-fixture",
                    image: "fixture:latest",
                    networks: [NetworkAttachment(name: "direct-network")]
                ),
                createdAt: Date(timeIntervalSince1970: 1_800_000_000)
            )
        )
        let runtime = try directRuntime(
            fixture: fixture,
            resourceClient: resourceClient,
            networkClient: networkClient,
            metadataStore: store
        )
        let context = RuntimeRequestContext()

        try await assertDirectInventory(
            runtime: runtime,
            resourceClient: resourceClient,
            context: context
        )
        try await assertDirectResourceCopies(
            runtime: runtime,
            resourceClient: resourceClient
        )
        try await assertHostsSynchronizationCache(
            runtime: runtime,
            resourceClient: resourceClient,
            context: context
        )
        #expect(!FileManager.default.fileExists(atPath: fixture.logURL.path))
    }

    private func assertDirectInventory(
        runtime: AppleContainerRuntime,
        resourceClient: DirectContainerClientFake,
        context: RuntimeRequestContext
    ) async throws {
        #expect(
            try await runtime.listContainers(
                all: true,
                labels: ["fixture": "yes"],
                context: context
            ).map(\.runtimeID.rawValue) == ["direct-fixture"]
        )
        #expect(
            try await runtime.inspectContainer(
                id: "docker-direct-fixture",
                context: context
            ).runtimeID.rawValue == "direct-fixture"
        )
        #expect(await resourceClient.listCount == 1)
        #expect(await resourceClient.getIDs == ["direct-fixture"])
        #expect(
            try await runtime.listContainers(
                all: true,
                labels: ["missing": ""],
                context: context
            ).isEmpty
        )
        #expect(
            try await runtime.listContainers(
                all: true,
                labels: ["fixture": "wrong"],
                context: context
            ).isEmpty
        )
        #expect(try await runtime.inspectContainerDirect(id: "missing") == nil)
        await resourceClient.returnFirstSnapshotForUnknownID()
        #expect(try await runtime.inspectContainerDirect(id: "alias") == nil)
    }

    private func assertDirectResourceCopies(
        runtime: AppleContainerRuntime,
        resourceClient: DirectContainerClientFake
    ) async throws {
        let initialCopyOutCount = await resourceClient.copyOutCount
        let initialCopyInCount = await resourceClient.copyInCount
        let temporary = try TemporaryDirectory()
        defer { temporary.remove() }
        let output = temporary.url.appendingPathComponent("output")
        try await runtime.copyContainerResourceOut(
            id: "direct-fixture",
            source: "/fixture",
            destination: output.path,
            operation: "test copy-out"
        )
        try await runtime.copyContainerResourceIn(
            id: "direct-fixture",
            source: output.path,
            destination: "/fixture",
            operation: "test copy-in"
        )
        #expect(await resourceClient.copyOutCount == initialCopyOutCount + 1)
        #expect(await resourceClient.copyInCount == initialCopyInCount + 1)
    }

    @Test
    func `direct network client maps resources and errors`() async throws {
        let fixture = try FakeAppleCLI()
        let resourceClient = DirectContainerClientFake(snapshots: [])
        let networkClient = DirectNetworkClientFake()
        let runtime = try directRuntime(
            fixture: fixture,
            resourceClient: resourceClient,
            networkClient: networkClient
        )
        let context = RuntimeRequestContext()
        let created = try await runtime.createNetwork(
            spec: NetworkSpec(
                name: "direct-network",
                labels: ["fixture": "yes"],
                internalNetwork: true
            ),
            context: context
        )

        #expect(created.spec.internalNetwork)
        #expect(created.spec.labels == ["fixture": "yes"])
        #expect(try await runtime.listNetworks(context: context) == [created])
        #expect(
            try await runtime.inspectNetwork(
                id: "direct-network",
                context: context
            ) == created
        )
        try await runtime.removeNetwork(id: "direct-network", context: context)
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectNetwork(
                id: "direct-network",
                context: context
            )
        }
        await networkClient.failNextList()
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.listNetworks(context: context)
        }
        await networkClient.failNextCreate()
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.createNetwork(
                spec: NetworkSpec(name: "failed-network"),
                context: context
            )
        }
        #expect(!FileManager.default.fileExists(atPath: fixture.logURL.path))
    }

    @Test
    func `direct errors and event wakeups preserve runtime semantics`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
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
        let existing = DevContainerError(.conflict, message: "fixture")
        #expect(
            await runtime.directAPIError(
                existing,
                operation: "test"
            ).code == .conflict
        )
        #expect(
            await runtime.directAPIError(
                CocoaError(.fileNoSuchFile),
                operation: "test"
            ).code == .runtimeUnavailable
        )

        await runtime.signalEventPollers()

        assertInvalidDirectRuntime(fixture: fixture)
    }

    private func assertInvalidDirectRuntime(fixture: FakeAppleCLI) {
        let resourceClient = DirectContainerClientFake(snapshots: [])
        let networkClient = DirectNetworkClientFake()
        #expect(throws: DevContainerError.self) {
            _ = try AppleContainerRuntime(
                executable: fixture.root.appendingPathComponent("missing"),
                resourceClient: resourceClient,
                networkClient: networkClient
            )
        }
    }

    private func directRuntime(
        fixture: FakeAppleCLI,
        resourceClient: any AppleContainerResourceClient,
        networkClient: any AppleNetworkResourceClient,
        metadataStore: (any RuntimeMetadataStore)? = nil
    ) throws -> AppleContainerRuntime {
        try AppleContainerRuntime(
            executable: fixture.executable,
            environment: [:],
            metadataStore: metadataStore,
            volumeRoot: fixture.root.appendingPathComponent("volumes"),
            resourceClient: resourceClient,
            networkClient: networkClient
        )
    }

    private func directContainerConfiguration(
        fixture: FakeAppleCLI,
        createdAt: Date
    ) throws -> ContainerConfiguration {
        var configuration = ContainerConfiguration(
            id: "direct-fixture",
            image: ImageDescription(
                reference: "fixture:latest",
                descriptor: Descriptor(
                    mediaType: "application/vnd.oci.image.manifest.v1+json",
                    digest: "sha256:fixture",
                    size: 1
                )
            ),
            process: ProcessConfiguration(
                executable: "/bin/sleep",
                arguments: ["infinity"],
                environment: ["A=1"],
                workingDirectory: "/workspace",
                terminal: true,
                user: .id(uid: 501, gid: 20)
            )
        )
        configuration.labels = [
            "fixture": "yes",
            AppleContainerRuntime.dockerIDLabel: "docker-direct-fixture"
        ]
        configuration.mounts = directFilesystems(fixture: fixture)
        configuration.networks = [
            AttachmentConfiguration(
                network: "direct-network",
                options: AttachmentOptions(hostname: "direct-fixture")
            )
        ]
        configuration.publishedPorts = try directPublishedPorts()
        configuration.useInit = true
        configuration.capAdd = ["CAP_SYS_PTRACE"]
        configuration.capDrop = ["CAP_NET_RAW"]
        configuration.creationDate = createdAt
        return configuration
    }

    private func directFilesystems(fixture: FakeAppleCLI) -> [Filesystem] {
        [
            .virtiofs(
                source: fixture.root.path,
                destination: "/workspace",
                options: ["ro"]
            ),
            .tmpfs(destination: "/run", options: []),
            .volume(
                name: "fixture-volume",
                format: "ext4",
                source: fixture.root.path,
                destination: "/volume",
                options: []
            ),
            .block(
                format: "ext4",
                source: fixture.root.path,
                destination: "/block",
                options: []
            )
        ]
    }

    private func directNetworkAttachments() throws
        -> [ContainerResource.Attachment]
    {
        try [
            ContainerResource.Attachment(
                network: "direct-network",
                hostname: "direct-fixture",
                ipv4Address: CIDRv4("192.0.2.2/24"),
                ipv4Gateway: IPv4Address("192.0.2.1"),
                ipv6Address: nil,
                macAddress: nil
            )
        ]
    }

    private func directPublishedPorts() throws -> [PublishPort] {
        try [
            PublishPort(
                hostAddress: IPAddress("127.0.0.1"),
                hostPort: 18080,
                containerPort: 8080,
                proto: .tcp,
                count: 2
            )
        ]
    }

    private func directContainerSnapshot(
        fixture: FakeAppleCLI
    ) throws -> ContainerResource.ContainerSnapshot {
        let configuration = try directContainerConfiguration(
            fixture: fixture,
            createdAt: Date(timeIntervalSince1970: 1_800_000_000)
        )
        return try ContainerResource.ContainerSnapshot(
            configuration: configuration,
            status: .running,
            networks: directNetworkAttachments(),
            startedDate: configuration.creationDate.addingTimeInterval(1)
        )
    }

    private func assertDirectSnapshot(
        _ snapshot: DevContainerModel.ContainerSnapshot,
        createdAt: Date
    ) {
        #expect(snapshot.runtimeID.rawValue == "direct-fixture")
        #expect(snapshot.dockerID.rawValue == "docker-direct-fixture")
        #expect(snapshot.spec.image == "fixture:latest")
        #expect(snapshot.spec.command == ["/bin/sleep", "infinity"])
        #expect(snapshot.spec.environment == ["A": "1"])
        #expect(snapshot.spec.workingDirectory == "/workspace")
        #expect(snapshot.spec.user == "501:20")
        #expect(snapshot.spec.terminal)
        #expect(snapshot.spec.mounts.map(\.type) == [.bind, .tmpfs, .volume])
        #expect(snapshot.spec.mounts.first?.readOnly == true)
        #expect(snapshot.spec.networks == [NetworkAttachment(name: "direct-network")])
        #expect(snapshot.networkAddresses == ["direct-network": "192.0.2.2"])
        #expect(
            snapshot.spec.ports == [
                PortBinding(containerPort: 8080, hostPort: 18080),
                PortBinding(containerPort: 8081, hostPort: 18081)
            ]
        )
        #expect(snapshot.spec.initProcess)
        #expect(snapshot.spec.capabilitiesToAdd == ["CAP_SYS_PTRACE"])
        #expect(snapshot.spec.capabilitiesToDrop == ["CAP_NET_RAW"])
        #expect(snapshot.state == .running)
        #expect(snapshot.createdAt == createdAt)
        #expect(snapshot.startedAt == createdAt.addingTimeInterval(2))
    }

    private func assertHostsSynchronizationCache(
        runtime: AppleContainerRuntime,
        resourceClient: DirectContainerClientFake,
        context: RuntimeRequestContext
    ) async throws {
        let initialCopyOutCount = await resourceClient.copyOutCount
        let initialCopyInCount = await resourceClient.copyInCount
        try await runtime.synchronizeNetworkHosts(context: context)
        try await runtime.synchronizeNetworkHosts(context: context)
        #expect(await resourceClient.copyOutCount == initialCopyOutCount + 1)
        #expect(await resourceClient.copyInCount == initialCopyInCount + 1)
        #expect(
            await resourceClient.uploadedHosts.contains(
                "# BEGIN devcontainer managed network hosts"
            )
        )

        await resourceClient.setStatus(.stopped)
        try await runtime.synchronizeNetworkHosts(context: context)
        await resourceClient.setStatus(.running)
        try await runtime.synchronizeNetworkHosts(context: context)
        #expect(await resourceClient.copyOutCount == initialCopyOutCount + 1)

        await resourceClient.replaceIncarnation(
            Date(timeIntervalSince1970: 1_800_000_001)
        )
        try await runtime.synchronizeNetworkHosts(context: context)
        #expect(await resourceClient.copyOutCount == initialCopyOutCount + 2)

        await resourceClient.replaceIncarnation(
            Date(timeIntervalSince1970: 1_800_000_002)
        )
        await resourceClient.failNextCopyOutAndStop()
        try await runtime.synchronizeNetworkHosts(context: context)
        #expect(await resourceClient.copyOutCount == initialCopyOutCount + 3)
    }
}

private actor DirectContainerClientFake: AppleContainerResourceClient {
    private var snapshots: [ContainerResource.ContainerSnapshot]
    private(set) var listCount = 0
    private(set) var getIDs: [String] = []
    private(set) var copyOutCount = 0
    private(set) var copyInCount = 0
    private(set) var uploadedHosts = ""
    private var returnsFirstSnapshotForUnknownID = false
    private var copyOutShouldFailAndStop = false

    init(snapshots: [ContainerResource.ContainerSnapshot]) {
        self.snapshots = snapshots
    }

    func list(
        filters _: ContainerListFilters
    ) -> [ContainerResource.ContainerSnapshot] {
        listCount += 1
        return snapshots
    }

    func get(id: String) throws -> ContainerResource.ContainerSnapshot {
        getIDs.append(id)
        guard
            let snapshot = snapshots.first(where: { $0.id == id })
            ?? (returnsFirstSnapshotForUnknownID ? snapshots.first : nil)
        else {
            throw ContainerizationError(
                .notFound,
                message: "container \(id) was not found"
            )
        }
        return snapshot
    }

    func returnFirstSnapshotForUnknownID() {
        returnsFirstSnapshotForUnknownID = true
    }

    func copyIn(
        id _: String,
        source: String,
        destination _: String,
        mode _: UInt32,
        createParents _: Bool
    ) throws {
        copyInCount += 1
        uploadedHosts = try String(contentsOfFile: source, encoding: .utf8)
    }

    func copyOut(
        id _: String,
        source: String,
        destination: String,
        createParents _: Bool
    ) throws {
        copyOutCount += 1
        if copyOutShouldFailAndStop {
            copyOutShouldFailAndStop = false
            snapshots[0].status = .stopped
            throw ContainerizationError(
                .internalError,
                message: "injected hosts copy failure"
            )
        }
        let content =
            source == "/etc/hosts"
                ? "127.0.0.1 localhost\n"
                : "direct-resource-copy\n"
        try Data(content.utf8).write(
            to: URL(fileURLWithPath: destination)
        )
    }

    func setStatus(_ status: RuntimeStatus) {
        snapshots[0].status = status
    }

    func replaceIncarnation(_ createdAt: Date) {
        snapshots[0].configuration.creationDate = createdAt
    }

    func failNextCopyOutAndStop() {
        copyOutShouldFailAndStop = true
    }
}

private actor DirectNetworkClientFake: AppleNetworkResourceClient {
    private var resources: [String: NetworkResource] = [:]
    private var listShouldFail = false
    private var createShouldFail = false

    func create(
        configuration: NetworkConfiguration
    ) throws -> NetworkResource {
        if createShouldFail {
            createShouldFail = false
            throw ContainerizationError(
                .internalError,
                message: "injected network create failure"
            )
        }
        let resource = try NetworkResource(
            configuration: configuration,
            status: NetworkStatus(
                ipv4Subnet: CIDRv4("192.0.2.0/24"),
                ipv4Gateway: IPv4Address("192.0.2.1"),
                ipv6Subnet: nil
            )
        )
        resources[resource.id] = resource
        return resource
    }

    func list() throws -> [NetworkResource] {
        if listShouldFail {
            listShouldFail = false
            throw ContainerizationError(
                .internalError,
                message: "injected network list failure"
            )
        }
        return resources.values.sorted { $0.id < $1.id }
    }

    func failNextList() {
        listShouldFail = true
    }

    func failNextCreate() {
        createShouldFail = true
    }

    func get(id: String) throws -> NetworkResource {
        guard let resource = resources[id] else {
            throw ContainerizationError(
                .notFound,
                message: "network \(id) was not found"
            )
        }
        return resource
    }

    func delete(id: String) throws {
        guard resources.removeValue(forKey: id) != nil else {
            throw ContainerizationError(
                .notFound,
                message: "network \(id) was not found"
            )
        }
    }
}
