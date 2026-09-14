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
import Testing

struct AppleContainerRuntimeSupportTests {
    @Test
    func `archive transfer names cannot escape their private staging directory`() {
        let root = AppleContainerRuntime.archiveTransferNames(for: "/")
        #expect(root.requested == "/")
        #expect(root.staging == "root")

        let normalizedRoot = AppleContainerRuntime.archiveTransferNames(
            for: "/workspace/.."
        )
        #expect(normalizedRoot.requested == "/")
        #expect(normalizedRoot.staging == "root")

        let optionLike = AppleContainerRuntime.archiveTransferNames(for: "/-C")
        #expect(optionLike.requested == "-C")
        #expect(optionLike.staging == "-C")
    }
}
