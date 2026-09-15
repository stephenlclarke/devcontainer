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

private struct DockerIgnoreRule {
    let includes: Bool
    let pattern: DockerIgnoreGlobPattern

    func matches(_ path: String, remainingWork: inout Int) throws -> Bool {
        if pattern.containsSeparator {
            return try pattern.matches(
                path,
                allowingDescendants: true,
                remainingWork: &remainingWork
            )
        }
        for component in path.split(separator: "/", omittingEmptySubsequences: false)
            where try pattern.matches(
                String(component),
                allowingDescendants: false,
                remainingWork: &remainingWork
            )
        {
            return true
        }
        return false
    }
}

private struct DockerIgnoreCharacterClass {
    enum Member {
        case literal(Unicode.Scalar)
        case range(Unicode.Scalar, Unicode.Scalar)

        func contains(_ scalar: Unicode.Scalar) -> Bool {
            switch self {
            case let .literal(expected):
                scalar == expected
            case let .range(lower, upper):
                lower.value <= scalar.value && scalar.value <= upper.value
            }
        }
    }

    let inverted: Bool
    let members: [Member]

    var maximumMembershipWork: Int {
        members.count + 1
    }

    func contains(_ scalar: Unicode.Scalar) -> Bool {
        let found = members.contains { $0.contains(scalar) }
        return inverted ? !found : found
    }
}

private struct DockerIgnoreGlobPattern {
    private struct ParsedLiteral {
        let scalar: Unicode.Scalar
        let escaped: Bool
    }

    private enum Token {
        case literal(Unicode.Scalar)
        case anyNonSeparator
        case starNonSeparator
        case starAny
        case starDirectories
        case characterClass(DockerIgnoreCharacterClass)
    }

    let containsSeparator: Bool
    private let tokens: [Token]
    private let literal: [Unicode.Scalar]?

    init(_ source: String) throws {
        containsSeparator = source.contains("/")
        let scalars = Array(source.unicodeScalars)
        var compiled: [Token] = []
        var index = 0
        while index < scalars.count {
            switch scalars[index] {
            case "*":
                compiled.append(Self.starToken(scalars, index: &index))
            case "?":
                compiled.append(.anyNonSeparator)
            case "[":
                let characterClass = try Self.characterClass(
                    scalars,
                    startingAt: index,
                    source: source
                )
                compiled.append(.characterClass(characterClass.value))
                index = characterClass.endingAt
            case "\\":
                if index + 1 < scalars.count {
                    index += 1
                    compiled.append(.literal(scalars[index]))
                } else {
                    throw DockerCLIError.invalidArguments(
                        "malformed .dockerignore pattern: \(source)"
                    )
                }
            default:
                compiled.append(.literal(scalars[index]))
            }
            index += 1
        }
        tokens = compiled
        var literalScalars: [Unicode.Scalar] = []
        for token in compiled {
            guard case let .literal(scalar) = token else {
                literal = nil
                return
            }
            literalScalars.append(scalar)
        }
        literal = literalScalars
    }

    /// Dynamic programming evaluates each compiled-state/input pair once.
    /// Unlike a backtracking regular expression, adjacent or interleaved
    /// stars therefore cannot cause exponential work.
    func matches(
        _ value: String,
        allowingDescendants: Bool,
        remainingWork: inout Int
    ) throws -> Bool {
        let scalars = Array(value.unicodeScalars)
        if let literal {
            return scalars == literal
                || (
                    allowingDescendants
                        && scalars.count > literal.count
                        && scalars.prefix(literal.count).elementsEqual(literal)
                        && scalars[literal.count] == "/"
                )
        }
        let work = requiredWork(inputCount: scalars.count)
        guard work <= remainingWork else {
            throw DockerCLIError.invalidArguments(
                ".dockerignore matching work exceeds the safe build-context limit"
            )
        }
        remainingWork -= work
        return dynamicMatch(scalars, allowingDescendants: allowingDescendants)
    }

    private func requiredWork(inputCount: Int) -> Int {
        var units = 0
        for token in tokens {
            let tokenUnits: Int = if case let .characterClass(characterClass) = token {
                characterClass.maximumMembershipWork
            } else {
                1
            }
            let addition = units.addingReportingOverflow(tokenUnits)
            guard !addition.overflow else {
                return Int.max
            }
            units = addition.partialValue
        }
        let calculation = units.multipliedReportingOverflow(by: inputCount + 1)
        return calculation.overflow ? Int.max : calculation.partialValue
    }

    private func dynamicMatch(
        _ scalars: [Unicode.Scalar],
        allowingDescendants: Bool
    ) -> Bool {
        var following = (0 ... scalars.count).map { index in
            index == scalars.count
                || (allowingDescendants && scalars[index] == "/")
        }
        for token in tokens.reversed() {
            var current = Array(repeating: false, count: scalars.count + 1)
            var directorySuffixMatches = false
            for index in stride(from: scalars.count, through: 0, by: -1) {
                switch token {
                case let .literal(expected):
                    current[index] = index < scalars.count
                        && scalars[index] == expected
                        && following[index + 1]
                case .anyNonSeparator:
                    current[index] = index < scalars.count
                        && scalars[index] != "/"
                        && following[index + 1]
                case .starNonSeparator:
                    current[index] = following[index]
                        || (index < scalars.count
                            && scalars[index] != "/"
                            && current[index + 1])
                case .starAny:
                    current[index] = following[index]
                        || (index < scalars.count && current[index + 1])
                case .starDirectories:
                    if index < scalars.count,
                       scalars[index] == "/",
                       following[index + 1]
                    {
                        directorySuffixMatches = true
                    }
                    current[index] = following[index] || directorySuffixMatches
                case let .characterClass(characterClass):
                    current[index] = index < scalars.count
                        && characterClass.contains(scalars[index])
                        && following[index + 1]
                }
            }
            following = current
        }
        return following[0]
    }

    private static func starToken(
        _ scalars: [Unicode.Scalar],
        index: inout Int
    ) -> Token {
        guard index + 1 < scalars.count, scalars[index + 1] == "*" else {
            return .starNonSeparator
        }
        while index + 1 < scalars.count, scalars[index + 1] == "*" {
            index += 1
        }
        guard index + 1 < scalars.count, scalars[index + 1] == "/" else {
            return .starAny
        }
        index += 1
        return .starDirectories
    }

    private static func characterClass(
        _ scalars: [Unicode.Scalar],
        startingAt start: Int,
        source: String
    ) throws -> (value: DockerIgnoreCharacterClass, endingAt: Int) {
        var index = start + 1
        guard index < scalars.count else {
            throw DockerCLIError.invalidArguments(
                "malformed .dockerignore pattern: \(source)"
            )
        }
        // Docker delegates character classes to Go's path.Match grammar,
        // where only ^ negates a class. A leading ! is a literal member.
        let inverted = scalars[index] == "^"
        if inverted {
            index += 1
        }
        var literals: [ParsedLiteral] = []
        while index < scalars.count, scalars[index] != "]" {
            var escaped = false
            if scalars[index] == "\\", index + 1 < scalars.count {
                index += 1
                escaped = true
            }
            literals.append(
                ParsedLiteral(scalar: scalars[index], escaped: escaped)
            )
            index += 1
        }
        guard index < scalars.count,
              !literals.isEmpty,
              literals.first.map({ $0.scalar != "-" || $0.escaped }) == true,
              literals.last.map({ $0.scalar != "-" || $0.escaped }) == true
        else {
            throw DockerCLIError.invalidArguments(
                "malformed .dockerignore pattern: \(source)"
            )
        }

        return try (
            DockerIgnoreCharacterClass(
                inverted: inverted,
                members: characterClassMembers(literals, source: source)
            ),
            index
        )
    }

    private static func characterClassMembers(
        _ literals: [ParsedLiteral],
        source: String
    ) throws -> [DockerIgnoreCharacterClass.Member] {
        var members: [DockerIgnoreCharacterClass.Member] = []
        var literalIndex = 0
        while literalIndex < literals.count {
            let lower = literals[literalIndex]
            guard lower.scalar != "-" || lower.escaped else {
                throw DockerCLIError.invalidArguments(
                    "malformed .dockerignore pattern: \(source)"
                )
            }
            if literalIndex + 2 < literals.count,
               literals[literalIndex + 1].scalar == "-",
               !literals[literalIndex + 1].escaped
            {
                let upper = literals[literalIndex + 2]
                guard upper.scalar != "-" || upper.escaped,
                      lower.scalar.value <= upper.scalar.value
                else {
                    throw DockerCLIError.invalidArguments(
                        "malformed .dockerignore pattern: \(source)"
                    )
                }
                members.append(.range(
                    lower.scalar,
                    upper.scalar
                ))
                literalIndex += 3
            } else {
                members.append(.literal(lower.scalar))
                literalIndex += 1
            }
        }
        return members
    }
}

struct DockerIgnoreMatcher {
    private static let maximumMatchingWork = 32 * 1024 * 1024

    private let rules: [DockerIgnoreRule]
    private var remainingWork: Int

    init(contents: String, matchingWorkLimit: Int? = nil) throws {
        remainingWork = matchingWorkLimit ?? Self.maximumMatchingWork
        rules = try contents.split(
            separator: "\n",
            omittingEmptySubsequences: false
        ).compactMap { rawLine in
            try Self.rule(String(rawLine))
        }
    }

    init(contentsOf url: URL, matchingWorkLimit: Int? = nil) throws {
        guard FileManager.default.fileExists(atPath: url.path) else {
            try self.init(contents: "", matchingWorkLimit: matchingWorkLimit)
            return
        }
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw DockerCLIError.invalidArguments(
                "could not read .dockerignore: \(error.localizedDescription)"
            )
        }
        try Self.validateLineLengths(in: data)
        let utf8BOM = Data([0xEF, 0xBB, 0xBF])
        let contentData = data.starts(with: utf8BOM) ? data.dropFirst(utf8BOM.count) : data[...]
        guard let contents = String(data: contentData, encoding: .utf8) else {
            throw DockerCLIError.invalidArguments(
                "could not read .dockerignore: contents are not valid UTF-8"
            )
        }
        try self.init(contents: contents, matchingWorkLimit: matchingWorkLimit)
    }

    private static func validateLineLengths(in data: Data) throws {
        let maximumTokenSize = 64 * 1024
        var lineLength = 0
        for byte in data {
            if byte == 0x0A {
                guard lineLength < maximumTokenSize else {
                    throw DockerCLIError.invalidArguments(
                        "could not read .dockerignore: line is too long"
                    )
                }
                lineLength = 0
            } else {
                lineLength += 1
                guard lineLength <= maximumTokenSize else {
                    throw DockerCLIError.invalidArguments(
                        "could not read .dockerignore: line is too long"
                    )
                }
            }
        }
        guard lineLength < maximumTokenSize else {
            throw DockerCLIError.invalidArguments(
                "could not read .dockerignore: line is too long"
            )
        }
    }

    mutating func includes(_ path: String) throws -> Bool {
        let components = path.split(separator: "/")
        if components.count > 1 {
            for end in 1 ..< components.count {
                let ancestor = components[..<end].joined(separator: "/")
                if try !ruleResult(for: ancestor) {
                    return false
                }
            }
        }
        return try ruleResult(for: path)
    }

    private mutating func ruleResult(for path: String) throws -> Bool {
        var result = true
        for rule in rules where try rule.matches(path, remainingWork: &remainingWork) {
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
        // Docker cleans the complete line before interpreting a leading `!`.
        // Cleaning only the text after `!` changes `!dir/../secret` from an
        // exclusion into an inclusion and can leak an ignored build-context
        // file.
        pattern = normalized(pattern)
        let includes = pattern.hasPrefix("!")
        if includes {
            guard pattern != "!" else {
                throw DockerCLIError.invalidArguments(
                    "malformed .dockerignore pattern: \(rawLine)"
                )
            }
            pattern.removeFirst()
        }
        pattern = normalized(pattern)
        guard !pattern.isEmpty, pattern != "." else {
            return nil
        }
        return try DockerIgnoreRule(
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
