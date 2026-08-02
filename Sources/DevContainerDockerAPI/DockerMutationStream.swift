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

import DevContainerCore
import DevContainerModel
import Foundation

func coordinatedMutationStream(
    _ stream: AsyncThrowingStream<Data, any Error>,
    coordinator: ProjectCoordinator,
    session: ProjectMutationSession
) -> AsyncThrowingStream<Data, any Error> {
    let completion = DockerMutationStreamCompletion(
        coordinator: coordinator,
        session: session
    )
    let iterator = DockerMutationStreamIterator(
        iterator: stream.makeAsyncIterator(),
        completion: completion
    )
    return AsyncThrowingStream {
        try await iterator.next()
    }
}

private final class DockerMutationStreamIterator: @unchecked Sendable {
    private var iterator: AsyncThrowingStream<Data, any Error>.Iterator
    private let completion: DockerMutationStreamCompletion

    init(
        iterator: AsyncThrowingStream<Data, any Error>.Iterator,
        completion: DockerMutationStreamCompletion
    ) {
        self.iterator = iterator
        self.completion = completion
    }

    func next() async throws -> Data? {
        do {
            try Task.checkCancellation()
            let value = try await iterator.next()
            try Task.checkCancellation()
            guard let value else {
                try await completion.commit()
                return nil
            }
            return value
        } catch {
            await completion.fail(error)
            throw error
        }
    }
}

private final class DockerMutationStreamCompletion: @unchecked Sendable {
    private let coordinator: ProjectCoordinator
    private let session: ProjectMutationSession
    private let lock = NSLock()
    private var completed = false

    init(
        coordinator: ProjectCoordinator,
        session: ProjectMutationSession
    ) {
        self.coordinator = coordinator
        self.session = session
    }

    deinit {
        guard claimCompletion() else {
            return
        }
        let coordinator = coordinator
        let session = session
        Task.detached {
            await coordinator.failMutation(
                session,
                errorCode: DevContainerErrorCode.cancelled.rawValue
            )
        }
    }

    func commit() async throws {
        guard claimCompletion() else {
            return
        }
        try await coordinator.commitMutation(session)
    }

    func fail(_ error: any Error) async {
        guard claimCompletion() else {
            return
        }
        let errorCode: String? = if error is CancellationError {
            DevContainerErrorCode.cancelled.rawValue
        } else {
            (error as? DevContainerError)?.code.rawValue
        }
        await coordinator.failMutation(session, errorCode: errorCode)
    }

    private func claimCompletion() -> Bool {
        lock.withLock {
            guard !completed else {
                return false
            }
            completed = true
            return true
        }
    }
}
