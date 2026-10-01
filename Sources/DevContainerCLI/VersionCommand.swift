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

struct VersionCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "version",
        abstract: "Show devcontainer build provenance"
    )

    @Flag(name: .long, help: "Print only the semantic version.")
    var short = false

    @Option(name: .long, help: "Output format: pretty or json.")
    var format = "pretty"

    func run() throws {
        try FileHandle.standardOutput.write(contentsOf: outputData())
        FileHandle.standardOutput.write(Data("\n".utf8))
    }

    func outputData() throws -> Data {
        let info = DevContainerProject.buildInfo
        if short {
            return Data(info.version.utf8)
        }
        switch format {
        case "pretty":
            let text = "devcontainer \(info.version) (lane: \(info.lane), commit: \(info.commit), source: \(info.source))"
            return Data(text.utf8)
        case "json":
            return try JSONEncoder.pretty.encode(info)
        default:
            throw ValidationError("unsupported format \(format); expected pretty or json")
        }
    }
}

extension JSONEncoder {
    static var pretty: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return encoder
    }
}
