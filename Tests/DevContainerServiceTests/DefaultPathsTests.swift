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

@testable import DevContainerService
import Foundation
import Testing

@Test
func `default paths are user scoped and executable selection is absolute`() {
    #expect(DefaultPaths.socket.hasSuffix("/devcontainer/docker.sock"))
    #expect(DefaultPaths.stateDatabase.hasSuffix("/devcontainer/state.sqlite"))
    #expect(DefaultPaths.containerExecutable.hasPrefix("/"))
    if FileManager.default.isExecutableFile(atPath: "/usr/local/bin/container") {
        #expect(DefaultPaths.containerExecutable == "/usr/local/bin/container")
    } else if FileManager.default.isExecutableFile(
        atPath: "/opt/homebrew/bin/container"
    ) {
        #expect(DefaultPaths.containerExecutable == "/opt/homebrew/bin/container")
    } else {
        #expect(DefaultPaths.containerExecutable == "/usr/local/bin/container")
    }
}
