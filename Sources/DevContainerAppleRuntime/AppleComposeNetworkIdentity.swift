// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel

extension AppleContainerRuntime {
    /// Native Compose mirrors its identity into Docker labels. Accept neither
    /// a partial identity nor disagreeing mirrors as authority for an alias.
    static func nativeComposeServiceName(labels: [String: String]) -> String? {
        let native = "com.apple.container.compose."
        let docker = "com.docker.compose."
        guard labels[native + "version"] == "1",
              let project = labels[native + "project"],
              !project.isEmpty,
              project == labels[docker + "project"],
              let service = labels[native + "service"],
              service == labels[docker + "service"],
              labels[native + "oneoff"] == "false",
              labels[docker + "oneoff"] == "false",
              isSafeHostName(service)
        else {
            return nil
        }
        return service
    }
}
