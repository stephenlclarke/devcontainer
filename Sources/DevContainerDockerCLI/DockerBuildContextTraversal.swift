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

enum DockerBuildContextTraversal {
    static func pruneExcludedDirectory(
        _ url: URL,
        path: String,
        preserving requiredPath: String?,
        in enumerator: FileManager.DirectoryEnumerator?
    ) {
        guard requiredPath?.hasPrefix(path + "/") != true else {
            return
        }
        let values = try? url.resourceValues(
            forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        )
        guard values?.isDirectory == true, values?.isSymbolicLink != true else {
            return
        }
        // Docker cannot re-include a child after excluding its parent, so
        // walking this real directory subtree can add only wasted work.
        enumerator?.skipDescendants()
    }
}
