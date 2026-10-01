// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import DevContainerDockerClient
import Foundation
import Testing

struct DockerMultiplexTests {
    @Test
    func `multiplex headers and binary payload survive every split`() throws {
        let first = Data([0, 255, 42, 10])
        let second = Data("error".utf8)
        let bytes = try DockerStreamFraming.encode(.init(channel: .standardOutput, data: first), terminal: false)
            + DockerStreamFraming.encode(.init(channel: .standardError, data: second), terminal: false)
        for split in 0 ... bytes.count {
            var decoder = DockerMultiplexDecoder()
            let frames = try decoder.consume(bytes.prefix(split)) + decoder.consume(bytes.dropFirst(split))
            try decoder.finish()
            #expect(frames.filter { $0.channel == .standardOutput }.reduce(Data()) { $0 + $1.data } == first)
            #expect(frames.filter { $0.channel == .standardError }.reduce(Data()) { $0 + $1.data } == second)
        }
        var decoder = DockerMultiplexDecoder()
        #expect(try decoder.consume(Data([1, 0, 0, 0, 0, 0, 0, 0])).isEmpty)
        try decoder.finish()
    }

    @Test(arguments: [Data([4, 0, 0, 0, 0, 0, 0, 0]), Data([1, 2, 0, 0, 0, 0, 0, 0])])
    func `invalid channel or reserved bits are not output`(bytes: Data) {
        var decoder = DockerMultiplexDecoder()
        #expect(throws: (any Error).self) { try decoder.consume(bytes) }
    }

    @Test(arguments: [Data([1]), Data([1, 0, 0, 0, 0, 0, 0, 2, 65])])
    func `truncated header or payload is not clean EOF`(bytes: Data) throws {
        var decoder = DockerMultiplexDecoder()
        _ = try decoder.consume(bytes)
        #expect(throws: (any Error).self) { try decoder.finish() }
    }
}
