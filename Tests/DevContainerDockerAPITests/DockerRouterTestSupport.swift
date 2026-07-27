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

@testable import DevContainerDockerAPI
import DevContainerModel

func awaitExecResizeStatus(
    router: DockerRouter,
    execID: ExecID,
    width: UInt16,
    height: UInt16
) async throws -> Int {
    let deadline = ContinuousClock.now + .seconds(5)
    var status = 200
    while status != 409, ContinuousClock.now < deadline {
        status = await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/exec/\(execID)/resize?w=\(width)&h=\(height)"
            )
        ).status
        if status != 409 {
            try await Task.sleep(for: .milliseconds(10))
        }
    }
    return status
}
