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

import DevContainerModel
import Foundation

extension AppleContainerRuntime {
    func scheduleAutomaticRemoval(
        id: String,
        expectedCreatedAt suppliedCreatedAt: Date? = nil
    ) {
        guard automaticRemovalRegistrations[id] == nil else {
            return
        }
        let registration = UUID()
        let expectedCreatedAt = suppliedCreatedAt ?? requestedContainers[id]?.createdAt
        automaticRemovalRegistrations[id] = registration
        automaticRemovalTasks[id] = Task {
            var identifiers: Set<String> = [id]
            defer {
                finishAutomaticRemoval(
                    id: id,
                    registration: registration,
                    identifiers: identifiers
                )
            }
            // Give a concurrently delivered external restart enough time to
            // become visible before deleting a stable native container name.
            try? await Task.sleep(for: .milliseconds(100))
            await performAutomaticRemoval(
                id: id,
                registration: registration,
                expectedCreatedAt: expectedCreatedAt,
                identifiers: &identifiers
            )
        }
    }

    private func finishAutomaticRemoval(
        id: String,
        registration: UUID,
        identifiers: Set<String>
    ) {
        for identifier in identifiers
            where automaticRemovalRegistrations[identifier] == registration
        {
            automaticRemovalRegistrations.removeValue(forKey: identifier)
        }
        if automaticRemovalRegistrations[id] == nil {
            automaticRemovalTasks.removeValue(forKey: id)
        }
    }

    private func performAutomaticRemoval(
        id: String,
        registration: UUID,
        expectedCreatedAt: Date?,
        identifiers: inout Set<String>
    ) async {
        let retryDelays: [Duration] = [
            .milliseconds(100),
            .milliseconds(250),
            .milliseconds(500),
            .seconds(1)
        ]
        for attempt in 0 ... retryDelays.count {
            guard automaticRemovalRegistrations[id] == registration,
                  canPerformAutomaticRemoval(
                      registration: registration,
                      identifiers: identifiers
                  )
            else {
                return
            }
            do {
                let snapshot = try await automaticRemovalSnapshot(id: id)
                guard let observedIdentifiers = automaticRemovalIdentifiers(
                    id: id,
                    snapshot: snapshot,
                    expectedCreatedAt: expectedCreatedAt
                ) else {
                    return
                }
                identifiers.formUnion(observedIdentifiers)
                guard canPerformAutomaticRemoval(
                    registration: registration,
                    identifiers: identifiers
                ) else {
                    return
                }
                for identifier in identifiers {
                    automaticRemovalRegistrations[identifier] = registration
                }
                try await removeAutomatically(id: id)
                return
            } catch let error as DevContainerError where error.code == .notFound {
                // The native runtime already completed the requested removal.
                return
            } catch {
                guard attempt < retryDelays.count else {
                    return
                }
                try? await Task.sleep(for: retryDelays[attempt])
            }
        }
    }

    private func automaticRemovalIdentifiers(
        id: String,
        snapshot: DevContainerModel.ContainerSnapshot,
        expectedCreatedAt: Date?
    ) -> Set<String>? {
        guard snapshot.state == .stopped else {
            return nil
        }
        if let expectedCreatedAt,
           !Self.sameContainerIncarnation(
               metadataCreatedAt: expectedCreatedAt,
               observedCreatedAt: snapshot.createdAt
           )
        {
            return nil
        }
        return [
            id,
            snapshot.runtimeID.rawValue,
            snapshot.dockerID.rawValue,
            snapshot.spec.name
        ]
    }

    private func canPerformAutomaticRemoval(
        registration: UUID,
        identifiers: Set<String>
    ) -> Bool {
        identifiers.allSatisfy {
            containerStartOperations[$0] == nil
                && containerExitRegistrations[$0] == nil
                && (automaticRemovalRegistrations[$0] == nil
                    || automaticRemovalRegistrations[$0] == registration)
        }
    }

    private func automaticRemovalSnapshot(id: String) async throws
        -> DevContainerModel.ContainerSnapshot
    {
        let context = automaticRemovalContext()
        return try await RuntimeRequestScope.$context.withValue(context) {
            try await self.inspectContainer(id: id, context: context)
        }
    }

    private func removeAutomatically(id: String) async throws {
        let context = automaticRemovalContext()
        try await RuntimeRequestScope.$context.withValue(context) {
            try await self.removeContainer(
                id: id,
                force: true,
                context: context
            )
        }
    }

    private func automaticRemovalContext() -> RuntimeRequestContext {
        RuntimeRequestContext(deadline: Date().addingTimeInterval(5))
    }
}
