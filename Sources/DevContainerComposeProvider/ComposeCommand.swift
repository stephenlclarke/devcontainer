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
    var projectName: String?
    var projectDirectory: String?
    var files: [String] = []

    mutating func consumeValueOption(_ option: String, value: String) {
        switch option {
        case "-p", "--project-name":
            projectName = value
        case "--project-directory":
            projectDirectory = value
        default:
            files.append(value)
        }
    }

    mutating func consumeInlineOption(_ argument: String) -> Bool {
        if argument.hasPrefix("--project-name=") {
            projectName = String(argument.dropFirst("--project-name=".count))
        } else if argument.hasPrefix("--project-directory=") {
            projectDirectory = String(argument.dropFirst("--project-directory=".count))
        } else if argument.hasPrefix("--file=") {
            files.append(String(argument.dropFirst("--file=".count)))
        } else {
            return false
        }
        return true
    }
}

public struct ComposeCommandEnvelope: Equatable, Sendable {
    public var command: String?
    public var projectName: String?
    public var projectDirectory: String?
    public var files: [String]
    public var mutating: Bool

    public init(arguments: [String]) throws {
        let parsed = try Self.parse(arguments)
        command = parsed.command
        projectName = parsed.projectName
        projectDirectory = parsed.projectDirectory
        files = parsed.files
        mutating = Self.mutatingCommands.contains(parsed.command ?? "")
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
                parsed.command = parsed.command ?? argument
                index += 1
                continue
            }
            if argument == "--" {
                optionsEnded = true
                index += 1
                continue
            }
            if ["-p", "--project-name", "--project-directory", "-f", "--file"].contains(argument) {
                guard index + 1 < arguments.count else {
                    throw DevContainerError(
                        .invalidRequest,
                        message: "Compose option \(argument) requires a value"
                    )
                }
                let value = arguments[index + 1]
                parsed.consumeValueOption(argument, value: value)
                index += 2
                continue
            }
            if parsed.consumeInlineOption(argument) {
                index += 1
                continue
            }
            if argument.hasPrefix("-") {
                index += 1
                continue
            }
            parsed.command = argument
            break
        }
        return parsed
    }

    private static let mutatingCommands: Set<String> = [
        "build",
        "create",
        "down",
        "kill",
        "pause",
        "pull",
        "push",
        "restart",
        "rm",
        "run",
        "start",
        "stop",
        "unpause",
        "up"
    ]
}
