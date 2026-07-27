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

import Foundation

public enum DevContainerErrorCode: String, Codable, Sendable {
    case authentication
    case build
    case cancelled
    case conflict
    case deadlineExceeded
    case invalidRequest
    case notFound
    case providerProtocolMismatch
    case runtimeUnavailable
    case stateCorruption
    case unsupportedCapability
}

public struct DevContainerError: Error, Codable, CustomStringConvertible, Equatable, Sendable {
    public var code: DevContainerErrorCode
    public var message: String
    public var correlationID: String?

    public init(
        _ code: DevContainerErrorCode,
        message: String,
        correlationID: String? = nil
    ) {
        self.code = code
        self.message = message
        self.correlationID = correlationID
    }

    public var description: String {
        if let correlationID {
            return "\(message) [correlation: \(correlationID)]"
        }
        return message
    }
}
