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
import DevContainerRuntimeSPI
import Foundation

public extension AppleContainerRuntime {
    func startAttachedContainer(
        id: String,
        terminal: Bool,
        context: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession {
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let resolved = try await resolveContainerID(id, context: context)
        mutationIdentifiers.insert(resolved)
        includeContainerLifecycleMutation(id: resolved, registration: mutation)
        guard automaticRemovalRegistrations[id] == nil,
              automaticRemovalRegistrations[resolved] == nil
        else {
            throw DevContainerError(
                .conflict,
                message: "container automatic removal is in progress"
            )
        }
        guard containerStartOperations[resolved] == nil else {
            throw DevContainerError(.conflict, message: "container \(id) is already starting")
        }
        let arguments = ["start", "--attach", "--interactive", resolved]
        let session: any RuntimeProcessSession = try terminal
            ? terminalProcess(arguments)
            : process(arguments)
        let exitRegistration = UUID()
        try await performAttachedContainerStart(
            requestedID: id,
            runtimeID: resolved,
            context: context,
            exitRegistration: exitRegistration,
            session: session
        )
        return TrackedAppleProcessSession(session: session) { [weak self] exitCode in
            await self?.handleContainerExit(
                ContainerExit(code: exitCode, finishedAt: Date()),
                id: resolved,
                registration: exitRegistration
            )
        }
    }

    func performAttachedContainerStart(
        requestedID: String,
        runtimeID: String,
        context: RuntimeRequestContext,
        exitRegistration: UUID,
        session: any RuntimeProcessSession
    ) async throws {
        containerExitTasks[runtimeID]?.cancel()
        containerExitTasks.removeValue(forKey: runtimeID)
        containerExitRegistrations[runtimeID] = exitRegistration
        containerExits.removeValue(forKey: runtimeID)
        let registration = UUID()
        let task = Task {
            try await self.finishContainerStart(
                requestedID: requestedID,
                runtimeID: runtimeID,
                context: context,
                processGeneration: exitRegistration
            )
        }
        containerStartOperations[runtimeID] = ContainerStartOperation(
            registration: registration,
            kind: .start,
            task: task
        )
        do {
            try await task.value
            finishStartOperation(id: runtimeID, registration: registration)
        } catch {
            await session.cancel()
            await cleanupAttachedContainerStartFailure(
                runtimeID: runtimeID,
                exitRegistration: exitRegistration
            )
            finishStartOperation(id: runtimeID, registration: registration)
            throw error
        }
    }

    internal func cleanupAttachedContainerStartFailure(
        runtimeID: String,
        exitRegistration: UUID
    ) async {
        await portForwarding.stop(
            containerID: runtimeID,
            generation: exitRegistration
        )
        if containerExitRegistrations[runtimeID] == exitRegistration {
            containerExitRegistrations.removeValue(forKey: runtimeID)
        }
    }
}
