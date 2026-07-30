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

struct EngineServerLimits: Sendable {
    static let production = EngineServerLimits(
        maximumRequestBodyBytes: 1_073_741_824,
        maximumBufferedRequestBodyBytes: 1_073_741_824,
        maximumPendingRequests: 32
    )

    let maximumRequestBodyBytes: Int
    let maximumBufferedRequestBodyBytes: Int
    let maximumPendingRequests: Int

    init(
        maximumRequestBodyBytes: Int,
        maximumBufferedRequestBodyBytes: Int,
        maximumPendingRequests: Int
    ) {
        precondition(maximumRequestBodyBytes > 0)
        precondition(maximumBufferedRequestBodyBytes >= maximumRequestBodyBytes)
        precondition(maximumPendingRequests > 0)
        self.maximumRequestBodyBytes = maximumRequestBodyBytes
        self.maximumBufferedRequestBodyBytes = maximumBufferedRequestBodyBytes
        self.maximumPendingRequests = maximumPendingRequests
    }
}
