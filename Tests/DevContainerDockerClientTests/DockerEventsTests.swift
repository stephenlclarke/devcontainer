// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import ContainerUnixHTTPClient
@testable import DevContainerDockerClient
import Foundation
import Testing

struct DockerEventsTests {
    @Test
    func `events preserves repeated filters and escapes query delimiters`() throws {
        let spec = try command([
            "--format",
            "{{json .}}",
            "--filter",
            "event=start",
            "-f",
            "label=a=x&y",
            "--filter=event=die"
        ])
        #expect(spec.filters == ["event": ["start", "die"], "label": ["a=x&y"]])
        #expect(try spec.request()
            .target ==
            "/events?filters=%7B%22event%22%3A%5B%22start%22%2C%22die%22%5D%2C%22label%22%3A%5B%22a%3Dx%26y%22%5D%7D")
        #expect(try command(["--format={{json .}}"]).filters.isEmpty)
    }

    @Test(arguments: [
        [String](), ["--format", "text"], ["--format={{json .}}", "--format={{json .}}"],
        ["--format={{json .}}", "--filter=event="], ["--format={{json .}}", "--filter=label"],
        ["--format={{json .}}", "--filter=type=container"], ["--format={{json .}}", "--filter=event=x\0"],
        ["--format={{json .}}", "--since", "0"]
    ])
    func `unsupported event options fail before connecting`(_ args: [String]) {
        #expect(throws: DockerFrontendError.self) { try command(args) }
    }

    @Test
    func `event decoder preserves exact JSON across every byte boundary`() throws {
        let data = Data("{\"timeNano\":9223372036854775807,\"Action\":\"start\"}\n{\"x\":1}\n".utf8)
        for split in 0 ... data.count {
            let decoder = DockerEventLines()
            var output = Data()
            try decoder.consume(Data(data.prefix(split))) { output.append($0) }
            try decoder.consume(Data(data.dropFirst(split))) { output.append($0) }
            #expect(try decoder.finish() == nil)
            #expect(output == data)
        }
        let tail = DockerEventLines()
        try tail.consume(Data("{\"last\":true}".utf8)) { _ in Issue.record("unterminated record emitted too early") }
        #expect(try tail.finish() == Data("{\"last\":true}\n".utf8))
        #expect(try tail.finish() == nil)
    }

    @Test(arguments: ["[]\n", "null\n", "broken\n", "\n", "{", "{\"x\":"])
    func `invalid event records are never emitted`(_ input: String) {
        let decoder = DockerEventLines()
        #expect(throws: DockerFrontendError.self) {
            try decoder.consume(Data(input.utf8)) { _ in Issue.record("invalid record emitted") }
            _ = try decoder.finish()
        }
    }

    @Test
    func `event decoder bounds both transport chunks and individual records`() throws {
        let decoder = DockerEventLines()
        #expect(throws: DockerFrontendError.self) {
            try decoder.consume(Data(repeating: 32, count: 65537)) { _ in Issue.record("oversize chunk emitted") }
        }
        let chunk = Data(repeating: 32, count: 65536)
        for _ in 0 ..< 16 {
            try decoder.consume(chunk) { _ in Issue.record("incomplete record emitted") }
        }
        #expect(throws: DockerFrontendError.self) {
            try decoder.consume(Data([32])) { _ in Issue.record("oversize record emitted") }
        }
        let newline = DockerEventLines()
        for _ in 0 ..< 16 {
            try newline.consume(chunk) { _ in Issue.record("incomplete record emitted") }
        }
        #expect(throws: DockerFrontendError.self) {
            try newline.consume(Data([32, 10])) { _ in Issue.record("oversize terminated record emitted") }
        }
    }

    @Test
    func `events reaches stdout without buffering the full stream`() async throws {
        let pipe = Pipe()
        defer { try? pipe.fileHandleForWriting.close(); try? pipe.fileHandleForReading.close() }
        let writer = try DockerFrontendOutput(descriptor: pipe.fileHandleForWriting.fileDescriptor)
        let transport = EventTransport(chunks: [Data("{\"one\":1}\n{\"two\":2}".utf8)])
        try await DockerFrontend(version: "test").executeEvents(
            command(["--format={{json .}}"]),
            transport: transport,
            output: writer
        )
        #expect(try pipe.fileHandleForReading.read(upToCount: 20) == Data("{\"one\":1}\n{\"two\":2}\n".utf8))
    }

    @Test
    func `events cancellation interrupts a full output pipe and joins the callback`() async throws {
        let pipe = Pipe()
        defer { try? pipe.fileHandleForWriting.close(); try? pipe.fileHandleForReading.close() }
        let writer = try DockerFrontendOutput(descriptor: pipe.fileHandleForWriting.fileDescriptor)
        let record = Data(("{\"data\":\"" + String(repeating: "x", count: 60000) + "\"}\n").utf8)
        let transport = EventTransport(chunks: Array(repeating: record, count: 20))
        let start = ContinuousClock.now
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "test", executionTimeout: .milliseconds(60)).executeEvents(
                command(["--format={{json .}}"]), transport: transport, output: writer
            )
        }
        #expect(start.duration(to: .now) < .seconds(2))
    }

    private func command(_ args: [String]) throws -> DockerEventsCommand {
        guard case let .events(spec) = try DockerFrontendCommand.parse(["events"] + args) else {
            throw DockerFrontendError.usage("not events")
        }
        return spec
    }
}

private struct EventTransport: DockerFrontendEventTransport {
    let chunks: [Data]
    func events(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws {
        #expect(request.method == .get)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
            DispatchQueue.global().async {
                continuation.resume(with: Result { for chunk in chunks {
                    try onBody(chunk)
                } })
            }
        }
    }
}
