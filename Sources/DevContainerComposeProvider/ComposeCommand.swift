//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

import Darwin
import DevContainerModel
import Foundation

private struct ParsedComposeArguments {
    var command: String?
    var commandIndex: Int?
    var projectName: String?
    var projectDirectory: String?
    var files: [String] = []
    var preventsMutation = false
    var configurationArguments: [String] = []

    mutating func consumeValueOption(_ option: String, value: String) {
        switch option {
        case "-p", "--project-name":
            projectName = value
        case "--project-directory":
            projectDirectory = value
        case "-f", "--file":
            files.append(value)
        default:
            break
        }
    }

    mutating func consumeInlineOption(_ argument: String) throws -> Bool {
        if argument.hasPrefix("--project-name=") {
            projectName = String(argument.dropFirst("--project-name=".count))
        } else if argument.hasPrefix("-p=") {
            projectName = String(argument.dropFirst("-p=".count))
        } else if argument.hasPrefix("--project-directory=") {
            projectDirectory = String(argument.dropFirst("--project-directory=".count))
        } else if argument.hasPrefix("--file=") {
            files.append(String(argument.dropFirst("--file=".count)))
        } else if argument.hasPrefix("-f=") {
            files.append(String(argument.dropFirst("-f=".count)))
        } else if Self.ignoredInlineValueOptions.contains(where: argument.hasPrefix) {
            return true
        } else if let option = Self.booleanOptions.first(where: {
            argument.hasPrefix("\($0)=")
        }) {
            try consumeBooleanOption(option, argument: argument)
            return true
        } else {
            return false
        }
        return true
    }

    private mutating func consumeBooleanOption(
        _ option: String,
        argument: String
    ) throws {
        let value = String(argument.dropFirst(option.count + 1))
        let boolean: Bool
        switch value.lowercased() {
        case "1", "t", "true":
            boolean = true
        case "0", "f", "false":
            boolean = false
        default:
            throw DevContainerError(
                .invalidRequest,
                message: "Compose option \(option) requires a Boolean value"
            )
        }
        if boolean, ["--dry-run", "--help", "--version"].contains(option) {
            preventsMutation = true
        }
    }

    private static let ignoredInlineValueOptions = [
        "--ansi=",
        "--env-file=",
        "--parallel=",
        "--profile=",
        "--progress="
    ]

    private static let booleanOptions = [
        "--all-resources",
        "--compatibility",
        "--dry-run",
        "--help",
        "--verbose",
        "--version"
    ]
}

public struct ComposeCommandEnvelope: Equatable, Sendable {
    public var command: String?
    public var projectName: String?
    public var projectDirectory: String?
    public var files: [String]
    public var mutating: Bool
    public var removesProject: Bool
    public var configurationArguments: [String]

    public init(arguments: [String]) throws {
        let parsed = try Self.parse(arguments)
        command = parsed.command
        projectName = parsed.projectName
        projectDirectory = parsed.projectDirectory
        files = parsed.files
        mutating = Self.mutatingCommands.contains(parsed.command ?? "")
            && !parsed.preventsMutation
        removesProject = mutating
            && (
                parsed.command == "down"
                    || (
                        parsed.command == "wait"
                            && Self.commandFlag(
                                "--down-project",
                                isEnabledIn: arguments,
                                after: parsed.commandIndex
                            )
                    )
            )
        configurationArguments =
            parsed.configurationArguments + ["config", "--format", "json"]
    }

    public func projectKey(userID: uid_t = getuid()) -> ProjectKey? {
        guard let projectName, !projectName.isEmpty else {
            return nil
        }
        return ProjectKey(rawValue: "\(userID):\(projectName.lowercased())")
    }

    private static func parse(_ arguments: [String]) throws -> ParsedComposeArguments {
        var parsed = ParsedComposeArguments()
        var index = 0
        var optionsEnded = false

        while index < arguments.count {
            let argument = arguments[index]
            if optionsEnded {
                parsed.command = argument
                parsed.commandIndex = index
                break
            }
            if argument == "--" {
                optionsEnded = true
                index += 1
                continue
            }
            if try consumeGlobalOption(
                from: arguments,
                at: &index,
                into: &parsed
            ) {
                continue
            }
            if argument.hasPrefix("-") {
                throw DevContainerError(
                    .invalidRequest,
                    message: "unsupported Compose global option \(argument)"
                )
            }
            parsed.command = argument
            parsed.commandIndex = index
            try parseInheritedOptions(
                arguments,
                startingAt: index + 1,
                into: &parsed
            )
            break
        }
        return parsed
    }

    private static func consumeGlobalOption(
        from arguments: [String],
        at index: inout Int,
        into parsed: inout ParsedComposeArguments
    ) throws -> Bool {
        let argument = arguments[index]
        if valueOptions.contains(argument) {
            guard index + 1 < arguments.count else {
                throw DevContainerError(
                    .invalidRequest,
                    message: "Compose option \(argument) requires a value"
                )
            }
            let value = arguments[index + 1]
            parsed.consumeValueOption(argument, value: value)
            parsed.configurationArguments.append(contentsOf: [argument, value])
            index += 2
            return true
        }
        if try parsed.consumeInlineOption(argument) {
            parsed.configurationArguments.append(argument)
            index += 1
            return true
        }
        guard flagOptions.contains(argument) else {
            return false
        }
        if ["--dry-run", "--help", "--version", "-h"].contains(argument) {
            parsed.preventsMutation = true
        }
        parsed.configurationArguments.append(argument)
        index += 1
        return true
    }

    private static func parseInheritedOptions(
        _ arguments: [String],
        startingAt startIndex: Int,
        into parsed: inout ParsedComposeArguments
    ) throws {
        var index = startIndex
        while index < arguments.count {
            let argument = arguments[index]
            if argument == "--" {
                return
            }
            if try consumeGlobalOption(
                from: arguments,
                at: &index,
                into: &parsed
            ) {
                continue
            }
            index += 1
        }
    }

    private static func commandFlag(
        _ option: String,
        isEnabledIn arguments: [String],
        after commandIndex: Int?
    ) -> Bool {
        guard let commandIndex else {
            return false
        }
        var enabled = false
        for argument in arguments.dropFirst(commandIndex + 1) {
            if argument == "--" {
                break
            }
            if argument == option {
                enabled = true
                continue
            }
            guard argument.hasPrefix("\(option)=") else {
                continue
            }
            let value = argument.dropFirst(option.count + 1).lowercased()
            if ["1", "t", "true"].contains(value) {
                enabled = true
            } else if ["0", "f", "false"].contains(value) {
                enabled = false
            }
        }
        return enabled
    }

    private static let valueOptions: Set<String> = [
        "--ansi",
        "--env-file",
        "--file",
        "--parallel",
        "--profile",
        "--progress",
        "--project-directory",
        "--project-name",
        "-f",
        "-p"
    ]

    private static let flagOptions: Set<String> = [
        "--all-resources",
        "--compatibility",
        "--dry-run",
        "--help",
        "--verbose",
        "--version",
        "-h"
    ]

    private static let mutatingCommands: Set<String> = [
        "build",
        "commit",
        "cp",
        "create",
        "down",
        "exec",
        "kill",
        "pause",
        "publish",
        "pull",
        "push",
        "restart",
        "rm",
        "run",
        "scale",
        "start",
        "stop",
        "unpause",
        "up",
        "wait",
        "watch"
    ]
}

/// Resolves an upstream Docker Compose invocation without depending on a
/// user's Docker CLI plug-in configuration.
public struct DockerComposeCommand: Equatable, Sendable {
    public let executable: URL
    public let arguments: [String]

    public init(
        arguments: [String],
        docker: URL,
        standaloneCompose: URL?
    ) {
        if let standaloneCompose {
            executable = standaloneCompose
            self.arguments = arguments
        } else {
            executable = docker
            self.arguments = ["compose"] + arguments
        }
    }
}
