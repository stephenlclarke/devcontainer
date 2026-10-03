// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
@testable import DevContainerState
import Foundation
import Testing

struct JSONFileLogEncoderTests {
    private static let vectors: [([UInt8], [UInt8])] = [
        ([UInt8]([0, 9, 13, 10]), [UInt8]([0, 9, 13, 10])),
        (
            [0xC2, 0xA2, 0xE2, 0x82, 0xAC, 0xF0, 0x9F, 0x98, 0x80],
            [0xC2, 0xA2, 0xE2, 0x82, 0xAC, 0xF0, 0x9F, 0x98, 0x80]
        ),
        ([0xE2, 0x82], [0xEF, 0xBF, 0xBD, 0xEF, 0xBF, 0xBD]),
        ([0xC0, 0xAF], [0xEF, 0xBF, 0xBD, 0xEF, 0xBF, 0xBD]),
        ([0xED, 0xA0, 0x80], Array(repeating: [UInt8]([0xEF, 0xBF, 0xBD]), count: 3).flatMap(\.self)),
        ([0xF4, 0x90, 0x80, 0x80], Array(repeating: [UInt8]([0xEF, 0xBF, 0xBD]), count: 4).flatMap(\.self)),
        ([0xF0, 0x80, 0x80, 0x80], Array(repeating: [UInt8]([0xEF, 0xBF, 0xBD]), count: 4).flatMap(\.self)),
        ([0xFF, 65], [0xEF, 0xBF, 0xBD, 65])
    ]

    @Test(arguments: vectors)
    func `pinned json file UTF8 vectors are independent of raw frames`(_ vector: ([UInt8], [UInt8])) throws {
        var encoder = JSONFileLogEncoder(terminal: false)
        var output = Data()
        // Deliberately split every valid multi-byte sequence across raw reads.
        for byte in vector.0 {
            for frame in try encoder.append(.init(channel: .standardOutput, data: Data([byte]))) {
                output.append(frame.data)
            }
        }
        for frame in try encoder.end(.standardOutput) {
            output.append(frame.data)
        }
        #expect(output == Data(vector.1))
        #expect(!encoder.complete)
        #expect(try encoder.end(.standardError).isEmpty)
        #expect(encoder.complete)
        #expect(throws: DevContainerModel.DevContainerError.self) { try encoder.end(.standardOutput) }
        #expect(throws: DevContainerModel.DevContainerError.self) {
            try encoder.append(.init(channel: .standardOutput, data: Data([1])))
        }
    }

    @Test func `partial record limit splits UTF8 but raw frame boundaries do not`() throws {
        var encoder = JSONFileLogEncoder(terminal: true)
        var bytes = Data(repeating: 65, count: 16383)
        bytes.append(contentsOf: [0xE2, 0x82, 0xAC])
        let prefix = try encoder.append(.init(channel: .standardOutput, data: bytes))
        #expect(prefix.map(\.data) == [Data(repeating: 65, count: 16383) + Data([0xEF, 0xBF, 0xBD])])
        #expect(try encoder.end(.standardOutput).map(\.data) == [Data([0xEF, 0xBF, 0xBD, 0xEF, 0xBF, 0xBD])])
        #expect(encoder.complete)
    }

    @Test func `source and EOF boundaries never combine truncated UTF8`() throws {
        var encoder = JSONFileLogEncoder(terminal: false)
        #expect(try encoder.append(.init(channel: .standardOutput, data: Data([0xE2, 0x82]))).isEmpty)
        #expect(try encoder.append(.init(channel: .standardError, data: Data([0xAC]))).isEmpty)
        #expect(try encoder.end(.standardOutput).map(\.data) == [Data([0xEF, 0xBF, 0xBD, 0xEF, 0xBF, 0xBD])])
        #expect(try encoder.end(.standardError).map(\.data) == [Data([0xEF, 0xBF, 0xBD])])
    }

    @Test func `newline heavy records coalesce and expanded binary frames stay bounded`() throws {
        var encoder = JSONFileLogEncoder(terminal: false)
        let newlines = Data(repeating: 10, count: 65536)
        #expect(try encoder.append(.init(channel: .standardOutput, data: newlines)).map(\.data) == [newlines])
        let binary = try encoder.append(.init(channel: .standardError, data: Data(repeating: 255, count: 65536)))
        #expect(binary.count == 3)
        #expect(binary.allSatisfy { $0.data.count == 65536 && $0.channel == .standardError })
        let expected = Data(Array(repeating: [UInt8]([0xEF, 0xBF, 0xBD]), count: 65536).flatMap(\.self))
        #expect(binary.reduce(Data()) { $0 + $1.data } == expected)
    }
}
