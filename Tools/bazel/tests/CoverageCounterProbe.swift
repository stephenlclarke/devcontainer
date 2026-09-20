// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Dispatch

/// These branches reproduce the production counter underflow without a runtime
/// dependency. No shared product state exists: only LLVM's counters are shared.
@inline(never)
func firstCandidate(_ prefix: String) -> String {
    for candidate in ["alpha", "beta", "gamma"] where candidate.hasPrefix(prefix) {
        return candidate
    }
    return "fallback"
}

@inline(never)
func expandHome(_ path: String) -> String {
    guard path == "~" || path.hasPrefix("~/") else {
        return path
    }
    return "/home/user" + String(path.dropFirst())
}

enum InvalidOption: Error { case unsupported }

@inline(never)
func inlineOption(_ argument: String) throws -> Bool {
    if argument == "throw" {
        throw InvalidOption.unsupported
    }
    return argument.hasPrefix("--name=")
}

@inline(never)
func consumeOption(_ argument: String) throws -> Bool {
    if ["--name", "--file"].contains(argument) {
        return true
    }
    if try inlineOption(argument) {
        return true
    }
    guard ["--help", "--version", "--dry-run"].contains(argument) else {
        return false
    }
    return ["--help", "--version"].contains(argument)
}

@main
enum CoverageCounterProbe {
    static func main() {
        DispatchQueue.concurrentPerform(iterations: 32) { _ in
            for _ in 0 ..< 10000 {
                precondition(firstCandidate("a") == "alpha")
                precondition(expandHome("/work") == "/work")
                precondition((try? consumeOption("--name=test")) == true)
            }
        }
    }
}
