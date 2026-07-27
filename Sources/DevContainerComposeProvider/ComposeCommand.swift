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

public struct ComposeCommandEnvelope: Equatable, Sendable {
    public var command: String?
    public var projectName: String?
    public var projectDirectory: String?
    public var files: [String]
    public var mutating: Bool

    public init(arguments: [String]) throws {
        var commandValue: String?
        var projectNameValue: String?
        var projectDirectoryValue: String?
        var fileValues: [String] = []
        var index = 0
        var optionsEnded = false

        while index < arguments.count {
            let argument = arguments[index]
            if optionsEnded {
                commandValue = commandValue ?? argument
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
                switch argument {
                case "-p", "--project-name":
                    projectNameValue = value
                case "--project-directory":
                    projectDirectoryValue = value
                default:
                    fileValues.append(value)
                }
                index += 2
                continue
            }
            if argument.hasPrefix("--project-name=") {
                projectNameValue = String(argument.dropFirst("--project-name=".count))
                index += 1
                continue
            }
            if argument.hasPrefix("--project-directory=") {
                projectDirectoryValue = String(argument.dropFirst("--project-directory=".count))
                index += 1
                continue
            }
            if argument.hasPrefix("--file=") {
                fileValues.append(String(argument.dropFirst("--file=".count)))
                index += 1
                continue
            }
            if argument.hasPrefix("-") {
                index += 1
                continue
            }
            commandValue = argument
            break
        }

        command = commandValue
        projectName = projectNameValue
        projectDirectory = projectDirectoryValue
        files = fileValues
        mutating = Self.mutatingCommands.contains(commandValue ?? "")
    }

    public func projectKey(userID: uid_t = getuid()) -> ProjectKey? {
        guard let projectName, !projectName.isEmpty else {
            return nil
        }
        return ProjectKey(rawValue: "\(userID):\(projectName.lowercased())")
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
