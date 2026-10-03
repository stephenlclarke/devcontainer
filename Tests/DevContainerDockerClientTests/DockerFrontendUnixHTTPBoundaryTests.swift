// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import ContainerUnixHTTPClient
@testable import DevContainerDockerClient
import Foundation
import Testing

struct DockerFrontendUnixHTTPBoundaryTests {
    @Test
    func `chunk extensions and trailers decode while response head precedes streamed bytes`() async throws {
        let peer = try DockerFrontendRawHTTPPeer(
            response: "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                + "4;name=first\r\nWiki\r\n5;name=second\r\npedia\r\n0\r\nX-Complete: yes\r\n\r\n"
        )
        let capture = HTTPBoundaryCapture()
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            let response = try await client.stream(
                DockerHTTPRequest(method: .get, target: "/chunked"), maximumBodyBytes: 9,
                onResponseHead: { capture.record("head:\($0.status):\($0.body.count)") },
                onBody: { capture.record("body:\(String(data: $0, encoding: .utf8) ?? "<invalid UTF-8>")") }
            )
            #expect(response.status == 200)
            #expect(response.body.isEmpty)
            let events = capture.events
            #expect(events.first == "head:200:0")
            #expect(events.dropFirst().allSatisfy { $0.hasPrefix("body:") })
            #expect(events.dropFirst().map { String($0.dropFirst(5)) }.joined() == "Wikipedia")
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test(arguments: [
        ("Z\r\n", "invalid chunk size"),
        ("1\r\nxZZ\r\n0\r\n\r\n", "invalid chunk terminator"),
        ("3\r\nab", "unexpected end of response"),
        ("FFFFFFFFFFFFFFFFFFFFFFFF\r\n", "invalid chunk size")
    ])
    func `malformed and truncated chunks fail closed`(chunk: String, message: String) async throws {
        let head = "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        let peer = try DockerFrontendRawHTTPPeer(response: head + chunk)
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            await #expect(throws: ContainerUnixHTTPClientError.invalidResponse(message)) {
                try await client.send(DockerHTTPRequest(method: .get, target: "/chunked"))
            }
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test
    func `chunk body limit counts decoded bytes`() async throws {
        let peer = try DockerFrontendRawHTTPPeer(
            response: "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\ndata\r\n0\r\n\r\n"
        )
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            await #expect(throws: ContainerUnixHTTPClientError.responseTooLarge(3)) {
                try await client.send(DockerHTTPRequest(method: .get, target: "/limited"), maximumBodyBytes: 3)
            }
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test(arguments: [
        (Data("not-http\r\n\r\n".utf8), "missing HTTP status"),
        (Data("HTTP/1.1 200 OK\r\nNo-Colon\r\n\r\n".utf8), "malformed header"),
        (
            Data([72, 84, 84, 80, 47, 49, 46, 49, 32, 50, 48, 48, 32, 79, 75, 13, 10, 88, 58, 32, 255, 13, 10, 13, 10]),
            "headers are not UTF-8"
        )
    ])
    func `invalid status header syntax and non UTF8 heads are rejected`(bytes: Data, message: String) async throws {
        let peer = try DockerFrontendRawHTTPPeer(response: bytes)
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            await #expect(throws: ContainerUnixHTTPClientError.invalidResponse(message)) {
                try await client.send(DockerHTTPRequest(method: .get, target: "/headers"))
            }
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test(arguments: ["HEAD", "204", "304"])
    func `bodyless responses do not wait for declared response bodies`(kind: String) async throws {
        let status = kind == "HEAD" ? "200 OK" : "\(kind) No Content"
        let response = "HTTP/1.1 \(status)\r\nContent-Length: 99\r\n\r\n"
        let peer = try DockerFrontendRawHTTPPeer(response: response, echoUntilEOF: true)
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            let method: DockerHTTPMethod = kind == "HEAD" ? .head : .get
            let start = ContinuousClock.now
            if kind == "304" {
                await #expect(throws: ContainerUnixHTTPClientError.server(status: 304, message: "")) {
                    try await client.send(DockerHTTPRequest(method: method, target: "/empty"))
                }
            } else {
                let result = try await client.send(DockerHTTPRequest(method: method, target: "/empty"))
                #expect(result.body.isEmpty)
            }
            #expect(start.duration(to: .now) < .seconds(1))
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test(arguments: ["header", "chunk-line", "trailers"])
    func `headers chunk lines and trailer sections retain their byte bounds`(kind: String) async throws {
        let response: Data
        let expected: Int
        switch kind {
        case "header":
            response = Data(("HTTP/1.1 200 OK\r\nX: " + String(repeating: "h", count: 65540) + "\r\n\r\n").utf8)
            expected = 64 * 1024
        case "chunk-line":
            response = Data(("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0;" +
                    String(repeating: "e", count: 8200) + "\r\n\r\n").utf8)
            expected = 8 * 1024
        default:
            let trailers = (0 ..< 9).map { _ in "X: " + String(repeating: "t", count: 8000) + "\r\n" }.joined()
            response = Data(("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n" +
                    trailers + "\r\n").utf8)
            expected = 64 * 1024
        }
        let peer = try DockerFrontendRawHTTPPeer(response: response)
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            await #expect(throws: ContainerUnixHTTPClientError.responseTooLarge(expected)) {
                try await client.send(DockerHTTPRequest(method: .get, target: "/bounded"))
            }
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test(arguments: [
        ("{\"message\":\"teapot\"}", "teapot"),
        ("{\"message\":\"too large\"}", "too large")
    ])
    func `ordinary errors do not invoke stream callbacks`(body: String, message: String) async throws {
        let data = Data(body.utf8)
        let peer = try DockerFrontendRawHTTPPeer(response: Data(
            ("HTTP/1.1 418 Error\r\nContent-Length: \(data.count)\r\n\r\n" + body).utf8
        ))
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            let capture = HTTPBoundaryCapture()
            await #expect(throws: ContainerUnixHTTPClientError.server(status: 418, message: message)) {
                try await client.stream(
                    DockerHTTPRequest(method: .get, target: "/error"),
                    onResponseHead: { _ in capture.record("head") },
                    onBody: { _ in capture.record("body") }
                )
            }
            #expect(capture.events.isEmpty)
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test
    func `error response diagnostics obey the caller body limit`() async throws {
        let body = "{\"message\":\"teapot\"}"
        let data = Data(body.utf8)
        let peer = try DockerFrontendRawHTTPPeer(response: Data(
            ("HTTP/1.1 418 Error\r\nContent-Length: \(data.count)\r\n\r\n" + body).utf8
        ))
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            await #expect(throws: ContainerUnixHTTPClientError.responseTooLarge(3)) {
                try await client.send(DockerHTTPRequest(method: .get, target: "/error"), maximumBodyBytes: 3)
            }
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test
    func `connection close delimits a response without length metadata`() async throws {
        let peer = try DockerFrontendRawHTTPPeer(response: "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nend")
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath)
            let response = try await client.send(DockerHTTPRequest(method: .get, target: "/eof"))
            #expect(response.body == Data("end".utf8))
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test
    func `absolute deadline applies to an ordinary response that keeps trickling`() async throws {
        let peer = try DockerFrontendRawHTTPPeer(response: "HTTP/1.1 200 OK\r\n\r\n", trickle: true)
        do {
            let client = try DockerFrontendUnixHTTPClient(socketPath: peer.socketPath, timeoutSeconds: 1)
            let start = ContinuousClock.now
            await #expect(throws: DockerFrontendUnixHTTPError.deadlineExceeded) {
                try await client.send(DockerHTTPRequest(method: .get, target: "/slow"))
            }
            #expect(start.duration(to: .now) < .seconds(3))
        } catch {
            await peer.finish()
            throw error
        }
        await peer.finish()
    }

    @Test
    func `nonpositive and unrepresentable request deadlines are rejected at construction`() {
        let invalidTimeout = ContainerUnixHTTPClientError.invalidResponse(
            "timeout must be a positive representable duration"
        )
        #expect(throws: invalidTimeout) {
            try DockerFrontendUnixHTTPClient(socketPath: "/tmp/socket", timeoutSeconds: 0)
        }
        #expect(throws: invalidTimeout) {
            try DockerFrontendUnixHTTPClient(socketPath: "/tmp/socket", timeoutSeconds: Int.max)
        }
    }
}

private final class HTTPBoundaryCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String] = []

    func record(_ value: String) {
        lock.withLock { storage.append(value) }
    }

    var events: [String] {
        lock.withLock { storage }
    }
}
