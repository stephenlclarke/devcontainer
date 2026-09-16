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
import DevContainerDockerCLI
import Foundation

do {
    let application = try DockerCLIApplication.configured()
    let arguments = Array(CommandLine.arguments.dropFirst())
    let standardInputFileDescriptor = try DockerCLIApplication.requiresInteractiveInput(
        arguments: arguments
    )
        ? STDIN_FILENO
        : nil
    let result = try application.run(
        arguments: arguments,
        standardInputFileDescriptor: standardInputFileDescriptor
    ) { data, standardError in
        try (standardError ? FileHandle.standardError : FileHandle.standardOutput)
            .write(contentsOf: data)
    }
    try FileHandle.standardOutput.write(contentsOf: result.standardOutput)
    try FileHandle.standardError.write(contentsOf: result.standardError)
    exit(result.exitCode)
} catch {
    let message = "devcontainer-docker: \(error)\n"
    try? FileHandle.standardError.write(contentsOf: Data(message.utf8))
    exit(1)
}
