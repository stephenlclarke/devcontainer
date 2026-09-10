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
import Foundation

enum CLIPaths {
    static var configuration: String {
        DevContainerPathDefaults.configuration
    }

    static var socket: String {
        DevContainerPathDefaults.socket
    }

    static var stateDatabase: String {
        DevContainerPathDefaults.stateDatabase
    }

    static var containerExecutable: String {
        ProcessInfo.processInfo.environment["DEVCONTAINER_CONTAINER_BIN"]
            ?? DevContainerPathDefaults.containerExecutable
    }

    static var safeEnvironment: [String: String] {
        let source = ProcessInfo.processInfo.environment
        return Dictionary(uniqueKeysWithValues: [
            "CONTAINER_APP_ROOT",
            "CONTAINER_HOST",
            "CONTAINER_INSTALL_ROOT",
            "CONTAINER_SERVICE_NAMESPACE",
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
