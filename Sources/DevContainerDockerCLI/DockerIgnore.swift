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

struct DockerIgnoreMatcher {
    private struct Rule {
        let includes: Bool
        let expression: NSRegularExpression

        func matches(_ path: String) -> Bool {
            expression.firstMatch(
                in: path,
                range: NSRange(path.startIndex..., in: path)
            ) != nil
        }
    }

    private let rules: [Rule]

    init(contents: String) throws {
        rules = try contents.split(
            separator: "\n",
            omittingEmptySubsequences: false
        ).compactMap { rawLine in
            try Self.rule(String(rawLine))
        }
    }

    func includes(_ path: String) -> Bool {
        let components = path.split(separator: "/")
        if components.count > 1 {
            for end in 1 ..< components.count {
                let ancestor = components[..<end].joined(separator: "/")
                if !ruleResult(for: ancestor) {
                    return false
                }
            }
        }
        return ruleResult(for: path)
    }

    private func ruleResult(for path: String) -> Bool {
        var result = true
        for rule in rules where rule.matches(path) {
            result = rule.includes
        }
        return result
    }

    private static func rule(_ rawLine: String) throws -> Rule? {
        guard !rawLine.hasPrefix("#") else {
            return nil
        }
        var pattern = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !pattern.isEmpty else {
            return nil
        }
        let includes = pattern.hasPrefix("!")
        if includes {
            pattern.removeFirst()
        }
        pattern = normalized(pattern)
        guard !pattern.isEmpty, pattern != "." else {
            return nil
        }
        let body = globExpression(pattern)
        let prefix = pattern.contains("/") ? "^" : "(?:^|.*/)"
        return try Rule(
            includes: includes,
            expression: NSRegularExpression(
                pattern: prefix + body + "(?:/.*)?$"
            )
        )
    }

    private static func normalized(_ pattern: String) -> String {
        var result = pattern
        while result.hasPrefix("./") || result.hasPrefix("/") {
            result.removeFirst(result.hasPrefix("./") ? 2 : 1)
        }
        while result.hasSuffix("/") {
            result.removeLast()
        }
        return result
    }

    private static func globExpression(_ pattern: String) -> String {
        let characters = Array(pattern)
        var result = ""
        var index = 0
        while index < characters.count {
            switch characters[index] {
            case "*":
                result += starExpression(characters, index: &index)
            case "?":
                result += "[^/]"
            case "[":
                if let characterClass = characterClassExpression(
                    characters,
                    startingAt: index
                ) {
                    result += characterClass.expression
                    index = characterClass.endingAt
                } else {
                    result += "\\["
                }
            case "\\":
                if index + 1 < characters.count {
                    index += 1
                    result += NSRegularExpression.escapedPattern(
                        for: String(characters[index])
                    )
                } else {
                    result += "\\\\"
                }
            default:
                result += NSRegularExpression.escapedPattern(
                    for: String(characters[index])
                )
            }
            index += 1
        }
        return result
    }

    private static func starExpression(
        _ characters: [Character],
        index: inout Int
    ) -> String {
        guard index + 1 < characters.count,
              characters[index + 1] == "*"
        else {
            return "[^/]*"
        }
        while index + 1 < characters.count, characters[index + 1] == "*" {
            index += 1
        }
        guard index + 1 < characters.count, characters[index + 1] == "/" else {
            return ".*"
        }
        index += 1
        return "(?:.*/)?"
    }

    private static func characterClassExpression(
        _ characters: [Character],
        startingAt start: Int
    ) -> (expression: String, endingAt: Int)? {
        var index = start + 1
        guard index < characters.count else {
            return nil
        }
        var expression = "["
        if characters[index] == "!" || characters[index] == "^" {
            expression += "^"
            index += 1
        }
        if index < characters.count, characters[index] == "]" {
            expression += "\\]"
            index += 1
        }
        let contentStart = index
        while index < characters.count, characters[index] != "]" {
            let character = characters[index]
            switch character {
            case "\\": expression += "\\\\"
            case "^" where index == contentStart: expression += "\\^"
            default: expression.append(character)
            }
            index += 1
        }
        guard index < characters.count, index > contentStart else {
            return nil
        }
        expression += "]"
        return (expression, index)
    }
}
