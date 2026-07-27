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

enum CLIPaths {
    static var configuration: String {
        let root = ProcessInfo.processInfo.environment["XDG_CONFIG_HOME"]
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config", isDirectory: true)
        return root
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("config.toml")
            .path
    }

    static var socket: String {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("docker.sock")
            .path
    }

    static var stateDatabase: String {
        let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return applicationSupport
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("state.sqlite")
            .path
    }

    static var containerExecutable: String {
        if let configured = ProcessInfo.processInfo.environment["DEVCONTAINER_CONTAINER_BIN"] {
            return configured
        }
        for candidate in [
            "/opt/homebrew/bin/container",
            "/usr/local/bin/container",
            "/usr/bin/container"
        ] where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return "/opt/homebrew/bin/container"
    }

    static var safeEnvironment: [String: String] {
        let source = ProcessInfo.processInfo.environment
        return Dictionary(uniqueKeysWithValues: [
            "CONTAINER_APP_ROOT",
            "CONTAINER_HOST",
            "CONTAINER_INSTALL_ROOT",
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "TMPDIR",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME"
        ].compactMap { key in
            source[key].map { (key, $0) }
        })
    }
}
