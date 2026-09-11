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

extension DockerCLIApplication {
    func runAttachedContainer(
        _ identifier: String,
        terminal: Bool,
        standardInput: Data?,
        standardInputFileDescriptor: Int32?,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        guard let transport = transport as? any DockerEngineHijackTransport else {
            throw DockerCLIError.unsupported("run --interactive transport")
        }
        let wait = concurrentWait(for: identifier)
        var decoder = DockerMultiplexedStreamDecoder(terminal: terminal)
        var standardOutput = Data()
        var standardError = Data()
        let request = DockerHTTPRequest(
            method: "POST",
            target: Self.target(
                "/containers/\(Self.path(identifier))/attach",
                query: [
                    ("logs", "true"), ("stream", "true"), ("stdin", "true"),
                    ("stdout", "true"), ("stderr", "true"), ("start", "true")
                ]
            ),
            headers: ["Connection": "Upgrade", "Upgrade": "tcp"]
        )
        _ = try transport.hijack(
            request,
            input: standardInput,
            inputFileDescriptor: standardInputFileDescriptor,
            maximumBodyBytes: nil
        ) { chunk in
            try decoder.append(chunk) { data, isStandardError in
                if let streamingOutput {
                    try streamingOutput(data, isStandardError)
                } else if isStandardError {
                    standardError.append(data)
                } else {
                    standardOutput.append(data)
                }
            }
        }
        try decoder.finish()
        let response = try wait.load().get()
        let status = try (Self.object(response.body)["StatusCode"] as? NSNumber)?.int32Value ?? 1
        return DockerCLIResult(
            standardOutput: standardOutput,
            standardError: standardError,
            exitCode: status
        )
    }
}

final class DockerRequestStartSignal: @unchecked Sendable {
    private let lock = NSLock()
    private let semaphore = DispatchSemaphore(value: 0)
    private var signalled = false

    func signal() {
        let shouldSignal = lock.withLock {
            guard !signalled else { return false }
            signalled = true
            return true
        }
        if shouldSignal {
            semaphore.signal()
        }
    }

    func wait() {
        semaphore.wait()
    }
}

final class DockerConcurrentResponse: @unchecked Sendable {
    private let group = DispatchGroup()
    private let lock = NSLock()
    private var result: Result<DockerHTTPResponse, any Error>?

    init() {
        group.enter()
    }

    func store(_ result: Result<DockerHTTPResponse, any Error>) {
        lock.withLock {
            self.result = result
        }
        group.leave()
    }

    func load() -> Result<DockerHTTPResponse, any Error> {
        group.wait()
        return lock.withLock {
            result ?? .failure(
                DockerCLIError.malformedResponse(
                    "container wait completed without a response"
                )
            )
        }
    }
}

struct DockerMultiplexedStreamDecoder {
    private var buffer = Data()
    private let terminal: Bool

    init(terminal: Bool = false) {
        self.terminal = terminal
    }

    mutating func append(
        _ data: Data,
        handler: (Data, Bool) throws -> Void
    ) throws {
        if terminal {
            try handler(data, false)
            return
        }
        buffer.append(data)
        while buffer.count >= 8 {
            let channel = buffer[buffer.startIndex]
            let lengthBytes = buffer[
                buffer.startIndex.advanced(by: 4) ..< buffer.startIndex.advanced(by: 8)
            ]
            let length = lengthBytes.reduce(0) { ($0 << 8) | Int($1) }
            guard length <= 64 * 1024 * 1024 else {
                throw DockerCLIError.malformedResponse("stream frame is too large")
            }
            guard buffer.count >= 8 + length else { return }
            let start = buffer.startIndex.advanced(by: 8)
            try handler(Data(buffer[start ..< start.advanced(by: length)]), channel == 2)
            buffer.removeFirst(8 + length)
        }
    }

    func finish() throws {
        guard terminal || buffer.isEmpty else {
            throw DockerCLIError.malformedResponse("truncated stream frame")
        }
    }
}
