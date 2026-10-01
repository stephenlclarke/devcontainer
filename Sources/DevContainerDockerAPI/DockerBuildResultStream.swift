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

func dockerBuildResultStream(
    _ stream: AsyncThrowingStream<Data, any Error>
) -> AsyncThrowingStream<Data, any Error> {
    let iterator = DockerBuildResultIterator(iterator: stream.makeAsyncIterator())
    return AsyncThrowingStream {
        try await iterator.next()
    }
}

/// Like the wrapped async iterator, next() is consumed serially. Keeping this
/// lazy preserves backpressure and the mutation stream's abandonment handling.
private final class DockerBuildResultIterator: @unchecked Sendable {
    private var iterator: AsyncThrowingStream<Data, any Error>.Iterator
    private var finished = false

    init(iterator: AsyncThrowingStream<Data, any Error>.Iterator) {
        self.iterator = iterator
    }

    func next() async throws -> Data? {
        guard !finished else {
            return nil
        }
        do {
            let value = try await iterator.next()
            try Task.checkCancellation()
            finished = value == nil
            return value
        } catch {
            finished = true
            if error is CancellationError || Task.isCancelled
                || (error as? DevContainerError)?.code == .cancelled
            {
                throw error
            }
            let message = (error as? DevContainerError)?.message ?? String(describing: error)
            var encoded = try DockerJSON.encoder.encode(
                DockerBuildFailure(error: message, errorDetail: .init(message: message))
            )
            encoded.append(0x0A)
            return encoded
        }
    }
}

private struct DockerBuildFailure: Encodable {
    struct Detail: Encodable {
        let message: String
    }

    let error: String
    let errorDetail: Detail
}
