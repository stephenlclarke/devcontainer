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

@testable import DevContainerCore
import DevContainerModel
import Testing

@Test
func `design manifest and library agree on required backends`() {
    #expect(DevContainerProject.designSchemaVersion == 1)
    #expect(DevContainerProject.buildInfo == BuildInfo.current)
    #expect(
        Set(DevContainerProject.requiredParityBackends) == [
            "docker",
            "apple-stock",
            "container-compose"
        ]
    )
}
