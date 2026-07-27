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
import DevContainerRuntimeSPI
import Foundation

public enum DockerHTTPMethod: String, Sendable {
    case delete = "DELETE"
    case get = "GET"
    case head = "HEAD"
    case post = "POST"
    case put = "PUT"
}

public struct DockerHTTPRequest: Sendable {
    public var method: DockerHTTPMethod
    public var target: String
    public var headers: [String: String]
    public var body: Data

    public init(
        method: DockerHTTPMethod,
        target: String,
        headers: [String: String] = [:],
        body: Data = Data()
    ) {
        self.method = method
        self.target = target
        self.headers = headers
        self.body = body
    }

    public func header(_ name: String) -> String? {
        let lowercased = name.lowercased()
        return headers.first { $0.key.lowercased() == lowercased }?.value
    }
}

public enum DockerHTTPBody: Sendable {
    case bytes(Data)
    case stream(AsyncThrowingStream<Data, any Error>)
    case hijack(any RuntimeProcessSession, terminal: Bool)
}

public struct DockerHTTPResponse: Sendable {
    public var status: Int
    public var headers: [String: String]
    public var body: DockerHTTPBody

    public init(
        status: Int,
        headers: [String: String] = [:],
        body: DockerHTTPBody = .bytes(Data())
    ) {
        self.status = status
        self.headers = headers
        self.body = body
    }

    public static func empty(status: Int) -> DockerHTTPResponse {
        DockerHTTPResponse(status: status)
    }

    public static func text(
        _ text: String,
        status: Int = 200,
        contentType: String = "text/plain; charset=utf-8"
    ) -> DockerHTTPResponse {
        DockerHTTPResponse(
            status: status,
            headers: ["Content-Type": contentType],
            body: .bytes(Data(text.utf8))
        )
    }

    public static func json(
        _ value: some Encodable,
        status: Int = 200,
        encoder: JSONEncoder = DockerJSON.encoder
    ) throws -> DockerHTTPResponse {
        try DockerHTTPResponse(
            status: status,
            headers: ["Content-Type": "application/json"],
            body: .bytes(encoder.encode(value))
        )
    }
}

public enum DockerJSON {
    public static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    public static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

public enum DockerStreamFraming {
    public static func encode(_ frame: RuntimeIOFrame, terminal: Bool) -> Data {
        if terminal {
            return frame.data
        }

        var result = Data(capacity: frame.data.count + 8)
        result.append(frame.channel.rawValue)
        result.append(contentsOf: [0, 0, 0])
        var size = UInt32(frame.data.count).bigEndian
        withUnsafeBytes(of: &size) { result.append(contentsOf: $0) }
        result.append(frame.data)
        return result
    }
}
