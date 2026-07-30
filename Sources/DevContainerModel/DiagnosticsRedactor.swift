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

public enum DiagnosticsRedactor {
    private static let authorizationPattern = try? NSRegularExpression(
        pattern: #"(?i)(["']?authorization["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\r\n]+)"#
    )
    private static let sensitivePattern = try? NSRegularExpression(
        pattern:
        #"(?i)(["']?(?:authorization|token|password|secret|cookie|"#
            + #"credential)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)"#
    )

    public static func redact(_ text: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let pathRedacted = home == "/"
            ? text
            : text.replacingOccurrences(of: home, with: "$HOME")
        let authorizationRedacted = authorizationPattern?.stringByReplacingMatches(
            in: pathRedacted,
            range: NSRange(pathRedacted.startIndex..., in: pathRedacted),
            withTemplate: #"$1"<redacted>""#
        ) ?? pathRedacted
        guard let sensitivePattern else {
            return authorizationRedacted
        }
        return sensitivePattern.stringByReplacingMatches(
            in: authorizationRedacted,
            range: NSRange(
                authorizationRedacted.startIndex...,
                in: authorizationRedacted
            ),
            withTemplate: #"$1"<redacted>""#
        )
    }
}
