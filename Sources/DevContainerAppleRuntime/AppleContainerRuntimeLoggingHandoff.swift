//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

import ContainerEngineRuntimeSPI
import ContainerResource
import DevContainerModel
import Foundation

public extension AppleContainerRuntime {
    /// Freezes stopped Apple-container log history into the provider-neutral
    /// logging handoff projection. Running containers, starts, and execs are
    /// rejected so the returned bytes are a terminal authority snapshot.
    func portableLoggingHandoffContainers(
        resourceIDs: [String],
        providerVersion: String,
        context: RuntimeRequestContext
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
            let snapshot = try await inspectContainer(
                id: resourceID,
                context: context
            )
            let runtimeID = snapshot.runtimeID.rawValue
            guard
                snapshot.state == .stopped,
                containerStartOperations[runtimeID] == nil,
                !execs.values.contains(where: {
                    $0.containerID.rawValue == runtimeID && $0.running
                }),
                resolvedDockerIDs.insert(snapshot.dockerID.rawValue).inserted
            else {
                throw DevContainerError(
                    .conflict,
                    message:
                    "container \(snapshot.dockerID.rawValue) is not quiesced for logging handoff"
                )
            }
            let records = try await apiClient.logRecords(
                id: runtimeID,
                replay: ContainerLogReplayOptions(includeRotated: true)
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
