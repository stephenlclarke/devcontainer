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

public enum RuntimeLabels {
    public static let namespace = "io.github.stephenlclarke.devcontainer"
    public static let project = "\(namespace).project"
    public static let provider = "\(namespace).provider"
    public static let generation = "\(namespace).generation"
    public static let operation = "\(namespace).operation"
    public static let configurationHash = "\(namespace).config-hash"
    public static let dockerID = "\(namespace).docker-id"

    public static let devContainerLocalFolder = "devcontainer.local_folder"
    public static let devContainerConfigFile = "devcontainer.config_file"

    public static let composeProjection = [
        "com.apple.container.compose.project": "com.docker.compose.project",
        "com.apple.container.compose.service": "com.docker.compose.service",
        "com.apple.container.compose.oneoff": "com.docker.compose.oneoff",
        "com.apple.container.compose.config-hash": "com.docker.compose.config-hash",
        "com.apple.container.compose.project.working-directory":
            "com.docker.compose.project.working_dir",
        "com.apple.container.compose.project.config-files":
            "com.docker.compose.project.config_files"
    ]

    public static func projectLabels(
        project: ProjectKey,
        provider selectedProvider: BackendProvider,
        generation selectedGeneration: Int64,
        operation selectedOperation: OperationID,
        configurationHash selectedConfigurationHash: String
    ) -> [String: String] {
        [
            Self.project: project.rawValue,
            provider: selectedProvider.rawValue,
            generation: String(selectedGeneration),
            operation: selectedOperation.rawValue,
            configurationHash: selectedConfigurationHash
        ]
    }

    public static func projectComposeLabels(
        _ labels: [String: String]
    ) throws -> [String: String] {
        var result = labels
        for (native, docker) in composeProjection {
            guard let nativeValue = labels[native] else {
                continue
            }
            if let dockerValue = labels[docker], dockerValue != nativeValue {
                throw DevContainerError(
                    .conflict,
                    message: "native label \(native) conflicts with Docker label \(docker)"
                )
            }
            result[docker] = nativeValue
        }
        return result
    }

    public static func translateDockerFilters(
        _ filters: [String: String]
    ) throws -> [String: String] {
        let reverse = Dictionary(uniqueKeysWithValues: composeProjection.map { ($0.value, $0.key) })
        var result: [String: String] = [:]
        for (key, value) in filters {
            let translated = reverse[key] ?? key
            if let existing = result[translated], existing != value {
                throw DevContainerError(
                    .invalidRequest,
                    message: "conflicting values for projected label \(key)"
                )
            }
            result[translated] = value
        }
        return result
    }
}
