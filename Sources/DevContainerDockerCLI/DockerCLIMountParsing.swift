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

extension DockerRunOptions {
    static func mount(_ value: String) throws -> [String: Any] {
        let fields = try mountFields(value)
        try validateMountAliases(fields)
        guard let type = fields["type"],
              let target = fields["target"] ?? fields["dst"] ?? fields["destination"]
        else {
            throw DockerCLIError.invalidArguments("mount requires type and target")
        }
        var result: [String: Any] = ["Type": type, "Target": target]
        if let source = fields["source"] ?? fields["src"] {
            result["Source"] = source
        }
        if try mountFlag("readonly", fields: fields) || mountFlag("ro", fields: fields) {
            result["ReadOnly"] = true
        }
        if let consistency = fields["consistency"] {
            result["Consistency"] = consistency
        }
        return result
    }

    private static let supportedMountFields = Set([
        "type", "target", "dst", "destination", "source", "src",
        "readonly", "ro", "consistency"
    ])

    private static func mountFields(_ value: String) throws -> [String: String] {
        var result: [String: String] = [:]
        for component in value.split(separator: ",", omittingEmptySubsequences: false) {
            let pair = component.split(
                separator: "=",
                maxSplits: 1,
                omittingEmptySubsequences: false
            )
            let key = String(pair[0])
            guard !key.isEmpty else {
                throw DockerCLIError.invalidArguments("mount contains an empty field")
            }
            guard supportedMountFields.contains(key) else {
                throw DockerCLIError.unsupported("run --mount \(key)")
            }
            guard result[key] == nil else {
                throw DockerCLIError.invalidArguments("mount repeats field \(key)")
            }
            result[key] = pair.count == 2 ? String(pair[1]) : "true"
        }
        return result
    }

    private static func validateMountAliases(_ fields: [String: String]) throws {
        for aliases in [
            ["target", "dst", "destination"],
            ["source", "src"],
            ["readonly", "ro"]
        ] where aliases.filter({ fields[$0] != nil }).count > 1 {
            throw DockerCLIError.invalidArguments("mount repeats an aliased field")
        }
    }

    private static func mountFlag(
        _ name: String,
        fields: [String: String]
    ) throws -> Bool {
        guard let value = fields[name] else {
            return false
        }
        guard value == "true" || value == "false" else {
            throw DockerCLIError.invalidArguments("mount \(name) must be true or false")
        }
        return value == "true"
    }
}
