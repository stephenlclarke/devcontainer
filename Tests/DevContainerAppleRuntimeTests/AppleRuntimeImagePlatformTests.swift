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

@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleRuntimeImagePlatformTests {
    @Test
    func `image inspection selects the requested Apple platform variant`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        let amd64 = try await runtime.inspectImage(
            reference: "fixture:latest",
            platform: "linux/amd64/v3",
            context: context
        )
        #expect(amd64.architecture == "amd64")
        #expect(amd64.variant == "v3")
        #expect(amd64.size == 1)

        let dockerAPIPlatform = try await runtime.inspectImage(
            reference: "fixture:latest",
            platform: #"{"os":"linux","architecture":"amd64","variant":"v3"}"#,
            context: context
        )
        #expect(dockerAPIPlatform.architecture == "amd64")
        #expect(dockerAPIPlatform.variant == "v3")
        #expect(dockerAPIPlatform.size == 1)

        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectImage(
                reference: "fixture:latest",
                platform: "linux/s390x",
                context: context
            )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectImage(
                reference: "fixture:latest",
                platform: #"{"os":"linux","architecture":"amd64","variant":""}"#,
                context: context
            )
        }
    }
}
