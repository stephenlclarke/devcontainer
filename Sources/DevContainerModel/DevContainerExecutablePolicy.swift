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

/// Prevents runtime selection from escaping the Docker-less product boundary.
public enum DevContainerExecutablePolicy {
    private static let forbiddenRuntimeNames: Set<String> = [
        "colima",
        "docker",
        "docker-buildx",
        "docker-compose"
    ]

    /// Rejects a Docker or Colima executable before any product process launch.
    public static func requireDockerless(_ path: String, name: String) throws {
        let executable = URL(fileURLWithPath: path).standardizedFileURL
        let resolved = executable.resolvingSymlinksInPath()
        let names = [executable.lastPathComponent, resolved.lastPathComponent]
            .map { $0.lowercased() }
        guard names.allSatisfy({ !forbiddenRuntimeNames.contains($0) }) else {
            throw DevContainerError(
                .invalidRequest,
                message: "\(name) cannot select Docker or Colima in the Docker-less product"
            )
        }
    }
}
