// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel

extension AppleContainerRuntime {
    /// Docker labels may be projected only at the HTTP boundary. The complete
    /// native identity is authoritative; any persisted mirrors must agree.
    static func nativeComposeServiceName(labels: [String: String]) -> String? {
        let native = "com.apple.container.compose."
        let docker = "com.docker.compose."
        guard labels[native + "version"] == "1",
              let project = labels[native + "project"],
              !project.isEmpty,
              labels[docker + "project"].map({ $0 == project }) ?? true,
              let service = labels[native + "service"],
              labels[docker + "service"].map({ $0 == service }) ?? true,
              labels[native + "oneoff"] == "false",
              labels[docker + "oneoff"].map({ $0 == "false" }) ?? true,
              isSafeHostName(service)
        else {
            return nil
        }
        return service
    }
}
