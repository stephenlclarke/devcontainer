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
import Foundation
import Testing

@Test
func `diagnostic redaction covers paths and credential shaped values`() {
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    let source = """
    path=\(home)/private/workspace
    Authorization: Bearer very-secret
    token=abc123 password='hunter2' COOKIE=session-value
    """
    let redacted = DiagnosticsRedactor.redact(source)

    #expect(redacted.contains("$HOME/private/workspace"))
    #expect(!redacted.contains(home))
    #expect(!redacted.contains("very-secret"))
    #expect(!redacted.contains("abc123"))
    #expect(!redacted.contains("hunter2"))
    #expect(!redacted.contains("session-value"))
}

@Test
func `build info uses makefile owned version`() throws {
    let repositoryRoot = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
    let makefile = try String(
        contentsOf: repositoryRoot.appendingPathComponent("Makefile"),
        encoding: .utf8
    )
    let assignment = try #require(
        makefile.split(separator: "\n").first {
            $0.hasPrefix("DEVCONTAINER_VERSION ?= ")
        }
    )
    let expectedVersion = try #require(
        assignment.split(separator: " ").last.map(String.init)
    )

    #expect(BuildInfo.current.version == expectedVersion)
    #expect(BuildInfo.current.source == "stephenlclarke/devcontainer")
    #expect(!BuildInfo.current.lane.isEmpty)
    #expect(BuildInfo.current.buildType == "development")
    #expect(BuildInfo.current.architecture == "arm64")
    #expect(BuildInfo.current.containerDistribution == "apple")
    #expect(BuildInfo.current.provider == "none")
    let encoded = try JSONEncoder().encode(BuildInfo.current)
    #expect(try JSONDecoder().decode(BuildInfo.self, from: encoded) == BuildInfo.current)
}

@Test
func `identifiers retain their raw values`() {
    #expect(ProjectKey(rawValue: "501:demo").description == "501:demo")
    #expect(RuntimeID(rawValue: "runtime").rawValue == "runtime")
    #expect(DockerID(rawValue: "docker").rawValue == "docker")
    #expect(OperationID.random() != OperationID.random())
    #expect(ExecID.random() != ExecID.random())
}

@Test
func `runtime models round trip through JSON`() throws {
    let spec = ContainerSpec(
        name: "demo",
        image: "alpine:3.22",
        command: ["sleep", "infinity"],
        environment: ["A": "B"],
        labels: ["example": "true"],
        workingDirectory: "/workspace",
        user: "1000:1000",
        hostname: "demo",
        mounts: [
            RuntimeMount(type: .bind, source: "/tmp/source", destination: "/workspace")
        ],
        ports: [
            PortBinding(containerPort: 8080, hostPort: 18080)
        ],
        terminal: true,
        openStandardInput: true
    )
    let snapshot = ContainerSnapshot(
        runtimeID: RuntimeID(rawValue: "runtime"),
        dockerID: DockerID(rawValue: "docker"),
        spec: spec,
        state: .running,
        createdAt: Date(timeIntervalSince1970: 1),
        startedAt: Date(timeIntervalSince1970: 2),
        networkAddresses: ["default": "192.0.2.2"]
    )
    let data = try JSONEncoder().encode(snapshot)
    #expect(try JSONDecoder().decode(ContainerSnapshot.self, from: data) == snapshot)
}

@Test
func `errors include correlation when present`() {
    let error = DevContainerError(
        .invalidRequest,
        message: "invalid",
        correlationID: "correlation"
    )
    #expect(error.description == "invalid [correlation: correlation]")
    #expect(DevContainerError(.notFound, message: "missing").description == "missing")
}

@Test
func `runtime request scope enforces deadlines and cancels owned work`() async {
    let cancellation = CancellationFlag()
    let context = RuntimeRequestContext(
        correlationID: "deadline-fixture",
        deadline: Date().addingTimeInterval(0.02)
    )

    do {
        _ = try await RuntimeRequestScope.$context.withValue(context) {
            try await RuntimeRequestScope.withDeadline {
                try await withTaskCancellationHandler {
                    try await Task.sleep(for: .seconds(10))
                    return "unexpected"
                } onCancel: {
                    cancellation.record()
                }
            }
        }
        Issue.record("expired runtime request unexpectedly completed")
    } catch let error as DevContainerError {
        #expect(error.code == .deadlineExceeded)
        #expect(error.correlationID == "deadline-fixture")
    } catch {
        Issue.record("deadline returned unexpected error \(error)")
    }
    #expect(cancellation.recorded)
}

@Test
func `runtime request scope does not surface cancelled deadline sleeper`() async throws {
    let context = RuntimeRequestContext(
        correlationID: "completed-fixture",
        deadline: Date().addingTimeInterval(10)
    )
    let result = try await RuntimeRequestScope.$context.withValue(context) {
        try await RuntimeRequestScope.withDeadline {
            await Task.yield()
            return "completed"
        }
    }
    #expect(result == "completed")
}

@Test
func `runtime request context rejects cancelled tasks`() async {
    let errorCode = await Task { () -> DevContainerErrorCode? in
        withUnsafeCurrentTask { $0?.cancel() }
        do {
            try RuntimeRequestContext(correlationID: "cancelled-fixture").checkActive()
            return nil
        } catch let error as DevContainerError {
            return error.code
        } catch {
            return nil
        }
    }.value

    #expect(errorCode == .cancelled)
}

private final class CancellationFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var recorded: Bool {
        lock.withLock { value }
    }

    func record() {
        lock.withLock {
            value = true
        }
    }
}
