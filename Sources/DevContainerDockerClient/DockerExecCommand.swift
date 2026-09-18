// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Foundation

public struct DockerExecCommand: Equatable, Sendable {
    public let container: String
    public let command: [String]
    public let interactive: Bool
    public let user: String?
    public let environment: [String]
    public let workingDirectory: String?

    static func parse(_ options: inout DockerFrontendArguments) throws -> Self {
        var values = ExecOptions()
        var container: String?
        while let argument = options.next() {
            if argument == "--" {
                container = options.next(); break
            }
            if !argument.hasPrefix("-") {
                container = argument; break
            }
            try values.consume(argument, options: &options)
        }
        var command: [String] = []
        while let argument = options.next() {
            command.append(argument)
        }
        guard let container, !container.isEmpty, let executable = command.first, !executable.isEmpty,
              ([container] + command + values.environment + [values.user, values.workingDirectory].compactMap(\.self))
              .allSatisfy({ !$0.contains("\0") })
        else {
            throw DockerFrontendError.usage("exec requires a container and command without NUL bytes")
        }
        return Self(
            container: container,
            command: command,
            interactive: values.interactive,
            user: values.user,
            environment: values.environment,
            workingDirectory: values.workingDirectory
        )
    }

    func createBody() throws -> Data {
        var fields: [String: Any] = [
            "AttachStdin": interactive, "AttachStdout": true, "AttachStderr": true,
            "Tty": false, "Cmd": command, "Env": environment
        ]
        fields["User"] = user
        fields["WorkingDir"] = workingDirectory
        return try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    }
}

private struct ExecOptions {
    var interactive = false
    var user: String?
    var environment: [String] = []
    var workingDirectory: String?

    mutating func consume(_ argument: String, options: inout DockerFrontendArguments) throws {
        switch argument.split(separator: "=", maxSplits: 1).first.map(String.init) {
        case "-i", "--interactive":
            guard !argument.contains("=") else { throw DockerFrontendError.usage("unsupported exec boolean flag") }
            interactive = true
        case "-u", "--user":
            guard user == nil else { throw DockerFrontendError.usage("duplicate exec user") }
            user = try options.value(for: argument)
        case "-e", "--env":
            let value = try options.value(for: argument)
            guard let separator = value.firstIndex(of: "="), separator != value.startIndex else {
                throw DockerFrontendError.usage("exec environment requires NAME=value")
            }
            environment.append(value)
        case "-w", "--workdir":
            guard workingDirectory == nil else { throw DockerFrontendError.usage("duplicate exec workdir") }
            workingDirectory = try options.value(for: argument)
        default:
            throw DockerFrontendError.usage("unsupported exec option: \(argument)")
        }
    }
}
