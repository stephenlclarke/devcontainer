// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Foundation

public enum DockerFrontendError: Error, Equatable, CustomStringConvertible {
    case usage(String)
    case invalidResponse(String)

    public var description: String {
        switch self {
        case let .usage(message): message
        case let .invalidResponse(message): "invalid Engine response: \(message)"
        }
    }
}

/// The command forms observed in the pinned Dev Containers CLI's parity ledgers.
/// Unsupported operations fail before contacting an engine; there is no external CLI fallback.
public enum DockerFrontendCommand: Equatable, Sendable {
    case clientVersion
    case version(format: String?)
    case info
    case inspect(kind: String, name: String)
    case containers(all: Bool, truncate: Bool, filters: [String: [String]])
    case exec(DockerExecCommand)
    case run(DockerRunCommand)
    case events(DockerEventsCommand)
    case build(DockerBuildCommand)

    public static func parse(_ arguments: [String]) throws -> Self {
        var options = DockerFrontendArguments(arguments)
        guard let command = options.next() else {
            throw DockerFrontendError
                .usage("expected a command; supported: version, info, inspect, ps, exec, run, events, build")
        }
        switch command {
        case "-v", "--version":
            try options.requireEnd()
            return .clientVersion
        case "version":
            let format = try options.format()
            try validateVersionFormat(format)
            return .version(format: format)
        case "info":
            guard try options.format() == "{{json .}}" else {
                throw DockerFrontendError.usage("info requires --format '{{json .}}'")
            }
            return .info
        case "inspect":
            return try inspect(&options, kind: nil)
        case "image", "container":
            return try inspectAlias(command, options: &options)
        case "ps":
            return try containers(&options)
        default:
            return try streaming(command, options: &options)
        }
    }

    private static func streaming(_ command: String, options: inout DockerFrontendArguments) throws -> Self {
        switch command {
        case "exec":
            return try .exec(DockerExecCommand.parse(&options))
        case "run":
            return try .run(DockerRunCommand.parse(&options))
        case "events":
            return try .events(DockerEventsCommand.parse(&options))
        case "build":
            return try .build(DockerBuildCommand.parse(&options))
        default:
            // In particular, a Buildx version probe must fail, allowing the upstream fallback.
            throw DockerFrontendError.usage("unsupported devcontainer-docker command: \(command)")
        }
    }

    private static func inspectAlias(_ command: String, options: inout DockerFrontendArguments) throws -> Self {
        guard options.next() == "inspect" else {
            throw DockerFrontendError.usage("unsupported \(command) subcommand")
        }
        return try inspect(&options, kind: command)
    }

    private static func validateVersionFormat(_ format: String?) throws {
        guard format == nil || format == "{{.Server.Version}}" || format == "{{json .}}" else {
            throw DockerFrontendError.usage("unsupported version format")
        }
    }

    private static func inspect(_ options: inout DockerFrontendArguments, kind initial: String?) throws -> Self {
        var kind = initial
        var name: String?
        while let argument = options.next() {
            if argument == "--" {
                guard name == nil else { throw DockerFrontendError.usage("inspect accepts one name") }
                name = options.next()
                try options.requireEnd()
                break
            } else if argument == "--type" || argument.hasPrefix("--type=") {
                guard kind == nil else { throw DockerFrontendError.usage("duplicate inspect type") }
                kind = try options.value(for: argument)
            } else if argument.hasPrefix("-") || name != nil {
                throw DockerFrontendError.usage("unsupported inspect argument: \(argument)")
            } else {
                name = argument
            }
        }
        guard let kind, ["image", "container"].contains(kind), let name, !name.isEmpty,
              !name.contains("\0")
        else {
            throw DockerFrontendError.usage("inspect requires --type image|container and one nonempty name")
        }
        return .inspect(kind: kind, name: name)
    }

    private static func containers(_ options: inout DockerFrontendArguments) throws -> Self {
        var all = false
        var quiet = false
        var truncate = true
        var filters: [String: [String]] = [:]
        while let argument = options.next() {
            switch argument {
            case "-a", "--all": all = true
            case "-q", "--quiet": quiet = true
            case "-aq", "-qa": all = true; quiet = true
            case "--no-trunc": truncate = false
            case "--filter", "-f": try addFilter(options.value(for: argument), to: &filters)
            default:
                guard argument.hasPrefix("--filter=") else {
                    throw DockerFrontendError.usage("unsupported ps argument: \(argument)")
                }
                try addFilter(options.value(for: argument), to: &filters)
            }
        }
        guard quiet else { throw DockerFrontendError.usage("ps currently requires --quiet") }
        return .containers(all: all, truncate: truncate, filters: filters)
    }

    private static func addFilter(_ value: String, to filters: inout [String: [String]]) throws {
        let fields = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
        guard fields.count == 2, !fields[0].isEmpty, !fields[1].isEmpty, !value.contains("\0") else {
            throw DockerFrontendError.usage("filter requires a nonempty key=value")
        }
        filters[String(fields[0]), default: []].append(String(fields[1]))
    }
}

struct DockerFrontendArguments {
    let arguments: [String]
    private var offset = 0

    init(_ arguments: [String]) {
        self.arguments = arguments
    }

    mutating func next() -> String? {
        guard offset < arguments.count else { return nil }
        defer { offset += 1 }
        return arguments[offset]
    }

    mutating func value(for option: String) throws -> String {
        let value: String? = if let separator = option.firstIndex(of: "=") {
            String(option[option.index(after: separator)...])
        } else {
            next()
        }
        guard let value, !value.isEmpty else {
            throw DockerFrontendError.usage("missing value for \(option)")
        }
        return value
    }

    mutating func requireEnd() throws {
        if let unexpected = next() {
            throw DockerFrontendError.usage("unexpected argument: \(unexpected)")
        }
    }

    mutating func format() throws -> String? {
        guard let option = next() else { return nil }
        guard option == "--format" || option == "-f" || option.hasPrefix("--format=") else {
            throw DockerFrontendError.usage("unsupported argument: \(option)")
        }
        let result = try value(for: option)
        try requireEnd()
        return result
    }
}
