// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation

/// The pinned json-file logger encodes each LF/16 KiB/EOF record separately.
/// Raw read boundaries are not log boundaries. Live output never uses this encoder.
struct JSONFileLogEncoder {
    private var pending: [RuntimeIOChannel: [UInt8]] = [:]
    private var ended: Set<RuntimeIOChannel> = []
    let sources: Set<RuntimeIOChannel>

    init(terminal: Bool) {
        sources = terminal ? [.standardOutput] : [.standardOutput, .standardError]
    }

    var complete: Bool {
        ended == sources
    }

    mutating func append(_ frame: RuntimeIOFrame) throws -> [RuntimeIOFrame] {
        try requireOpen(frame.channel)
        var buffer = pending[frame.channel, default: []]
        var encoded = Data()
        for byte in frame.data {
            buffer.append(byte)
            if byte == 10 || buffer.count == 16384 {
                encoded.append(Self.encode(buffer))
                buffer.removeAll(keepingCapacity: true)
            }
        }
        pending[frame.channel] = buffer
        return Self.frames(encoded, channel: frame.channel)
    }

    mutating func end(_ channel: RuntimeIOChannel) throws -> [RuntimeIOFrame] {
        try requireOpen(channel)
        ended.insert(channel)
        let bytes = pending.removeValue(forKey: channel) ?? []
        return Self.frames(Self.encode(bytes), channel: channel)
    }

    private func requireOpen(_ channel: RuntimeIOChannel) throws {
        guard sources.contains(channel), !ended.contains(channel) else {
            throw SQLiteOutputConnection.failure("Log source is invalid or already ended")
        }
    }

    private static func frames(_ data: Data, channel: RuntimeIOChannel) -> [RuntimeIOFrame] {
        // Coalesce completed records without changing their bytes. This bounds
        // row overhead even when every input byte is a newline.
        stride(from: 0, to: data.count, by: 65536).map { offset in
            RuntimeIOFrame(channel: channel, data: data.subdata(in: offset ..< min(offset + 65536, data.count)))
        }
    }

    private static func encode(_ bytes: [UInt8]) -> Data {
        var result = Data()
        result.reserveCapacity(bytes.count)
        var index = 0
        while index < bytes.count {
            let width = validWidth(bytes, at: index)
            if width == 0 {
                // Go's JSON encoder consumes exactly one invalid byte, even
                // for a truncated multi-byte prefix; Swift's decoder differs.
                result.append(contentsOf: [0xEF, 0xBF, 0xBD])
                index += 1
            } else {
                result.append(contentsOf: bytes[index ..< (index + width)])
                index += width
            }
        }
        return result
    }

    private static func validWidth(_ bytes: [UInt8], at index: Int) -> Int {
        let first = bytes[index]
        if first < 0x80 {
            return 1
        }
        let width: Int
        switch first {
        case 0xC2 ... 0xDF: width = 2
        case 0xE0 ... 0xEF: width = 3
        case 0xF0 ... 0xF4: width = 4
        default: return 0
        }
        guard index + width <= bytes.count else { return 0 }
        for offset in 1 ..< width where !(0x80 ... 0xBF).contains(bytes[index + offset]) {
            return 0
        }
        let second = bytes[index + 1]
        if (first == 0xE0 && second < 0xA0) || (first == 0xED && second >= 0xA0)
            || (first == 0xF0 && second < 0x90) || (first == 0xF4 && second >= 0x90)
        {
            return 0
        }
        return width
    }
}
