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

import DevContainerModel
import DevContainerRuntimeSPI

public actor RuntimeRegistry {
    private var runtimes: [BackendProvider: any DevContainerRuntime] = [:]

    public init() {}

    public func register(_ runtime: any DevContainerRuntime, for provider: BackendProvider) {
        runtimes[provider] = runtime
    }

    public func unregister(provider: BackendProvider) {
        runtimes.removeValue(forKey: provider)
    }

    public func runtime(for provider: BackendProvider) throws -> any DevContainerRuntime {
        guard let runtime = runtimes[provider] else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "\(provider.rawValue) runtime is not registered"
            )
        }
        return runtime
    }
}
