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

/// Immutable provenance embedded by SwiftPM from the Makefile-owned version.
public struct BuildInfo: Codable, Equatable, Sendable {
    public let version: String
    public let source: String
    public let commit: String
    public let lane: String
    public let buildType: String
    public let architecture: String
    public let containerDistribution: String
    public let provider: String

    public init(
        version: String,
        source: String = "stephenlclarke/devcontainer",
        commit: String,
        lane: String,
        buildType: String,
        architecture: String,
        containerDistribution: String = "apple",
        provider: String = "none"
    ) {
        self.version = version
        self.source = source
        self.commit = commit
        self.lane = lane
        self.buildType = buildType
        self.architecture = architecture
        self.containerDistribution = containerDistribution
        self.provider = provider
    }

    public static let current: BuildInfo = {
        let lane = GeneratedBuildIdentity.lane
        return BuildInfo(
            version: GeneratedBuildIdentity.version,
            commit: GeneratedBuildIdentity.commit,
            lane: lane,
            buildType: lane == "development" ? "development" : "release",
            architecture: compiledArchitecture
        )
    }()

    private static var compiledArchitecture: String {
        #if arch(arm64)
            "arm64"
        #elseif arch(x86_64)
            "x86_64"
        #else
            "unknown"
        #endif
    }
}
