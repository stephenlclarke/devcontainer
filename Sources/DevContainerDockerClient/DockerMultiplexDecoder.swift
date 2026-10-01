// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Foundation

/// Incrementally decodes Docker's eight-byte non-TTY frame headers without
/// buffering a frame payload. Memory stays proportional to the input chunk.
public struct DockerMultiplexDecoder {
    private var header = Data()
    private var remaining: UInt32 = 0
    private var channel = DockerStreamChannel.standardOutput

    public init() {}

    public mutating func consume(_ bytes: Data) throws -> [DockerStreamFrame] {
        var offset = bytes.startIndex
        var output: [DockerStreamFrame] = []
        while offset < bytes.endIndex {
            if remaining == 0 {
                let size = min(8 - header.count, bytes.distance(from: offset, to: bytes.endIndex))
                let end = bytes.index(offset, offsetBy: size)
                header.append(bytes[offset ..< end])
                offset = end
                guard header.count == 8 else { break }
                guard let channel = DockerStreamChannel(rawValue: header[0]),
                      channel == .standardOutput || channel == .standardError,
                      header[1 ..< 4].allSatisfy({ $0 == 0 })
                else {
                    throw DockerFrontendError.invalidResponse("invalid Docker output frame")
                }
                self.channel = channel
                remaining = header[4 ..< 8].reduce(0) { ($0 << 8) | UInt32($1) }
                header.removeAll(keepingCapacity: true)
            }
            let size = min(Int(remaining), bytes.distance(from: offset, to: bytes.endIndex))
            if size > 0 {
                let end = bytes.index(offset, offsetBy: size)
                output.append(.init(channel: channel, data: Data(bytes[offset ..< end])))
                remaining -= UInt32(size)
                offset = end
            }
        }
        return output
    }

    public func finish() throws {
        guard header.isEmpty, remaining == 0 else {
            throw DockerFrontendError.invalidResponse("truncated Docker output frame")
        }
    }
}
