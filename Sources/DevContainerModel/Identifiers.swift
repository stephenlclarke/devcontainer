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

public protocol StringIdentifier: Codable, CustomStringConvertible, Hashable, RawRepresentable, Sendable
    where RawValue == String {}

public extension StringIdentifier {
    var description: String {
        rawValue
    }
}

public struct ProjectKey: StringIdentifier {
    public let rawValue: String

    public init(rawValue: String) {
        self.rawValue = rawValue
    }
}

public struct RuntimeID: StringIdentifier {
    public let rawValue: String

    public init(rawValue: String) {
        self.rawValue = rawValue
    }
}

public struct DockerID: StringIdentifier {
    public let rawValue: String

    public init(rawValue: String) {
        self.rawValue = rawValue
    }
}

public struct OperationID: StringIdentifier {
    public let rawValue: String

    public init(rawValue: String) {
        self.rawValue = rawValue
    }

    public static func random() -> OperationID {
        OperationID(rawValue: UUID().uuidString.lowercased())
    }
}

public struct ExecID: StringIdentifier {
    public let rawValue: String

    public init(rawValue: String) {
        self.rawValue = rawValue
    }

    public static func random() -> ExecID {
        ExecID(rawValue: UUID().uuidString.lowercased())
    }
}
