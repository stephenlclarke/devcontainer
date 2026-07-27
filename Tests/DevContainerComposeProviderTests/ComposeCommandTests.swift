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

import DevContainerComposeProvider
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

@Test
func `command envelope finds project and mutation`() throws {
    let envelope = try ComposeCommandEnvelope(
        arguments: [
            "--project-directory", "/workspace",
            "-f", "compose.yaml",
            "--project-name=Demo",
            "up",
            "--detach"
        ]
    )
    #expect(envelope.command == "up")
    #expect(envelope.projectName == "Demo")
    #expect(envelope.projectDirectory == "/workspace")
    #expect(envelope.files == ["compose.yaml"])
    #expect(envelope.mutating)
    #expect(envelope.projectKey(userID: 501) == ProjectKey(rawValue: "501:demo"))
}

@Test
func `read only and malformed commands are handled`() throws {
    let version = try ComposeCommandEnvelope(arguments: ["version"])
    #expect(version.command == "version")
    #expect(!version.mutating)
    #expect(version.projectKey(userID: 501) == nil)
    #expect(throws: DevContainerError.self) {
        _ = try ComposeCommandEnvelope(arguments: ["--project-name"])
    }
}

@Test
func `command envelope accepts every global option spelling and separator`() throws {
    let split = try ComposeCommandEnvelope(
        arguments: [
            "-p", "Demo",
            "--project-directory=/workspace",
            "--file=first.yaml",
            "--unknown",
            "-f", "second.yaml",
            "--",
            "up",
            "--detach"
        ]
    )
    #expect(split.command == "up")
    #expect(split.projectName == "Demo")
    #expect(split.projectDirectory == "/workspace")
    #expect(split.files == ["first.yaml", "second.yaml"])
    #expect(split.mutating)

    let empty = try ComposeCommandEnvelope(arguments: [])
    #expect(empty.command == nil)
    #expect(!empty.mutating)
}

@Test
func `provider rejects missing executable`() {
    #expect(throws: DevContainerError.self) {
        _ = try ExecutableComposeProvider(
            executable: URL(fileURLWithPath: "/path/that/does/not/exist")
        )
    }
}

@Test
func `provider probes and invokes a compatible executable`() async throws {
    let fixture = try FakeComposeExecutable(mode: .valid)
    let provider = try ExecutableComposeProvider(
        executable: fixture.executable,
        environment: [
            "PATH": "/usr/bin:/bin",
            "SAFE_BASE": "yes",
            "DYLD_INSERT_LIBRARIES": "blocked",
            "BASH_ENV": "blocked"
        ]
    )
    let context = RuntimeRequestContext()
    let descriptor = try await provider.descriptor(context: context)
    #expect(descriptor.provider == .containerCompose)
    #expect(descriptor.providerVersion == "0.10.0")
    #expect(descriptor.providerCommit == "fixture-commit")
    #expect(descriptor.capabilities[.build] == .native)
    #expect(descriptor.capabilities[.events] == .emulated)
    #expect(descriptor.capabilities[.registryAuthentication] == .unsupported)

    let result = try await provider.invoke(
        ComposeInvocation(
            arguments: ["up", "--detach"],
            environment: ["COMPOSE_PROJECT_NAME": "fixture"],
            workingDirectory: fixture.root,
            project: ProjectKey(rawValue: "501:fixture"),
            mutating: true
        ),
        context: context
    )
    #expect(result.exitCode == 0)
    #expect(
        String(data: result.standardOutput, encoding: .utf8)?
            .contains("fixture") == true
    )
    #expect(
        String(data: result.standardError, encoding: .utf8)
            == "compose-warning"
    )
    let environment = try fixture.environmentLog()
    #expect(environment.contains("SAFE_BASE=yes"))
    #expect(environment.contains("COMPOSE_PROJECT_NAME=fixture"))
    #expect(!environment.contains("DYLD_INSERT_LIBRARIES"))
    #expect(!environment.contains("BASH_ENV"))
}

@Test
func `provider rejects unsafe overrides and incompatible version probes`() async throws {
    let valid = try FakeComposeExecutable(mode: .valid)
    let provider = try ExecutableComposeProvider(executable: valid.executable)
    await #expect(throws: DevContainerError.self) {
        _ = try await provider.invoke(
            ComposeInvocation(
                arguments: ["version"],
                environment: ["LD_PRELOAD": "blocked"],
                workingDirectory: valid.root,
                project: nil,
                mutating: false
            ),
            context: RuntimeRequestContext()
        )
    }

    for mode in [FakeComposeExecutable.Mode.wrongSource, .invalidJSON, .failure] {
        let fixture = try FakeComposeExecutable(mode: mode)
        let incompatible = try ExecutableComposeProvider(executable: fixture.executable)
        await #expect(throws: DevContainerError.self) {
            _ = try await incompatible.descriptor(context: RuntimeRequestContext())
        }
    }
}

private struct FakeComposeExecutable {
    enum Mode: String {
        case valid
        case wrongSource
        case invalidJSON
        case failure
    }

    let root: URL
    let executable: URL
    private let environmentURL: URL

    init(mode: Mode) throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-compose-tests-\(UUID().uuidString)")
        executable = root.appendingPathComponent("container-compose")
        environmentURL = root.appendingPathComponent("environment.log")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        let script = """
        #!/bin/sh
        set -eu
        env | sort > '\(environmentURL.path)'
        if [ "${1-}" = version ]; then
          case '\(mode.rawValue)' in
            valid)
              printf '%s\\n' '{
                "version":"0.10.0",
                "source":"stephenlclarke/container-compose",
                "commit":"fixture-commit",
                "containerDistribution":"custom"
              }'
              ;;
            wrongSource)
              printf '%s\\n' '{"version":"1","source":"someone/else"}'
              ;;
            invalidJSON)
              printf '%s\\n' 'not-json'
              ;;
            failure)
              printf '%s' 'probe-failed' >&2
              exit 23
              ;;
          esac
        else
          printf '%s' "${COMPOSE_PROJECT_NAME-unset}"
          printf '%s' 'compose-warning' >&2
        fi
        """
        try Data(script.utf8).write(to: executable)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: executable.path
        )
    }

    func environmentLog() throws -> String {
        try String(contentsOf: environmentURL, encoding: .utf8)
    }
}
