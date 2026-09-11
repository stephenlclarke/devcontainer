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
        "com.docker.cli",
        "docker",
        "docker-buildx",
        "docker-compose",
        "dockerd",
        "nerdctl",
        "podman"
    ]

    /// Rejects a non-Apple container runtime before any product process launch.
    public static func requireDockerless(_ path: String, name: String) throws {
        let executable = URL(fileURLWithPath: path).standardizedFileURL
        let resolved = executable.resolvingSymlinksInPath()
        let names = [executable.lastPathComponent, resolved.lastPathComponent]
            .map { $0.lowercased() }
        guard names.allSatisfy({ !forbiddenRuntimeNames.contains($0) }) else {
            throw DevContainerError(
                .invalidRequest,
                message: "\(name) cannot select a non-Apple runtime in the Docker-less product"
            )
        }
    }

    /// Requires the selected runtime CLI to be an Apple Container distribution.
    public static func requireAppleContainer(_ path: String, name: String) throws {
        try requireDockerless(path, name: name)
        let executable = URL(fileURLWithPath: path).standardizedFileURL
        let resolved = executable.resolvingSymlinksInPath()
        guard executable.lastPathComponent == "container",
              resolved.lastPathComponent == "container"
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "\(name) must select an Apple Container distribution executable"
            )
        }
    }

    /// Requires the selected multi-service CLI to be native container-compose.
    public static func requireNativeCompose(_ path: String, name: String) throws {
        try requireDockerless(path, name: name)
        let executable = URL(fileURLWithPath: path).standardizedFileURL
        let resolved = executable.resolvingSymlinksInPath()
        let allowed = Set(["compose", "container-compose"])
        guard allowed.contains(executable.lastPathComponent),
              allowed.contains(resolved.lastPathComponent)
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "\(name) must select a native container-compose executable"
            )
        }
    }
}
