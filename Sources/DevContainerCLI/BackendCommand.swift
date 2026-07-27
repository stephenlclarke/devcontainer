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

import ArgumentParser
import DevContainerModel
import DevContainerState
import Foundation

struct BackendCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "backend",
        abstract: "Inspect or manage a durable project provider claim",
        subcommands: [
            BackendShow.self,
            BackendSet.self,
            BackendReset.self
        ],
        defaultSubcommand: BackendShow.self
    )
}

private struct BackendOptions: ParsableArguments {
    @Option(name: .long, help: "Project ownership key.")
    var project: String

    @Option(name: .long, help: "State database path.")
    var state = CLIPaths.stateDatabase
}

private struct BackendShow: AsyncParsableCommand {
    static let configuration = CommandConfiguration(commandName: "show")

    @OptionGroup
    var options: BackendOptions

    mutating func run() async throws {
        let store = try SQLiteStateStore(path: URL(fileURLWithPath: options.state))
        guard let project = try await store.project(key: ProjectKey(rawValue: options.project)) else {
            throw ValidationError("project \(options.project) has no provider claim")
        }
        print(project.provider.rawValue)
    }
}

private struct BackendSet: AsyncParsableCommand {
    static let configuration = CommandConfiguration(commandName: "set")

    @OptionGroup
    var options: BackendOptions

    @Argument(help: "Provider: stock or container-compose.")
    var provider: String

    mutating func run() async throws {
        guard let selected = BackendProvider(rawValue: provider) else {
            throw ValidationError("provider must be stock or container-compose")
        }
        let store = try SQLiteStateStore(path: URL(fileURLWithPath: options.state))
        let record = try await store.claimProject(
            key: ProjectKey(rawValue: options.project),
            provider: selected,
            composeProject: nil,
            projectDirectory: nil,
            configurationHash: nil
        )
        print(record.provider.rawValue)
    }
}

private struct BackendReset: AsyncParsableCommand {
    static let configuration = CommandConfiguration(commandName: "reset")

    @OptionGroup
    var options: BackendOptions

    mutating func run() async throws {
        let store = try SQLiteStateStore(path: URL(fileURLWithPath: options.state))
        try await store.releaseProject(key: ProjectKey(rawValue: options.project))
        print("reset \(options.project)")
    }
}
