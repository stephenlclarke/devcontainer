// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

public extension ContainerSpec {
    /// Resolve the Engine create contract before journalling or launching.
    /// Empty CMD inherits only when the requested entrypoint has zero elements.
    /// An explicit empty entrypoint suppresses image ENTRYPOINT, while [""]
    /// additionally suppresses image CMD before the sentinel is removed.
    func resolvingImageProcess(imageEntrypoint: [String], imageCommand: [String]) throws -> ContainerSpec {
        var resolved = self
        if command.isEmpty, entrypoint.isEmpty {
            resolved.command = imageCommand
        }
        if inheritImageEntrypoint ?? entrypoint.isEmpty {
            resolved.entrypoint = imageEntrypoint
        }
        if resolved.entrypoint == [""] {
            resolved.entrypoint = []
        }
        guard let executable = (resolved.entrypoint + resolved.command).first, !executable.isEmpty else {
            throw DevContainerError(.invalidRequest, message: "Container command/entrypoint is empty")
        }
        resolved.inheritImageEntrypoint = false
        return resolved
    }
}
