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

@testable import DevContainerDockerCLI
import Foundation

final class StubTransport:
    DockerEngineHijackTransport,
    DockerEngineRequestNotificationTransport,
    @unchecked Sendable
{
    struct StubResponse {
        var status: Int
        var headers: [String: String]
        var body: Data
        var target: String?

        init(
            status: Int,
            headers: [String: String] = [:],
            body: Data = Data(),
            target: String? = nil
        ) {
            self.status = status
            self.headers = headers
            self.body = body
            self.target = target
        }

        static func json(
            _ object: Any,
            status: Int = 200,
            target: String? = nil
        ) -> StubResponse {
            guard
                let body = try? JSONSerialization.data(
                    withJSONObject: object,
                    options: [.sortedKeys]
                )
            else {
                fatalError("Stub JSON must be encodable")
            }
            return StubResponse(
                status: status,
                headers: ["content-type": "application/json"],
                body: body,
                target: target
            )
        }
    }

    private let lock = NSLock()
    private var responses: [StubResponse]
    private var recordedRequests: [DockerHTTPRequest] = []
    private var recordedHijackInput: Data?

    init(_ responses: [StubResponse]) {
        self.responses = responses
    }

    var requests: [DockerHTTPRequest] {
        lock.withLock { recordedRequests }
    }

    var hijackInput: Data? {
        lock.withLock { recordedHijackInput }
    }

    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes _: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        try respond(to: request, onBody: onBody)
    }

    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes _: Int?,
        onRequestSent: @escaping @Sendable () -> Void,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        try respond(to: request, onRequestSent: onRequestSent, onBody: onBody)
    }

    func hijack(
        _ request: DockerHTTPRequest,
        input: Data?,
        inputFileDescriptor _: Int32?,
        maximumBodyBytes _: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        lock.withLock { recordedHijackInput = input }
        return try respond(to: request, onBody: onBody)
    }

    private func respond(
        to request: DockerHTTPRequest,
        onRequestSent: @escaping @Sendable () -> Void = {},
        onBody: (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        let response = try lock.withLock { () throws -> StubResponse in
            recordedRequests.append(request)
            guard !responses.isEmpty else {
                throw DockerHTTPClientError.invalidResponse("no stub response")
            }
            if responses[0].target == nil || responses[0].target == request.target {
                return responses.removeFirst()
            }
            guard let index = responses.firstIndex(where: { $0.target == request.target }) else {
                throw DockerHTTPClientError.invalidResponse(
                    "no stub response for \(request.target)"
                )
            }
            return responses.remove(at: index)
        }
        onRequestSent()
        try onBody(response.body)
        return DockerHTTPResponse(status: response.status, headers: response.headers, body: Data())
    }
}
