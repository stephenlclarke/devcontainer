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
    func `native typed inventory verifies digest image spelling`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let digest = "sha256:" + String(repeating: "a", count: 64)
        let original = "fixture:version@" + digest
        let native = nativeSnapshot(
            id: "app", labels: [AppleContainerRuntime.composeImageReferenceLabel: original],
            status: .running, imageReference: "docker.io/library/fixture@" + digest
        )
        let record = try await runtime.containerRecord(native)
        #expect(record.spec.image == original)
    }

    @Test(arguments: [0o644, 0o750, 0o777], [false, true])
    func `archive upload preserves member permissions inside private staging`(
        mode: Int, includesRoot: Bool
    ) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let input = fixture.root.appendingPathComponent("archive-input")
        try FileManager.default.createDirectory(at: input, withIntermediateDirectories: false)
        try FileManager.default.setAttributes([.posixPermissions: 0o777], ofItemAtPath: input.path)
        let source = input.appendingPathComponent("permissions.txt")
        try Data("archive permission fixture".utf8).write(to: source)
        try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: source.path)
        let archive = try await AppleCommandRunner.run(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["--format=ustar", "-cf", "-", "-C", input.path, includesRoot ? "." : source.lastPathComponent],
            environment: ["COPYFILE_DISABLE": "1"]
        )
        #expect(archive.exitCode == 0)
        let files = ArchivePermissionsClient()
        let runtime = try directRuntime(
            fixture: fixture,
            inventory: FakeContainerInventory(snapshots: [
                nativeSnapshot(id: "fixture", labels: [:], status: .running)
            ]),
            files: files
        )
        try await runtime.copyArchiveToContainer(
            id: "fixture", path: "/workspace", archive: archive.standardOutput, context: RuntimeRequestContext()
        )
        #expect(await files.memberMode == mode)
        #expect(await files.privateParentMode == 0o700)
        #expect(await files.stagingMode == (includesRoot ? 0o777 : 0o700))
    }

    @Test(arguments: ["a", "c"])
    func `native digest references retain config IDs during list and inspection`(hex: String) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        try fixture.setImageInventory([imageRecord("fixture:latest")])
        let snapshot = nativeSnapshot(
            id: "fixture", labels: [:], status: .running,
            imageReference: "fixture@sha256:" + String(repeating: hex, count: 64)
        )
        let runtime = try directRuntime(fixture: fixture, inventory: FakeContainerInventory(snapshots: [snapshot]))
        let context = RuntimeRequestContext()
        let listed = try await runtime.listContainersDirect(all: true, labels: [:], context: context)
        #expect(listed.first?.imageID == FakeAppleImageIdentityClient.digest)
        let inspected = try await runtime.inspectContainerDirect(id: "fixture", context: context)
        #expect(inspected?.imageID == FakeAppleImageIdentityClient.digest)
    }

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
        #expect(running.first?.imageID == FakeAppleImageIdentityClient.digest)

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
        #expect(exact.imageID == FakeAppleImageIdentityClient.digest)
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

        let descriptor = try await runtime.descriptor(
            context: RuntimeRequestContext()
        )
        #expect(descriptor.provider == .containerCompose)
        #expect(descriptor.distribution == "container-compose")

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

    @Test(arguments: [false, true])
    func `stale incarnation and start generation never receive hosts writes`(restarted: Bool) async throws {
        let fixture = try FakeAppleCLI()
        let inventory = FakeContainerInventory(snapshots: [nativeSnapshot(
            id: "fixture",
            labels: [:],
            status: .running
        )])
        let files = FakeContainerFileClient()
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, files: files)
        let target = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "fixture"), dockerID: DockerID(rawValue: "fixture"),
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                networks: [NetworkAttachment(name: "shared")]
            ),
            state: .running, createdAt: Date(timeIntervalSince1970: restarted ? 1 : 0),
            startedAt: Date(timeIntervalSince1970: restarted ? 0 : 2),
            networkAddresses: ["shared": "192.0.2.2"]
        )
        try await runtime.synchronizeNetworkHosts(
            target: target,
            containers: [target],
            context: RuntimeRequestContext()
        )
        #expect(await files.copyInCallCount() == 0)
        #expect(await files.copyOutCallCount() == 0)
    }

    @Test
    func `external restart invalidates managed hosts without a creation date change`() async throws {
        let fixture = try FakeAppleCLI()
        let inventory = FakeContainerInventory(snapshots: [nativeSnapshot(
            id: "fixture",
            labels: [:],
            status: .running
        )])
        let files = FakeContainerFileClient()
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, files: files)
        var target = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "fixture"), dockerID: DockerID(rawValue: "fixture"),
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                networks: [NetworkAttachment(name: "direct-network")]
            ),
            state: .running, createdAt: Date(timeIntervalSince1970: 1),
            networkAddresses: ["direct-network": "192.0.2.2"]
        )
        target.startedAt = Date(timeIntervalSince1970: 2)
        let context = RuntimeRequestContext()
        try await runtime.synchronizeNetworkHosts(target: target, containers: [target], context: context)
        await files.resetHostsForBootstrap()
        target.startedAt = Date(timeIntervalSince1970: 3)
        let prior = nativeSnapshot(id: "fixture", labels: [:], status: .running)
        await inventory.replaceSnapshots([ContainerResource.ContainerSnapshot(
            configuration: prior.configuration, status: .running, networks: [], startedDate: target.startedAt
        )])
        try await runtime.synchronizeNetworkHosts(target: target, containers: [target], context: context)
        #expect(await files.copyInCallCount() == 2)
        #expect(await files.hosts().contains("192.0.2.2 fixture"))
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
            startedAt: Date(timeIntervalSince1970: 2),
            networkAddresses: ["direct-network": "192.0.2.2"]
        )

        try await runtime.synchronizeNetworkHosts(
            target: target,
            containers: [target],
            context: context
        )
        #expect(FileManager.default.fileExists(
            atPath: fixture.root.appendingPathComponent("transfers").path
        ))
        try await runtime.synchronizeNetworkHosts(
            target: target,
            containers: [target],
            context: context
        )
        #expect(await files.copyOutCallCount() == 1)
        #expect(await files.copyInCallCount() == 1)
        #expect(await files.uploadedPermissions() == 0o644)

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

        await files.resetHostsForBootstrap()
        try await runtime.restartContainer(
            id: "fixture",
            timeout: nil,
            context: context
        )
        try await runtime.synchronizeNetworkHosts(
            target: target,
            containers: [target],
            context: context
        )

        #expect(await files.copyOutCallCount() == 3)
        #expect(await files.copyInCallCount() == 3)
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
            try await runtime.inspectNetwork(
                id: network.spec.name,
                context: context
            ) == network
        )
        #expect(
            try await runtime.createNetwork(
                spec: network.spec,
                context: context
            ) == network
        )
        try await runtime.removeNetwork(
            id: network.spec.name,
            context: context
        )
        #expect(await networks.deletedIDs() == [network.id])
        #expect(await networks.listCallCount() == 3)
        do {
            _ = try await runtime.inspectNetwork(
                id: "missing-network",
                context: context
            )
            Issue.record("missing network lookup unexpectedly succeeded")
        } catch let error as DevContainerError {
            #expect(error.code == .notFound)
        }

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
                "CONTAINER_APP_ROOT": "/stable/runtime",
                "CONTAINER_SERVICE_NAMESPACE": "io.github.example.runtime",
                "HOME": "/fixture",
                "SECRET": "excluded"
            ]) == [
                "CONTAINER_APP_ROOT": "/stable/runtime",
                "CONTAINER_SERVICE_NAMESPACE": "io.github.example.runtime",
                "HOME": "/fixture"
            ]
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
