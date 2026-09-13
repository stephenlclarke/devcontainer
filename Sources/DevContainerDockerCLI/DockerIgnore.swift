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

private struct DockerIgnoreRule {
    let includes: Bool
    let pattern: DockerIgnoreGlobPattern

    func matches(_ path: String) -> Bool {
        if pattern.containsSeparator {
            return pattern.matches(path, allowingDescendants: true)
        }
        return path.split(separator: "/", omittingEmptySubsequences: false)
            .contains { pattern.matches(String($0), allowingDescendants: false) }
    }
}

private struct DockerIgnoreCharacterClass {
    enum Member {
        case literal(Character)
        case range(Character, Character)

        func contains(_ character: Character) -> Bool {
            switch self {
            case let .literal(expected):
                character == expected
            case let .range(lower, upper):
                lower <= character && character <= upper
            }
        }
    }

    let inverted: Bool
    let members: [Member]

    func contains(_ character: Character) -> Bool {
        let found = members.contains { $0.contains(character) }
        return inverted ? !found : found
    }
}

private struct DockerIgnoreGlobPattern {
    private enum Token {
        case literal(Character)
        case anyNonSeparator
        case starNonSeparator
        case starAny
        case starDirectories
        case characterClass(DockerIgnoreCharacterClass)
    }

    let containsSeparator: Bool
    private let tokens: [Token]

    init(_ source: String) {
        containsSeparator = source.contains("/")
        let characters = Array(source)
        var compiled: [Token] = []
        var index = 0
        while index < characters.count {
            switch characters[index] {
            case "*":
                compiled.append(Self.starToken(characters, index: &index))
            case "?":
                compiled.append(.anyNonSeparator)
            case "[":
                if let characterClass = Self.characterClass(
                    characters,
                    startingAt: index
                ) {
                    compiled.append(.characterClass(characterClass.value))
                    index = characterClass.endingAt
                } else {
                    compiled.append(.literal("["))
                }
            case "\\":
                if index + 1 < characters.count {
                    index += 1
                    compiled.append(.literal(characters[index]))
                } else {
                    compiled.append(.literal("\\"))
                }
            default:
                compiled.append(.literal(characters[index]))
            }
            index += 1
        }
        tokens = compiled
    }

    /// Dynamic programming evaluates each compiled-state/input pair once.
    /// Unlike a backtracking regular expression, adjacent or interleaved
    /// stars therefore cannot cause exponential work.
    func matches(_ value: String, allowingDescendants: Bool) -> Bool {
        let characters = Array(value)
        var following = (0 ... characters.count).map { index in
            index == characters.count
                || (allowingDescendants && characters[index] == "/")
        }
        for token in tokens.reversed() {
            var current = Array(repeating: false, count: characters.count + 1)
            var directorySuffixMatches = false
            for index in stride(from: characters.count, through: 0, by: -1) {
                switch token {
                case let .literal(expected):
                    current[index] = index < characters.count
                        && characters[index] == expected
                        && following[index + 1]
                case .anyNonSeparator:
                    current[index] = index < characters.count
                        && characters[index] != "/"
                        && following[index + 1]
                case .starNonSeparator:
                    current[index] = following[index]
                        || (index < characters.count
                            && characters[index] != "/"
                            && current[index + 1])
                case .starAny:
                    current[index] = following[index]
                        || (index < characters.count && current[index + 1])
                case .starDirectories:
                    if index < characters.count,
                       characters[index] == "/",
                       following[index + 1]
                    {
                        directorySuffixMatches = true
                    }
                    current[index] = following[index] || directorySuffixMatches
                case let .characterClass(characterClass):
                    current[index] = index < characters.count
                        && characters[index] != "/"
                        && characterClass.contains(characters[index])
                        && following[index + 1]
                }
            }
            following = current
        }
        return following[0]
    }

    private static func starToken(
        _ characters: [Character],
        index: inout Int
    ) -> Token {
        guard index + 1 < characters.count, characters[index + 1] == "*" else {
            return .starNonSeparator
        }
        while index + 1 < characters.count, characters[index + 1] == "*" {
            index += 1
        }
        guard index + 1 < characters.count, characters[index + 1] == "/" else {
            return .starAny
        }
        index += 1
        return .starDirectories
    }

    private static func characterClass(
        _ characters: [Character],
        startingAt start: Int
    ) -> (value: DockerIgnoreCharacterClass, endingAt: Int)? {
        var index = start + 1
        guard index < characters.count else { return nil }
        let inverted = characters[index] == "!" || characters[index] == "^"
        if inverted {
            index += 1
        }
        var literals: [Character] = []
        if index < characters.count, characters[index] == "]" {
            literals.append("]")
            index += 1
        }
        while index < characters.count, characters[index] != "]" {
            if characters[index] == "\\", index + 1 < characters.count {
                index += 1
            }
            literals.append(characters[index])
            index += 1
        }
        guard index < characters.count, !literals.isEmpty else { return nil }

        var members: [DockerIgnoreCharacterClass.Member] = []
        var literalIndex = 0
        while literalIndex < literals.count {
            if literalIndex + 2 < literals.count,
               literals[literalIndex + 1] == "-"
            {
                members.append(.range(
                    literals[literalIndex],
                    literals[literalIndex + 2]
                ))
                literalIndex += 3
            } else {
                members.append(.literal(literals[literalIndex]))
                literalIndex += 1
            }
        }
        return (DockerIgnoreCharacterClass(inverted: inverted, members: members), index)
    }
}

struct DockerIgnoreMatcher {
    private let rules: [DockerIgnoreRule]

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

    private static func rule(_ rawLine: String) throws -> DockerIgnoreRule? {
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
        return DockerIgnoreRule(
            includes: includes,
            pattern: DockerIgnoreGlobPattern(pattern)
        )
    }

    private static func normalized(_ pattern: String) -> String {
        let rooted = pattern.hasPrefix("/")
        var components: [Substring] = []
        for component in pattern.split(separator: "/", omittingEmptySubsequences: true) {
            switch component {
            case ".":
                continue
            case "..":
                if components.last.map({ $0 != ".." }) == true {
                    components.removeLast()
                } else if !rooted {
                    components.append(component)
                }
            default:
                components.append(component)
            }
        }
        return components.joined(separator: "/")
    }
}
