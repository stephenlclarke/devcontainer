// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Foundation

public struct DockerFrontendInvocation: Equatable, Sendable {
    public let command: DockerFrontendCommand
    public let socketPath: String?

    public init(arguments: [String], environment: [String: String]) throws {
        var options = DockerFrontendArguments(arguments)
        var host: String?
        var commandArguments: [String] = []
        while let argument = options.next() {
            if argument == "-H" || argument == "--host" || argument.hasPrefix("--host=") {
                guard host == nil else { throw DockerFrontendError.usage("multiple engine endpoints are unsupported") }
                host = try options.value(for: argument)
            } else {
                commandArguments.append(argument)
                while let remainder = options.next() {
                    commandArguments.append(remainder)
                }
            }
        }
        command = try DockerFrontendCommand.parse(commandArguments)
        if command == .clientVersion {
            // A local version probe neither reads configuration nor requires a running engine.
            socketPath = nil
            return
        }
        guard host != nil || environment["DOCKER_CONTEXT", default: ""].isEmpty else {
            throw DockerFrontendError.usage("Docker contexts are unsupported; select an explicit local Unix endpoint")
        }
        if let selected = host ?? environment["DOCKER_HOST"] {
            guard selected.hasPrefix("unix:///") else {
                throw DockerFrontendError.usage("only a local unix:/// engine endpoint is supported")
            }
            let path = String(selected.dropFirst("unix://".count))
            guard !path.contains("\0"), path.utf8.count < 104, path != "/",
                  !path.split(separator: "/").contains(where: { $0 == "." || $0 == ".." })
            else {
                throw DockerFrontendError.usage("invalid local Unix socket path")
            }
            socketPath = path
        } else {
            socketPath = nil
        }
    }
}
