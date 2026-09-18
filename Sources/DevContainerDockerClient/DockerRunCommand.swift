// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Foundation

/// Foreground, output-only startup used by the pinned upstream CLI.
/// Unsupported options fail before create; no requested setting is silently discarded.
public struct DockerRunCommand: Equatable, Sendable {
    public let image: String
    public let command: [String]
    public let environment: [String]
    public let labels: [String: String]
    public let entrypoint: String?
    public let mounts: [DockerRunMount]
    public let standardOutput: Bool
    public let standardError: Bool

    static func parse(_ options: inout DockerFrontendArguments) throws -> Self {
        var values = RunOptions()
        var image: String?
        while let argument = options.next() {
            if argument == "--" {
                image = options.next(); break
            }
            if !argument.hasPrefix("-") {
                image = argument; break
            }
            try values.consume(argument, options: &options)
        }
        var command: [String] = []
        while let argument = options.next() {
            command.append(argument)
        }
        guard let image, !image.isEmpty,
              ([image] + command).allSatisfy({ !$0.contains("\0") }),
              values.signalProxyDisabled
        else { throw DockerFrontendError.usage("run requires --sig-proxy=false and a nonempty image without NUL bytes")
        }
        return Self(
            image: image, command: command, environment: values.environment, labels: values.labels,
            entrypoint: values.entrypoint, mounts: values.mounts,
            standardOutput: values.attachments.isEmpty || values.attachments.contains("stdout"),
            standardError: values.attachments.isEmpty || values.attachments.contains("stderr")
        )
    }

    func createBody() throws -> Data {
        var fields: [String: Any] = [
            "Image": image, "Env": environment, "Labels": labels,
            "AttachStdin": false, "OpenStdin": false, "Tty": false,
            "AttachStdout": standardOutput, "AttachStderr": standardError,
            "HostConfig": ["Mounts": mounts.map(\.fields)]
        ]
        if !command.isEmpty {
            fields["Cmd"] = command
        }
        if let entrypoint {
            fields["Entrypoint"] = [entrypoint]
        }
        return try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    }
}

public struct DockerRunMount: Equatable, Sendable {
    public let source: String
    public let target: String
    public let readOnly: Bool

    var fields: [String: Any] {
        ["Type": "bind", "Source": source, "Target": target, "ReadOnly": readOnly]
    }

    static func parse(_ value: String) throws -> Self {
        // Quoted CSV, volume and propagation semantics need their own oracle.
        guard !value.contains("\""), !value.contains("\0") else {
            throw DockerFrontendError.usage("unsupported quoted or NUL-containing mount")
        }
        var fields: [String: String] = [:]
        for field in value.split(separator: ",", omittingEmptySubsequences: false) {
            let pair = field.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard let name = pair.first, !name.isEmpty else {
                throw DockerFrontendError.usage("empty mount field")
            }
            let key = switch name {
            case "src", "source": "source"
            case "dst", "destination", "target": "target"
            case "ro", "readonly": "readonly"
            default: String(name)
            }
            guard ["type", "source", "target", "readonly"].contains(key), fields[key] == nil else {
                throw DockerFrontendError.usage("unsupported or duplicate mount field")
            }
            fields[key] = pair.count == 2 ? String(pair[1]) : "true"
        }
        guard fields["type"] == "bind", let source = fields["source"], source.hasPrefix("/"),
              let target = fields["target"], target.hasPrefix("/"),
              fields["readonly"].map({ ["true", "false"].contains($0) }) ?? true
        else { throw DockerFrontendError.usage("run mount requires absolute source and target with type=bind") }
        return Self(source: source, target: target, readOnly: fields["readonly"] == "true")
    }
}

private struct RunOptions {
    var signalProxyDisabled = false
    var environment: [String] = []
    var labels: [String: String] = [:]
    var entrypoint: String?
    var mounts: [DockerRunMount] = []
    var attachments: Set<String> = []

    mutating func consume(_ argument: String, options: inout DockerFrontendArguments) throws {
        let key = argument.split(separator: "=", maxSplits: 1).first.map(String.init)
        switch key {
        case "--sig-proxy":
            guard try options.value(for: argument) == "false" else {
                throw DockerFrontendError.usage("run currently requires --sig-proxy=false")
            }
            signalProxyDisabled = true
        case "-a", "--attach":
            let value = try options.value(for: argument).lowercased()
            guard ["stdout", "stderr"].contains(value) else {
                throw DockerFrontendError.usage("run currently supports only stdout/stderr attachment")
            }
            attachments.insert(value)
        case "-e", "--env":
            let value = try options.value(for: argument)
            _ = try keyValue(value)
            environment.append(value)
        case "-l", "--label":
            let pair = try keyValue(options.value(for: argument))
            labels[pair.0] = pair.1
        case "--entrypoint":
            try setEntrypoint(options.value(for: argument))
        case "--mount":
            try mounts.append(DockerRunMount.parse(options.value(for: argument)))
        default: throw DockerFrontendError.usage("unsupported run option: \(argument)")
        }
    }

    private mutating func setEntrypoint(_ value: String) throws {
        guard entrypoint == nil else { throw DockerFrontendError.usage("duplicate entrypoint") }
        guard !value.contains("\0") else { throw DockerFrontendError.usage("invalid entrypoint") }
        entrypoint = value
    }

    private func keyValue(_ value: String) throws -> (String, String) {
        guard let separator = value.firstIndex(of: "="), separator != value.startIndex,
              !value.contains("\0")
        else { throw DockerFrontendError.usage("run environment and labels require nonempty KEY=value without NUL") }
        return (String(value[..<separator]), String(value[value.index(after: separator)...]))
    }
}
