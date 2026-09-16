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

@testable import DevContainerDockerCLI
import Foundation
import Testing

@Suite("Docker CLI build streaming")
struct DockerCLIBuildStreamingTests {
    @Test
    func `streams a private file backed build context and removes it`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-stream-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        defer { try? FileManager.default.removeItem(at: root) }
        let transport = StubTransport([
            .init(status: 200, body: Data("build-output".utf8))
        ])
        let application = DockerCLIApplication(transport: transport)

        var streamed = Data()
        let build = try application.run(arguments: ["build", root.path]) { data, _ in
            streamed.append(data)
        }

        #expect(build.standardOutput.isEmpty)
        #expect(streamed == Data("build-output".utf8))
        let request = try #require(
            transport.requests.first { $0.target.hasPrefix("/build?") }
        )
        #expect(request.headers["Content-Type"] == "application/x-tar")
        #expect(!request.body.isEmpty)
        #expect(transport.fileUploadLengths == [UInt64(request.body.count)])
        #expect(transport.fileUploadDirectoryPermissions == [0o700])
        #expect(transport.fileUploadPaths.allSatisfy {
            !FileManager.default.fileExists(atPath: $0)
        })
    }
}
