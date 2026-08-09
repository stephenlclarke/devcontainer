//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

import ContainerAPIClient
import ContainerEngineRuntimeSPI
import ContainerResource
import DevContainerModel
import Foundation

protocol AppleContainerLoggingRecordClient: Sendable {
    func loggingRecords(
        id: String
    ) async throws -> [ContainerLogRecord]

    func loggingRecordStream(
        id: String
    ) async throws -> AsyncThrowingStream<ContainerLogRecord, any Error>
}

struct LiveAppleContainerLoggingRecordClient: AppleContainerLoggingRecordClient {
    let client: ContainerClient

    func loggingRecords(
        id: String
    ) async throws -> [ContainerLogRecord] {
        try await client.logRecords(
            id: id,
            replay: ContainerLogReplayOptions(includeRotated: true)
        )
    }

    func loggingRecordStream(
        id: String
    ) async throws -> AsyncThrowingStream<ContainerLogRecord, any Error> {
        try await client.logRecordStream(
            id: id,
            replay: ContainerLogReplayOptions(includeRotated: true)
        )
    }
}

protocol AppleContainerLoggingHandoffClient: Sendable {
    func loggingHandoffSnapshot(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> DevContainerModel.ContainerSnapshot

    func loggingHandoffIsQuiesced(
        _ snapshot: DevContainerModel.ContainerSnapshot
    ) async -> Bool

    func loggingHandoffRecords(
        id: String
    ) async throws -> [ContainerLogRecord]

    func loggingHandoffRecordStream(
        id: String
    ) async throws -> AsyncThrowingStream<ContainerLogRecord, any Error>
}

public extension AppleContainerRuntime {
    /// Freezes stopped Apple-container log history into the provider-neutral
    /// logging handoff projection. Running containers, starts, and execs are
    /// rejected so the returned bytes are a terminal authority snapshot.
    func portableLoggingHandoffContainers(
        resourceIDs: [String],
        providerVersion: String,
        context: RuntimeRequestContext
    ) async throws -> [ProviderHandoffPortableLoggingContainerV1] {
        try await Self.collectPortableLoggingHandoffContainers(
            resourceIDs: resourceIDs,
            providerVersion: providerVersion,
            context: context,
            client: loggingHandoffClientOverride ?? self
        )
    }

    /// Freezes stopped Apple-container log history as bounded, one-shot
    /// record streams for direct file-backed handoff packaging.
    func portableLoggingHandoffContainerSources(
        resourceIDs: [String],
        providerVersion: String,
        context: RuntimeRequestContext
    ) async throws -> [ProviderHandoffPortableLoggingContainerSourceV2] {
        try await Self.collectPortableLoggingHandoffContainerSources(
            resourceIDs: resourceIDs,
            providerVersion: providerVersion,
            context: context,
            client: loggingHandoffClientOverride ?? self
        )
    }
}

extension AppleContainerRuntime {
    static func collectPortableLoggingHandoffContainers(
        resourceIDs: [String],
        providerVersion: String,
        context: RuntimeRequestContext,
        client: any AppleContainerLoggingHandoffClient
    ) async throws -> [ProviderHandoffPortableLoggingContainerV1] {
        try context.checkActive()
        guard
            !resourceIDs.isEmpty,
            resourceIDs.count == Set(resourceIDs).count,
            !providerVersion.isEmpty
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "logging handoff requires unique resource IDs"
            )
        }

        var containers: [ProviderHandoffPortableLoggingContainerV1] = []
        var resolvedDockerIDs = Set<String>()
        for resourceID in resourceIDs {
            try context.checkActive()
            let snapshot = try await client.loggingHandoffSnapshot(
                id: resourceID,
                context: context
            )
            guard
                snapshot.state == .stopped,
                await client.loggingHandoffIsQuiesced(snapshot),
                resolvedDockerIDs.insert(snapshot.dockerID.rawValue).inserted
            else {
                throw DevContainerError(
                    .conflict,
                    message:
                    "container \(snapshot.dockerID.rawValue) is not quiesced for logging handoff"
                )
            }
            let records = try await client.loggingHandoffRecords(
                id: snapshot.runtimeID.rawValue
            )
            try containers.append(
                ProviderHandoffPortableLoggingContainerV1(
                    containerID: snapshot.dockerID.rawValue,
                    providerID: "devcontainer.apple-container",
                    providerVersion: providerVersion,
                    records: records.map(Self.portableLogRecord)
                )
            )
        }
        return containers.sorted {
            $0.containerID.utf8.lexicographicallyPrecedes($1.containerID.utf8)
        }
    }

    static func collectPortableLoggingHandoffContainerSources(
        resourceIDs: [String],
        providerVersion: String,
        context: RuntimeRequestContext,
        client: any AppleContainerLoggingHandoffClient
    ) async throws -> [ProviderHandoffPortableLoggingContainerSourceV2] {
        try context.checkActive()
        guard
            !resourceIDs.isEmpty,
            resourceIDs.count == Set(resourceIDs).count,
            !providerVersion.isEmpty
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "logging handoff requires unique resource IDs"
            )
        }

        var containers: [ProviderHandoffPortableLoggingContainerSourceV2] = []
        var resolvedDockerIDs = Set<String>()
        for resourceID in resourceIDs {
            try context.checkActive()
            let snapshot = try await client.loggingHandoffSnapshot(
                id: resourceID,
                context: context
            )
            guard
                snapshot.state == .stopped,
                await client.loggingHandoffIsQuiesced(snapshot),
                resolvedDockerIDs.insert(snapshot.dockerID.rawValue).inserted
            else {
                throw DevContainerError(
                    .conflict,
                    message:
                    "container \(snapshot.dockerID.rawValue) is not quiesced for logging handoff"
                )
            }
            let records = try await client.loggingHandoffRecordStream(
                id: snapshot.runtimeID.rawValue
            )
            containers.append(
                ProviderHandoffPortableLoggingContainerSourceV2(
                    containerID: snapshot.dockerID.rawValue,
                    providerID: "devcontainer.apple-container",
                    providerVersion: providerVersion,
                    records: Self.portableLogRecordStream(records: records)
                )
            )
        }
        return containers.sorted {
            $0.containerID.utf8.lexicographicallyPrecedes($1.containerID.utf8)
        }
    }

    static func portableLogRecordStream(
        records: AsyncThrowingStream<ContainerLogRecord, any Error>
    ) -> AsyncThrowingStream<ProviderHandoffPortableLogRecordV1, any Error> {
        let mapper = PortableLoggingRecordMapper(
            iterator: records.makeAsyncIterator()
        )
        return AsyncThrowingStream(
            unfolding: {
                try await mapper.next()
            }
        )
    }

    static func portableLogRecord(
        _ record: ContainerLogRecord
    ) throws -> ProviderHandoffPortableLogRecordV1 {
        let interval = record.timestamp.timeIntervalSince1970
        let wholeSeconds = floor(interval)
        guard
            interval.isFinite,
            wholeSeconds >= Double(Int64.min),
            wholeSeconds <= Double(Int64.max)
        else {
            throw DevContainerError(
                .stateCorruption,
                message: "container log timestamp is outside the handoff range"
            )
        }
        var seconds = Int64(wholeSeconds)
        var nanoseconds = Int64(
            ((interval - wholeSeconds) * 1_000_000_000).rounded()
        )
        if nanoseconds == 1_000_000_000 {
            guard seconds < Int64.max else {
                throw DevContainerError(
                    .stateCorruption,
                    message: "container log timestamp overflows the handoff range"
                )
            }
            seconds += 1
            nanoseconds = 0
        }
        guard (0 ..< 1_000_000_000).contains(nanoseconds) else {
            throw DevContainerError(
                .stateCorruption,
                message: "container log timestamp precision is invalid"
            )
        }
        let stream: ProviderHandoffPortableLogRecordV1.Stream =
            switch record.stream {
            case .stdout:
                .stdout
            case .stderr:
                .stderr
            }
        return ProviderHandoffPortableLogRecordV1(
            secondsSinceUnixEpoch: seconds,
            nanoseconds: UInt32(nanoseconds),
            stream: stream,
            data: record.data
        )
    }
}

extension AppleContainerRuntime: AppleContainerLoggingHandoffClient {
    func loggingHandoffSnapshot(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> DevContainerModel.ContainerSnapshot {
        try await inspectContainer(id: id, context: context)
    }

    func loggingHandoffIsQuiesced(
        _ snapshot: DevContainerModel.ContainerSnapshot
    ) async -> Bool {
        let runtimeID = snapshot.runtimeID.rawValue
        return containerStartOperations[runtimeID] == nil
            && !execs.values.contains(where: {
                $0.containerID.rawValue == runtimeID && $0.running
            })
    }

    func loggingHandoffRecords(
        id: String
    ) async throws -> [ContainerLogRecord] {
        try await loggingRecordClient.loggingRecords(id: id)
    }

    func loggingHandoffRecordStream(
        id: String
    ) async throws -> AsyncThrowingStream<ContainerLogRecord, any Error> {
        try await loggingRecordClient.loggingRecordStream(id: id)
    }
}

private final class PortableLoggingRecordMapper: @unchecked Sendable {
    private var iterator:
        AsyncThrowingStream<ContainerLogRecord, any Error>.Iterator

    init(
        iterator: AsyncThrowingStream<ContainerLogRecord, any Error>.Iterator
    ) {
        self.iterator = iterator
    }

    func next() async throws -> ProviderHandoffPortableLogRecordV1? {
        guard let record = try await iterator.next() else {
            return nil
        }
        return try AppleContainerRuntime.portableLogRecord(record)
    }
}
