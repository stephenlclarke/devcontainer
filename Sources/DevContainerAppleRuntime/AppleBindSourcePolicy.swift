// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import DevContainerModel
import Foundation

enum AppleBindSourcePolicy {
    static func validationSource(_ mount: RuntimeMount) throws -> String {
        guard mount.type == .bind, mount.createSourceDirectory == true else { return mount.source }
        guard mount.source.hasPrefix("/"), !mount.source.contains(","),
              !mount.source.contains("="), !mount.source.contains("\0")
        else {
            throw DevContainerError(.invalidRequest, message: "Bind source must be an absolute, representable path")
        }
        // Validate the destination and native mount syntax without creating
        // caller-owned directories during preflight. Creation happens only
        // after the container configuration has passed validation.
        return FileManager.default.fileExists(atPath: mount.source) ? mount.source : "/"
    }

    static func prepare(_ mount: RuntimeMount) throws {
        guard mount.type == .bind, mount.createSourceDirectory == true else { return }
        let source = try validationSource(mount)
        _ = try Parser.mounts([
            "type=bind,source=\(source),target=\(mount.destination)" + (mount.readOnly ? ",readonly" : "")
        ])
        if FileManager.default.fileExists(atPath: mount.source) { return }
        // These directories belong to the caller, not the container. As with
        // Docker bind-create-src, removal must never delete their contents.
        try FileManager.default.createDirectory(
            atPath: mount.source, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o755]
        )
    }
}
