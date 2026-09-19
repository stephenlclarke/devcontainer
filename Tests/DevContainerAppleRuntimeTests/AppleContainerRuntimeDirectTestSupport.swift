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

actor FakeContainerInventory: AppleContainerInventoryClient {
    private var snapshots: [ContainerResource.ContainerSnapshot]
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

    func replaceSnapshots(_ values: [ContainerResource.ContainerSnapshot]) {
        snapshots = values
    }

    func setGetFailure(_ value: DirectInventoryFailure?) {
        getFailure = value
    }

    func listCallCount() -> Int {
        listCalls
    }
}

actor FakeNetworkClient: AppleNetworkClient {
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

    func get(id: String) throws -> NetworkSnapshot {
        try check(.get)
        guard id == snapshot.id else {
            throw ContainerizationError(.notFound, message: id)
        }
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

    func check(_ operation: NetworkFailureOperation) throws {
        if failure == operation {
            throw DirectInventoryFailure.failed
        }
    }
}

actor ArchivePermissionsClient: AppleContainerFileClient {
    var memberMode: Int?
    var stagingMode: Int?
    var privateParentMode: Int?

    func copyIn(id _: String, source: String, destination _: String) throws {
        let manager = FileManager.default
        stagingMode = try manager.attributesOfItem(atPath: source)[.posixPermissions] as? Int
        privateParentMode = try manager.attributesOfItem(
            atPath: URL(fileURLWithPath: source).deletingLastPathComponent().path
        )[.posixPermissions] as? Int
        memberMode = try manager.attributesOfItem(
            atPath: URL(fileURLWithPath: source).appendingPathComponent("permissions.txt").path
        )[.posixPermissions] as? Int
    }

    func copyOut(id _: String, source _: String, destination _: String) throws {
        throw DirectInventoryFailure.failed
    }
}

actor FakeContainerFileClient: AppleContainerFileClient {
    private var currentHosts = "127.0.0.1 localhost\n"
    private var hostsByID: [String: String] = [:]
    private var copyOutCalls = 0
    private var copyInCalls = 0
    private var lastUploadedPermissions: Int?
    private var holdDownload = false
    private var heldDownload: CheckedContinuation<Void, Never>?
    private var downloadObserver: CheckedContinuation<Void, Never>?

    func holdNextDownload() {
        holdDownload = true
    }

    func waitForHeldDownload() async {
        if heldDownload != nil {
            return
        }
        await withCheckedContinuation { downloadObserver = $0 }
    }

    func releaseDownload() {
        heldDownload?.resume()
        heldDownload = nil
    }

    func copyIn(
        id: String,
        source: String,
        destination _: String
    ) throws {
        copyInCalls += 1
        lastUploadedPermissions = try FileManager.default.attributesOfItem(
            atPath: source
        )[.posixPermissions] as? Int
        currentHosts = try String(contentsOfFile: source, encoding: .utf8)
        hostsByID[id] = currentHosts
    }

    func copyOut(
        id: String,
        source _: String,
        destination: String
    ) async throws {
        copyOutCalls += 1
        if holdDownload {
            holdDownload = false
            await withCheckedContinuation {
                heldDownload = $0
                downloadObserver?.resume()
                downloadObserver = nil
            }
        }
        let contents = hostsByID[id] ?? "127.0.0.1 localhost\n"
        try Data(contents.utf8).write(
            to: URL(fileURLWithPath: destination)
        )
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o644], ofItemAtPath: destination
        )
    }

    func resetHostsForBootstrap() {
        currentHosts = "127.0.0.1 localhost\n"
        hostsByID.removeAll()
    }

    func copyOutCallCount() -> Int {
        copyOutCalls
    }

    func copyInCallCount() -> Int {
        copyInCalls
    }

    func uploadedPermissions() -> Int? {
        lastUploadedPermissions
    }

    func hosts() -> String {
        currentHosts
    }
}

#if !DEVCONTAINER_ENHANCED_RUNTIME
    func waitForHostsQueueChange(runtime: AppleContainerRuntime, previous: UUID) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while await runtime.networkHostsOperation?.id == previous {
            if ContinuousClock.now >= deadline {
                return false
            }
            await Task.yield()
        }
        return true
    }

    func nativeNetworkSnapshot(
        id: String,
        service: String?,
        address: String,
        network: String = "shared",
        nativeOnly: Bool = false
    ) throws -> ContainerResource
        .ContainerSnapshot
    {
        var labels: [String: String] = [:]
        if let service {
            let prefixes = nativeOnly
                ? ["com.apple.container.compose."]
                : ["com.apple.container.compose.", "com.docker.compose."]
            for prefix in prefixes {
                labels[prefix + "project"] = network
                labels[prefix + "service"] = service
                labels[prefix + "oneoff"] = "false"
            }
            labels["com.apple.container.compose.version"] = "1"
        }
        let base = nativeSnapshot(id: id, labels: labels, status: .running)
        var configuration = base.configuration
        configuration.networks = [AttachmentConfiguration(network: network, options: .init(hostname: id))]
        return try ContainerResource.ContainerSnapshot(
            configuration: configuration, status: .running,
            networks: [Attachment(
                network: network,
                hostname: id,
                ipv4Address: .init("\(address)/24"),
                ipv4Gateway: .init("192.0.2.1"),
                ipv6Address: nil,
                macAddress: nil
            )],
            startedDate: base.startedDate
        )
    }
#endif

enum NetworkFailureOperation: CaseIterable {
    case list
    case get
    case create
    case delete
}

enum DirectInventoryFailure: Error {
    case failed
    case notFound
}

func directRuntime(
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
        storageRoots: AppleContainerRuntime.StorageRoots(
            volumes: fixture.root.appendingPathComponent("volumes"),
            transfers: fixture.root.appendingPathComponent("transfers")
        ),
        clients: AppleContainerRuntime.DirectClients(
            api: ContainerClient(),
            inventory: inventory,
            files: files,
            networks: networks,
            images: FakeAppleImageIdentityClient()
        )
    )
}

func nativeSnapshot(
    id: String,
    labels: [String: String],
    status: RuntimeStatus,
    imageReference: String = "fixture:latest"
) -> ContainerResource.ContainerSnapshot {
    let image = ImageDescription(
        reference: imageReference,
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
