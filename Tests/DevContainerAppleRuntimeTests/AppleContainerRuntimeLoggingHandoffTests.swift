//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

import ContainerAPIClient
import ContainerEngineRuntimeSPI
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleContainerRuntimeLoggingHandoffTests {
    @Test
    func `runtime record preserves stream bytes and nanosecond timestamp`() throws {
        let date = Date(timeIntervalSince1970: 1_786_000_000.123_456_7)
        let value = try AppleContainerRuntime.portableLogRecord(
            ContainerLogRecord(
                timestamp: date,
                stream: .stderr,
                data: Data([0x00, 0xFF, 0x0A])
            )
        )

        #expect(value.secondsSinceUnixEpoch == 1_786_000_000)
        #expect(value.nanoseconds == 123_456_717)
        #expect(value.stream == .stderr)
        #expect(value.data == Data([0x00, 0xFF, 0x0A]))
    }

    @Test
    func `runtime record normalizes a negative fractional timestamp`() throws {
        let value = try AppleContainerRuntime.portableLogRecord(
            ContainerLogRecord(
                timestamp: Date(timeIntervalSince1970: -0.25),
                stream: .stdout,
                data: Data()
            )
        )

        #expect(value.secondsSinceUnixEpoch == -1)
        #expect(value.nanoseconds == 750_000_000)
    }

    @Test
    func `runtime record stream is mapped incrementally`() async throws {
        let records = [
            ContainerLogRecord(
                timestamp: Date(timeIntervalSince1970: 1_786_000_000.125),
                stream: .stdout,
                data: Data("first\n".utf8)
            ),
            ContainerLogRecord(
                timestamp: Date(timeIntervalSince1970: 1_786_000_001.25),
                stream: .stderr,
                data: Data([0x00, 0xFF, 0x0A])
            )
        ]
        let recordsStream = AsyncThrowingStream<ContainerLogRecord, any Error> { continuation in
            for record in records {
                continuation.yield(record)
            }
            continuation.finish()
        }

        var values: [ProviderHandoffPortableLogRecordV1] = []
        for try await value in AppleContainerRuntime.portableLogRecordStream(
            records: recordsStream
        ) {
            values.append(value)
        }

        #expect(values.count == 2)
        #expect(values.map(\.stream) == [.stdout, .stderr])
        #expect(values.map(\.data) == records.map(\.data))
    }

    @Test
    // swiftlint:disable:next function_body_length
    func `handoff containers sort Docker IDs and preserve records`() async throws {
        let client = HandoffClient(
            snapshots: [
                "z-runtime": handoffSnapshot(
                    runtimeID: "z-runtime",
                    dockerID: "docker-z"
                ),
                "a-runtime": handoffSnapshot(
                    runtimeID: "a-runtime",
                    dockerID: "docker-a"
                )
            ],
            records: [
                "z-runtime": [
                    ContainerLogRecord(
                        timestamp: Date(timeIntervalSince1970: 4.5),
                        stream: .stderr,
                        data: Data([0xFF])
                    )
                ],
                "a-runtime": [
                    ContainerLogRecord(
                        timestamp: Date(timeIntervalSince1970: 3.25),
                        stream: .stdout,
                        data: Data("first\\n".utf8)
                    )
                ]
            ]
        )

        let values = try await AppleContainerRuntime
            .collectPortableLoggingHandoffContainers(
                resourceIDs: ["z-runtime", "a-runtime"],
                providerVersion: "fixture-version",
                context: RuntimeRequestContext(),
                client: client
            )

        #expect(values.map(\.containerID) == ["docker-a", "docker-z"])
        #expect(values.map(\.providerID) == [
            "devcontainer.apple-container",
            "devcontainer.apple-container"
        ])
        #expect(values.map(\.providerVersion) == [
            "fixture-version",
            "fixture-version"
        ])
        #expect(values[0].records == [
            ProviderHandoffPortableLogRecordV1(
                secondsSinceUnixEpoch: 3,
                nanoseconds: 250_000_000,
                stream: .stdout,
                data: Data("first\\n".utf8)
            )
        ])
        #expect(values[1].records == [
            ProviderHandoffPortableLogRecordV1(
                secondsSinceUnixEpoch: 4,
                nanoseconds: 500_000_000,
                stream: .stderr,
                data: Data([0xFF])
            )
        ])
    }

    @Test
    func `handoff sources stream records and sort Docker IDs`() async throws {
        let client = HandoffClient(
            snapshots: [
                "z-runtime": handoffSnapshot(
                    runtimeID: "z-runtime",
                    dockerID: "docker-z"
                ),
                "a-runtime": handoffSnapshot(
                    runtimeID: "a-runtime",
                    dockerID: "docker-a"
                )
            ],
            records: [
                "z-runtime": [
                    ContainerLogRecord(
                        timestamp: Date(timeIntervalSince1970: 9),
                        stream: .stderr,
                        data: Data("second\\n".utf8)
                    )
                ],
                "a-runtime": [
                    ContainerLogRecord(
                        timestamp: Date(timeIntervalSince1970: 8),
                        stream: .stdout,
                        data: Data("first\\n".utf8)
                    )
                ]
            ]
        )

        let values = try await AppleContainerRuntime
            .collectPortableLoggingHandoffContainerSources(
                resourceIDs: ["z-runtime", "a-runtime"],
                providerVersion: "fixture-version",
                context: RuntimeRequestContext(),
                client: client
            )

        #expect(values.map(\.containerID) == ["docker-a", "docker-z"])
        var records: [ProviderHandoffPortableLogRecordV1] = []
        for try await record in values[0].records {
            records.append(record)
        }
        #expect(records == [
            ProviderHandoffPortableLogRecordV1(
                secondsSinceUnixEpoch: 8,
                nanoseconds: 0,
                stream: .stdout,
                data: Data("first\\n".utf8)
            )
        ])
    }

    @Test
    // swiftlint:disable:next function_body_length
    func `handoff collectors reject invalid or nonquiesced requests`() async throws {
        let stopped = handoffSnapshot(
            runtimeID: "stopped-runtime",
            dockerID: "docker-stopped"
        )
        let running = handoffSnapshot(
            runtimeID: "running-runtime",
            dockerID: "docker-running",
            state: .running
        )
        let client = HandoffClient(
            snapshots: [
                "stopped-runtime": stopped,
                "running-runtime": running,
                "duplicate-a": handoffSnapshot(
                    runtimeID: "duplicate-a",
                    dockerID: "docker-duplicate"
                ),
                "duplicate-b": handoffSnapshot(
                    runtimeID: "duplicate-b",
                    dockerID: "docker-duplicate"
                )
            ],
            records: [:],
            activeRuntimeIDs: ["stopped-runtime"]
        )

        await #expect(throws: DevContainerError.self) {
            _ = try await AppleContainerRuntime
                .collectPortableLoggingHandoffContainers(
                    resourceIDs: [],
                    providerVersion: "fixture-version",
                    context: RuntimeRequestContext(),
                    client: client
                )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await AppleContainerRuntime
                .collectPortableLoggingHandoffContainerSources(
                    resourceIDs: [],
                    providerVersion: "fixture-version",
                    context: RuntimeRequestContext(),
                    client: client
                )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await AppleContainerRuntime
                .collectPortableLoggingHandoffContainerSources(
                    resourceIDs: ["running-runtime"],
                    providerVersion: "fixture-version",
                    context: RuntimeRequestContext(),
                    client: client
                )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await AppleContainerRuntime
                .collectPortableLoggingHandoffContainers(
                    resourceIDs: ["stopped-runtime"],
                    providerVersion: "fixture-version",
                    context: RuntimeRequestContext(),
                    client: client
                )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await AppleContainerRuntime
                .collectPortableLoggingHandoffContainerSources(
                    resourceIDs: ["duplicate-a", "duplicate-b"],
                    providerVersion: "fixture-version",
                    context: RuntimeRequestContext(),
                    client: client
                )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await AppleContainerRuntime
                .collectPortableLoggingHandoffContainers(
                    resourceIDs: ["running-runtime", "running-runtime"],
                    providerVersion: "",
                    context: RuntimeRequestContext(
                        deadline: Date(timeIntervalSinceNow: -1)
                    ),
                    client: client
                )
        }
    }

    @Test
    func `runtime record rejects out of range timestamp and carries rounding`() throws {
        #expect(throws: DevContainerError.self) {
            _ = try AppleContainerRuntime.portableLogRecord(
                ContainerLogRecord(
                    timestamp: Date(timeIntervalSince1970: .greatestFiniteMagnitude),
                    stream: .stdout,
                    data: Data()
                )
            )
        }

        let rounded = try AppleContainerRuntime.portableLogRecord(
            ContainerLogRecord(
                timestamp: Date(timeIntervalSince1970: 0.999_999_999_6),
                stream: .stdout,
                data: Data()
            )
        )
        #expect(rounded.secondsSinceUnixEpoch == 1)
        #expect(rounded.nanoseconds == 0)
    }

    @Test
    func `public handoff methods use the injected handoff client`() async throws {
        let client = HandoffClient(
            snapshots: [
                "runtime": handoffSnapshot(
                    runtimeID: "runtime",
                    dockerID: "docker-id"
                )
            ],
            records: [
                "runtime": [
                    ContainerLogRecord(
                        timestamp: Date(timeIntervalSince1970: 12.25),
                        stream: .stdout,
                        data: Data("fixture\\n".utf8)
                    )
                ]
            ]
        )
        let fixture = try HandoffRuntimeFixture(
            records: client,
            handoffOverride: client
        )

        let containers = try await fixture.runtime
            .portableLoggingHandoffContainers(
                resourceIDs: ["runtime"],
                providerVersion: "fixture-version",
                context: RuntimeRequestContext()
            )
        let sources = try await fixture.runtime
            .portableLoggingHandoffContainerSources(
                resourceIDs: ["runtime"],
                providerVersion: "fixture-version",
                context: RuntimeRequestContext()
            )

        #expect(containers.map(\.containerID) == ["docker-id"])
        #expect(containers[0].records.count == 1)
        var sourceRecords: [ProviderHandoffPortableLogRecordV1] = []
        for try await record in sources[0].records {
            sourceRecords.append(record)
        }
        #expect(sourceRecords == containers[0].records)
    }

    @Test
    func `runtime handoff client uses the injected record client`() async throws {
        let client = HandoffClient(
            snapshots: [:],
            records: [
                "runtime": [
                    ContainerLogRecord(
                        timestamp: Date(timeIntervalSince1970: 13),
                        stream: .stderr,
                        data: Data([0xFF])
                    )
                ]
            ]
        )
        let fixture = try HandoffRuntimeFixture(records: client)
        let snapshot = handoffSnapshot(
            runtimeID: "runtime",
            dockerID: "docker-id"
        )

        #expect(await fixture.runtime.loggingHandoffIsQuiesced(snapshot))
        let records = try await fixture.runtime.loggingHandoffRecords(id: "runtime")
        let stream = try await fixture.runtime.loggingHandoffRecordStream(id: "runtime")
        var streamed: [ContainerLogRecord] = []
        for try await record in stream {
            streamed.append(record)
        }

        #expect(records == streamed)
        #expect(records.map(\.stream) == [.stderr])
    }

    @Test
    func `runtime handoff snapshot propagates an unavailable provider response`() async throws {
        let client = HandoffClient(snapshots: [:], records: [:])
        let fixture = try HandoffRuntimeFixture(records: client)
        var requestSucceeded = false

        do {
            _ = try await fixture.runtime.loggingHandoffSnapshot(
                id: "missing-runtime",
                context: RuntimeRequestContext()
            )
            requestSucceeded = true
        } catch {
            // The fixture's /usr/bin/true executable provides no container
            // inventory, so this is the expected adapter failure path.
        }

        #expect(!requestSucceeded)
    }

    @Test
    func `live record client forwards read-only missing-container calls`() async {
        let client = LiveAppleContainerLoggingRecordClient(client: ContainerClient())
        let missingID = "devcontainer-logging-handoff-\(UUID().uuidString)"
        var recordsSucceeded = false
        var streamSucceeded = false

        do {
            _ = try await client.loggingRecords(id: missingID)
            recordsSucceeded = true
        } catch {
            // A missing container or unavailable local API server is the
            // expected read-only failure result for this unique fixture ID.
        }
        do {
            _ = try await client.loggingRecordStream(id: missingID)
            streamSucceeded = true
        } catch {
            // See the record retrieval assertion above.
        }

        #expect(!recordsSucceeded)
        #expect(!streamSucceeded)
    }
}

private actor HandoffClient:
    AppleContainerLoggingHandoffClient,
    AppleContainerLoggingRecordClient
{
    private let snapshots: [String: DevContainerModel.ContainerSnapshot]
    private let records: [String: [ContainerLogRecord]]
    private let activeRuntimeIDs: Set<String>

    init(
        snapshots: [String: DevContainerModel.ContainerSnapshot],
        records: [String: [ContainerLogRecord]],
        activeRuntimeIDs: Set<String> = []
    ) {
        self.snapshots = snapshots
        self.records = records
        self.activeRuntimeIDs = activeRuntimeIDs
    }

    func loggingHandoffSnapshot(
        id: String,
        context _: RuntimeRequestContext
    ) throws -> DevContainerModel.ContainerSnapshot {
        guard let snapshot = snapshots[id] else {
            throw DevContainerError(.notFound, message: "missing test container \(id)")
        }
        return snapshot
    }

    func loggingHandoffIsQuiesced(
        _ snapshot: DevContainerModel.ContainerSnapshot
    ) -> Bool {
        !activeRuntimeIDs.contains(snapshot.runtimeID.rawValue)
    }

    func loggingHandoffRecords(
        id: String
    ) -> [ContainerLogRecord] {
        loggingRecords(id: id)
    }

    func loggingRecords(
        id: String
    ) -> [ContainerLogRecord] {
        records[id] ?? []
    }

    func loggingHandoffRecordStream(
        id: String
    ) -> AsyncThrowingStream<ContainerLogRecord, any Error> {
        loggingRecordStream(id: id)
    }

    func loggingRecordStream(
        id: String
    ) -> AsyncThrowingStream<ContainerLogRecord, any Error> {
        let values = records[id] ?? []
        return AsyncThrowingStream { continuation in
            for value in values {
                continuation.yield(value)
            }
            continuation.finish()
        }
    }
}

private final class HandoffRuntimeFixture {
    let root: URL
    let runtime: AppleContainerRuntime

    init(
        records: any AppleContainerLoggingRecordClient,
        handoffOverride: (any AppleContainerLoggingHandoffClient)? = nil
    ) throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(
            "devcontainer-logging-handoff-\(UUID().uuidString)",
            isDirectory: true
        )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        runtime = try AppleContainerRuntime(
            executable: URL(fileURLWithPath: "/usr/bin/true"),
            environment: [:],
            useDirectProcessAPI: false,
            useDirectContainerAPI: false,
            metadataStore: nil,
            volumeRoot: root.appendingPathComponent("volumes", isDirectory: true),
            clients: AppleContainerRuntime.DirectClients(
                api: ContainerClient(),
                inventory: EmptyInventoryClient(),
                files: EmptyFileClient(),
                networks: EmptyNetworkClient(),
                loggingRecords: records,
                loggingHandoffClientOverride: handoffOverride
            )
        )
    }

    deinit {
        try? FileManager.default.removeItem(at: root)
    }
}

private struct EmptyInventoryClient: AppleContainerInventoryClient {
    func list() async throws -> [ContainerResource.ContainerSnapshot] {
        []
    }

    func get(id: String) async throws -> ContainerResource.ContainerSnapshot {
        throw DevContainerError(.notFound, message: "missing test container \(id)")
    }
}

private struct EmptyFileClient: AppleContainerFileClient {
    func copyIn(
        id _: String,
        source _: String,
        destination _: String
    ) async throws {}

    func copyOut(
        id _: String,
        source _: String,
        destination _: String
    ) async throws {}
}

private struct EmptyNetworkClient: AppleNetworkClient {
    func list() async throws -> [NetworkSnapshot] {
        []
    }

    func get(id: String) async throws -> NetworkSnapshot {
        throw DevContainerError(.notFound, message: "missing test network \(id)")
    }

    func create(spec _: NetworkSpec) async throws -> NetworkSnapshot {
        throw DevContainerError(.runtimeUnavailable, message: "test network creation is disabled")
    }

    func delete(id _: String) async throws {}
}

private func handoffSnapshot(
    runtimeID: String,
    dockerID: String,
    state: RuntimeContainerState = .stopped
) -> DevContainerModel.ContainerSnapshot {
    DevContainerModel.ContainerSnapshot(
        runtimeID: RuntimeID(rawValue: runtimeID),
        dockerID: DockerID(rawValue: dockerID),
        spec: ContainerSpec(name: runtimeID, image: "fixture:latest"),
        state: state,
        createdAt: Date(timeIntervalSince1970: 1)
    )
}
