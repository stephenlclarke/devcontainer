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
import DevContainerCore
import DevContainerModel
import Foundation

struct ConfigureCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "configure",
        abstract: "Write the user configuration without changing Docker's default context"
    )

    @Option(name: .long, help: "Backend: stock or container-compose.")
    var backend: String?

    @Option(name: .long, help: "Compose provider: docker or container-compose.")
    var composeProvider: String?

    @Option(name: .long, help: "Engine Unix socket.")
    var socket: String?

    @Flag(name: .long, inversion: .prefixedNo, help: "Enable strict capability validation.")
    var strict = true

    @Option(name: .long, help: "Configuration file path.")
    var config = CLIPaths.configuration

    func run() throws {
        let url = URL(fileURLWithPath: config)
        var value = try DevContainerConfigurationStore.load(
            from: url,
            defaultSocket: CLIPaths.socket
        )
        if let backend {
            guard let parsed = BackendProvider(rawValue: backend) else {
                throw ValidationError("backend must be stock or container-compose")
            }
            value.backend = parsed
        }
        if let composeProvider {
            guard let parsed = ComposeProviderKind(rawValue: composeProvider) else {
                throw ValidationError("compose provider must be docker or container-compose")
            }
            value.composeProvider = parsed
        }
        if let socket {
            value.socket = socket
        }
        value.strictCompatibility = strict
        try DevContainerConfigurationStore.save(value, to: url)
        print(config)
    }
}
