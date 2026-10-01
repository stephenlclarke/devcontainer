// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Foundation

public struct DockerBuildCommand: Equatable, Sendable {
    public let context: String
    public let dockerfile: String?
    public let tags: [String]
    public let target: String?
    public let arguments: [String: String]

    static func parse(_ options: inout DockerFrontendArguments) throws -> Self {
        var context: String?
        var dockerfile: String?
        var tags: [String] = []
        var target: String?
        var arguments: [String: String] = [:]
        while let option = options.next() {
            let key = option.split(separator: "=", maxSplits: 1).first.map(String.init)
            switch key {
            case "-f", "--file":
                try requireUnset(dockerfile, option: "file")
                dockerfile = try options.value(for: option)
            case "-t", "--tag": try tags.append(options.value(for: option))
            case "--target":
                try requireUnset(target, option: "target")
                target = try options.value(for: option)
            case "--build-arg":
                let (name, value) = try buildArgument(options.value(for: option))
                arguments[name] = value
            case "--":
                guard context == nil else { throw DockerFrontendError.usage("build accepts one local context") }
                context = options.next()
                try options.requireEnd()
            default:
                guard !option.hasPrefix("-"), context == nil else {
                    throw DockerFrontendError.usage("unsupported build argument: \(option)")
                }
                context = option
            }
        }
        let command = Self(
            context: context ?? "", dockerfile: dockerfile, tags: tags, target: target, arguments: arguments
        )
        try command.validate()
        return command
    }

    private static func requireUnset(_ value: String?, option: String) throws {
        guard value == nil else { throw DockerFrontendError.usage("duplicate build \(option)") }
    }

    private func validate() throws {
        let values = [context] + tags + [dockerfile, target].compactMap(\.self)
            + Array(arguments.keys) + Array(arguments.values)
        guard !context.isEmpty, context != "-", !context.contains("://"),
              !values.contains(where: { $0.contains("\0") })
        else { throw DockerFrontendError.usage("build requires a local directory and NUL-free options") }
    }

    private static func buildArgument(_ value: String) throws -> (String, String) {
        let pair = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
        guard pair.count == 2, !pair[0].isEmpty else {
            throw DockerFrontendError.usage("build arguments require explicit key=value; no environment lookup")
        }
        return (String(pair[0]), String(pair[1]))
    }

    func request(archive: DockerBuildArchive) throws -> DockerHTTPRequest {
        let data = try JSONSerialization.data(withJSONObject: arguments, options: [.sortedKeys])
        var query = [("dockerfile", archive.dockerfile), ("buildargs", String(data: data, encoding: .utf8)!)]
        query += tags.map { ("t", $0) }
        if let target {
            query.append(("target", target))
        }
        let encoded = query.map {
            DockerFrontend.escaped($0.0) + "=" + DockerFrontend.escaped($0.1)
        }.joined(separator: "&")
        return .init(
            method: .post,
            target: "/build?" + encoded,
            headers: .init([.init(name: "Content-Type", value: "application/x-tar")]),
            body: archive.data
        )
    }
}
