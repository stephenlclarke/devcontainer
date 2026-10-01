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
    public let user: String?
    public let mounts: [DockerRunMount]
    public let ports: [DockerRunPort]
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
            entrypoint: values.entrypoint, user: values.user, mounts: values.mounts, ports: values.ports,
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
        fields["User"] = user
        if !ports.isEmpty {
            let bindings = Dictionary(grouping: ports, by: \.key).mapValues { $0.map(\.fields) }
            fields["HostConfig"] = ["Mounts": mounts.map(\.fields), "PortBindings": bindings]
            fields["ExposedPorts"] = bindings.mapValues { _ in [String: String]() }
        }
        return try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    }
}

/// Explicit IPv4 TCP publications; ranges, dynamic ports and IPv6 need their own oracle.
public struct DockerRunPort: Equatable, Sendable {
    public let hostAddress: String
    public let hostPort: UInt16
    public let containerPort: UInt16

    var key: String {
        "\(containerPort)/tcp"
    }

    var fields: [String: String] {
        ["HostIp": hostAddress, "HostPort": String(hostPort)]
    }

    static func parse(_ value: String) throws -> Self {
        let publication = value.hasSuffix("/tcp") ? String(value.dropLast(4)) : value
        let parts = publication.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 3 else {
            throw DockerFrontendError.usage("publish requires explicit IPv4:host-port:container-port[/tcp]")
        }
        let address = parts[0].split(separator: ".", omittingEmptySubsequences: false)
        guard address.count == 4, address.allSatisfy({ UInt8($0).map { String($0) } == String($0) }),
              let host = UInt16(parts[1]), host > 0, String(host) == parts[1],
              let container = UInt16(parts[2]), container > 0, String(container) == parts[2]
        else { throw DockerFrontendError.usage("publish requires canonical IPv4 and nonzero TCP ports in 1...65535") }
        return Self(hostAddress: String(parts[0]), hostPort: host, containerPort: container)
    }
}

public struct DockerRunMount: Equatable, Sendable {
    public enum Kind: String, Sendable {
        case bind, volume
    }

    public let source: String
    public let target: String
    public let readOnly: Bool
    public let kind: Kind

    init(source: String, target: String, readOnly: Bool, kind: Kind = .bind) {
        self.source = source
        self.target = target
        self.readOnly = readOnly
        self.kind = kind
    }

    var fields: [String: Any] {
        ["Type": kind.rawValue, "Source": source, "Target": target, "ReadOnly": readOnly]
    }

    static func parse(_ value: String) throws -> Self {
        let fields = try parseFields(value)
        guard let kind = fields["type"].flatMap(Kind.init(rawValue:)), let source = fields["source"],
              let target = fields["target"], target.hasPrefix("/"),
              fields["readonly"].map({ ["true", "false"].contains($0) }) ?? true
        else {
            throw DockerFrontendError.usage("run mount requires explicit bind/volume type, source and absolute target")
        }
        let validSource = kind == .bind ? source.hasPrefix("/") :
            source.range(of: "[a-zA-Z0-9][a-zA-Z0-9_.-]+", options: .regularExpression) ==
            source.startIndex ..< source.endIndex
        guard validSource else {
            throw DockerFrontendError.usage("run mount requires an absolute bind source or a valid named volume")
        }
        return Self(source: source, target: target, readOnly: fields["readonly"] == "true", kind: kind)
    }

    private static func parseFields(_ value: String) throws -> [String: String] {
        // Anonymous volumes, quoted CSV and propagation need their own oracle.
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
            guard key == "readonly" || pair.count == 2 else {
                throw DockerFrontendError.usage("mount type, source and target require explicit values")
            }
            fields[key] = pair.count == 2 ? String(pair[1]) : "true"
        }
        return fields
    }
}

private struct RunOptions {
    var signalProxyDisabled = false
    var environment: [String] = []
    var labels: [String: String] = [:]
    var entrypoint: String?
    var user: String?
    var mounts: [DockerRunMount] = []
    var ports: [DockerRunPort] = []
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
            try addAttachment(options.value(for: argument))
        case "-e", "--env":
            let value = try options.value(for: argument)
            _ = try keyValue(value)
            environment.append(value)
        case "-l", "--label":
            let pair = try keyValue(options.value(for: argument))
            labels[pair.0] = pair.1
        case "--entrypoint":
            try setEntrypoint(options.value(for: argument))
        case "-u", "--user":
            try setUser(options.value(for: argument))
        case "--mount":
            try mounts.append(DockerRunMount.parse(options.value(for: argument)))
        case "-p", "--publish":
            try ports.append(DockerRunPort.parse(options.value(for: argument)))
        default: throw DockerFrontendError.usage("unsupported run option: \(argument)")
        }
    }

    private mutating func addAttachment(_ value: String) throws {
        let channel = value.lowercased()
        guard ["stdout", "stderr"].contains(channel) else {
            throw DockerFrontendError.usage("run currently supports only stdout/stderr attachment")
        }
        attachments.insert(channel)
    }

    private mutating func setUser(_ value: String) throws {
        guard user == nil, !value.isEmpty, !value.contains("\0") else {
            throw DockerFrontendError.usage("run requires one nonempty user without NUL bytes")
        }
        user = value
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
