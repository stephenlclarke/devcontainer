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

import Foundation
import PackagePlugin

@main
struct GenerateDevContainerVersion: BuildToolPlugin {
    private enum IdentityError: Error {
        case invalidCommit(String)
        case invalidLane(String)
    }

    func createBuildCommands(
        context: PluginContext,
        target _: Target
    ) async throws -> [Command] {
        let generator = try context.tool(named: "DevContainerVersionGenerator")
        let makefile = context.package.directoryURL.appending(path: "Makefile")
        let environment = ProcessInfo.processInfo.environment
        let commit = environment["GIT_COMMIT"] ?? "unspecified"
        let lane = environment["DEVCONTAINER_BUILD_LANE"] ?? "development"
        guard commit == "unspecified" || commit.wholeMatch(
            of: /^[0-9a-f]{40}$/
        ) != nil else {
            throw IdentityError.invalidCommit(commit)
        }
        guard lane.wholeMatch(of: /^[a-z][a-z0-9-]*$/) != nil else {
            throw IdentityError.invalidLane(lane)
        }
        let output = context.pluginWorkDirectoryURL.appending(
            path: "DevContainerVersionGenerated-\(commit)-\(lane).swift"
        )

        return [
            .buildCommand(
                displayName: "Generate devcontainer build identity",
                executable: generator.url,
                arguments: [
                    makefile.path,
                    output.path,
                    commit,
                    lane
                ],
                inputFiles: [makefile],
                outputFiles: [output]
            )
        ]
    }
}
