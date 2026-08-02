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

import ContainerEngineWire
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public typealias DockerHTTPBody = ContainerEngineWire.DockerHTTPBody
public typealias DockerHTTPHeaders = ContainerEngineWire.DockerHTTPHeaders
public typealias DockerHTTPMethod = ContainerEngineWire.DockerHTTPMethod
public typealias DockerHTTPRequest = ContainerEngineWire.DockerHTTPRequest
public typealias DockerHTTPResponder = ContainerEngineWire.DockerHTTPResponder
public typealias DockerHTTPResponse = ContainerEngineWire.DockerHTTPResponse
public typealias DockerJSON = ContainerEngineWire.DockerJSON

public extension DockerHTTPRequest {
    /// Source-compatible bridge for callers whose headers are already unique.
    init(
        method: DockerHTTPMethod,
        target: String,
        headers: [String: String],
        body: Data = Data()
    ) {
        let fields = headers.map {
            DockerHTTPHeaders.Field(name: $0.key, value: $0.value)
        }.sorted {
            let lhs = $0.name.lowercased()
            let rhs = $1.name.lowercased()
            return lhs == rhs ? $0.name < $1.name : lhs < rhs
        }
        self.init(
            method: method,
            target: target,
            headers: DockerHTTPHeaders(fields),
            body: body
        )
    }

    /// Legacy convenience now fails closed when a field is repeated.
    func header(_ name: String) -> String? {
        try? uniqueHeader(name)
    }
}

public struct DockerRuntimeHijackSession: DockerHijackSession {
    private let session: any RuntimeProcessSession

    public init(_ session: any RuntimeProcessSession) {
        self.session = session
    }

    public var frames: AsyncThrowingStream<DockerStreamFrame, any Error> {
        let session = session
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await frame in session.frames {
                        let channel: DockerStreamChannel = switch frame.channel {
                        case .standardInput:
                            .standardInput
                        case .standardOutput:
                            .standardOutput
                        case .standardError:
                            .standardError
                        }
                        continuation.yield(
                            DockerStreamFrame(
                                channel: channel,
                                data: frame.data
                            )
                        )
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    public func write(_ data: Data) async throws {
        try await session.write(data)
    }

    public func closeStandardInput() async throws {
        try await session.closeStandardInput()
    }

    public func wait() async throws -> Int32 {
        try await session.wait()
    }

    public func cancel() async {
        await session.cancel()
    }
}

public enum DockerStreamFraming {
    public static func encode(_ frame: RuntimeIOFrame, terminal: Bool) -> Data {
        let channel: DockerStreamChannel = switch frame.channel {
        case .standardInput:
            .standardInput
        case .standardOutput:
            .standardOutput
        case .standardError:
            .standardError
        }
        if terminal {
            return frame.data
        }
        precondition(frame.data.count <= Int(UInt32.max))
        var result = Data(capacity: frame.data.count + 8)
        result.append(channel.rawValue)
        result.append(contentsOf: [0, 0, 0])
        var size = UInt32(frame.data.count).bigEndian
        withUnsafeBytes(of: &size) { result.append(contentsOf: $0) }
        result.append(frame.data)
        return result
    }
}
