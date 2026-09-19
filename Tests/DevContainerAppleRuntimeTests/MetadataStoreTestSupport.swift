//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

actor TestMetadataStore: RuntimeCreationStore {
    private let recordDelay: Duration?
    private var values: [String: RuntimeContainerMetadata] = [:]
    private var listCount = 0
    private var lookupCount = 0
    private var records = 0
    private var creations: [String: RuntimeContainerCreation] = [:]
    private let failCreationCompletion: Bool
    private let failCreationIntent: Bool

    init(recordDelay: Duration? = nil, failCreationCompletion: Bool = false, failCreationIntent: Bool = false) {
        self.recordDelay = recordDelay
        self.failCreationCompletion = failCreationCompletion
        self.failCreationIntent = failCreationIntent
    }

    func beginContainerCreation(_ creation: RuntimeContainerCreation) throws {
        guard !failCreationIntent, creations[creation.runtimeID] == nil else { throw MetadataTestError.writeFailed }
        creations[creation.runtimeID] = creation
    }

    func pendingContainerCreation(id: String) -> RuntimeContainerCreation? {
        creations[id]
    }

    func finishContainerCreation(_ metadata: RuntimeContainerMetadata, operationID: UUID) throws {
        guard !failCreationCompletion, let creation = creations[metadata.runtimeID.rawValue],
              creation.operationID == operationID, creation.imageID == metadata.imageID,
              creation.spec == metadata.spec, creation.nativeCreatedAt == metadata.createdAt
        else {
            throw MetadataTestError.writeFailed
        }
        records += 1
        values[metadata.runtimeID.rawValue] = metadata
        creations[metadata.runtimeID.rawValue] = nil
    }

    func discardContainerCreation(id: String, operationID: UUID) throws {
        guard creations[id]?.operationID == operationID else { throw MetadataTestError.writeFailed }
        creations[id] = nil
    }

    func recordContainerMetadata(
        _ metadata: RuntimeContainerMetadata
    ) async {
        if let recordDelay {
            try? await Task.sleep(for: recordDelay)
        }
        records += 1
        values[metadata.runtimeID.rawValue] = metadata
    }

    func recordCount() -> Int {
        records
    }

    func containerMetadata(
        id: String
    ) -> RuntimeContainerMetadata? {
        lookupCount += 1
        return values[id]
            ?? values.values.first(where: { $0.dockerID.rawValue == id })
    }

    func listContainerMetadata() -> [RuntimeContainerMetadata] {
        listCount += 1
        return Array(values.values)
    }

    func resetAccessCounts() {
        listCount = 0
        lookupCount = 0
    }

    func accessCounts() -> (list: Int, lookup: Int) {
        (listCount, lookupCount)
    }

    func markContainerStarted(id: String, at date: Date) {
        values[id]?.startedAt = date
    }

    func removeContainerMetadata(id: String) {
        values[id] = nil
    }
}

enum MetadataTestError: Error {
    case writeFailed
}

actor FailingMetadataStore: RuntimeMetadataStore {
    func recordContainerMetadata(
        _: RuntimeContainerMetadata
    ) throws {
        throw MetadataTestError.writeFailed
    }

    func containerMetadata(
        id _: String
    ) -> RuntimeContainerMetadata? {
        nil
    }

    func listContainerMetadata() -> [RuntimeContainerMetadata] {
        []
    }

    func markContainerStarted(id _: String, at _: Date) {}

    func removeContainerMetadata(id _: String) {}
}
