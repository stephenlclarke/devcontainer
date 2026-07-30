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
import Foundation

private struct TarValidationState {
    var offset = 0
    var entries = 0
    var expandedSize: UInt64 = 0
    var pendingPath: String?
    var pendingLinkPath: String?
    var pax: [String: String] = [:]
}

private struct ParsedTarEntry {
    let size: UInt64
    let type: UInt8
    let headerName: String
    let body: Data
    let nextOffset: Int
}

public enum TarArchiveValidator {
    public static let maximumEntries = 100_000
    public static let maximumExpandedSize: UInt64 = 2_147_483_648

    public static func validatedForExtraction(_ archive: Data) throws -> Data {
        try validate(archive)
        let remainder = archive.count % 512
        guard remainder != 0 else {
            return archive
        }
        var normalized = archive
        normalized.append(Data(repeating: 0, count: 512 - remainder))
        return normalized
    }

    @discardableResult
    public static func validate(_ archive: Data) throws -> Int {
        var state = TarValidationState()

        while state.offset + 512 <= archive.count {
            let header = archive.subdata(in: state.offset ..< state.offset + 512)
            if header.allSatisfy({ $0 == 0 }) {
                return state.entries
            }
            let entry = try parseEntry(header, from: archive, offset: state.offset)
            try consume(entry, header: header, state: &state)
            state.offset = entry.nextOffset
            if state.offset == archive.count {
                return state.entries
            }
        }

        if state.entries > 0,
           state.offset < archive.count,
           archive[state.offset...].allSatisfy({ $0 == 0 })
        {
            return state.entries
        }

        throw invalid("tar archive ended before a complete header")
    }

    private static func parseEntry(
        _ header: Data,
        from archive: Data,
        offset: Int
    ) throws -> ParsedTarEntry {
        try validateChecksum(header)
        let size = try number(header, range: 124 ..< 136, field: "size")
        let bodyStart = offset + 512
        guard
            size <= UInt64(Int.max),
            bodyStart <= archive.count,
            Int(size) <= archive.count - bodyStart
        else {
            throw invalid("tar entry body exceeds the archive")
        }
        let bodyEnd = bodyStart + Int(size)
        let paddedSize = (Int(size) + 511) & ~511
        let paddedEnd = bodyStart + paddedSize
        let nextOffset: Int
        if paddedEnd <= archive.count {
            nextOffset = paddedEnd
        } else {
            guard archive[bodyEnd...].allSatisfy({ $0 == 0 }) else {
                throw invalid("tar entry padding is not zero filled")
            }
            nextOffset = archive.count
        }
        return try ParsedTarEntry(
            size: size,
            type: header[156],
            headerName: joinedName(header),
            body: archive.subdata(in: bodyStart ..< bodyEnd),
            nextOffset: nextOffset
        )
    }

    private static func consume(
        _ entry: ParsedTarEntry,
        header: Data,
        state: inout TarValidationState
    ) throws {
        switch entry.type {
        case 0, 48, 49, 50, 53:
            try consumeContent(entry, header: header, state: &state)
        case 103:
            _ = try parsePAX(entry.body)
        case 120:
            state.pax = try parsePAX(entry.body)
        case 76:
            state.pendingPath = try nulTerminated(entry.body)
        case 75:
            state.pendingLinkPath = try nulTerminated(entry.body)
        case 51, 52, 54:
            throw invalid("tar device and FIFO entries are not allowed")
        default:
            throw invalid("unsupported tar entry type \(entry.type)")
        }
    }

    private static func consumeContent(
        _ entry: ParsedTarEntry,
        header: Data,
        state: inout TarValidationState
    ) throws {
        let path = state.pax["path"] ?? state.pendingPath ?? entry.headerName
        let linkPath = try state.pax["linkpath"] ?? state.pendingLinkPath
            ?? string(header, range: 157 ..< 257)
        try validatePath(path, field: "entry path")
        if entry.type == 50 {
            try validateSymbolicLink(linkPath, from: path)
        } else if entry.type == 49 {
            try validatePath(linkPath, field: "hard-link target")
        }
        state.entries += 1
        state.expandedSize = try adding(state.expandedSize, entry.size)
        guard state.entries <= maximumEntries else {
            throw invalid("tar archive exceeds \(maximumEntries) entries")
        }
        guard state.expandedSize <= maximumExpandedSize else {
            throw invalid("tar archive expands beyond \(maximumExpandedSize) bytes")
        }
        state.pendingPath = nil
        state.pendingLinkPath = nil
        state.pax = [:]
    }

    private static func validateChecksum(_ header: Data) throws {
        let expected = try number(header, range: 148 ..< 156, field: "checksum")
        var actual: UInt64 = 0
        for index in header.indices {
            actual += UInt64((148 ..< 156).contains(index) ? 32 : header[index])
        }
        guard actual == expected else {
            throw invalid("tar header checksum is invalid")
        }
    }

    private static func joinedName(_ header: Data) throws -> String {
        let name = try string(header, range: 0 ..< 100)
        let prefix = try string(header, range: 345 ..< 500)
        return prefix.isEmpty ? name : "\(prefix)/\(name)"
    }

    private static func number(
        _ data: Data,
        range: Range<Int>,
        field: String
    ) throws -> UInt64 {
        let bytes = data[range]
        guard bytes.first.map({ $0 & 0x80 == 0 }) ?? false else {
            throw invalid("base-256 tar \(field) is not supported")
        }
        guard let decoded = String(bytes: bytes, encoding: .utf8) else {
            throw invalid("tar \(field) is not valid UTF-8")
        }
        let value = decoded.trimmingCharacters(
            in: CharacterSet(charactersIn: "\0 ")
        )
        if value.isEmpty {
            return 0
        }
        guard let result = UInt64(value, radix: 8) else {
            throw invalid("tar \(field) is not valid octal")
        }
        return result
    }

    private static func string(
        _ data: Data,
        range: Range<Int>
    ) throws -> String {
        try nulTerminated(data.subdata(in: range))
    }

    private static func nulTerminated(_ data: Data) throws -> String {
        let end = data.firstIndex(of: 0) ?? data.endIndex
        guard let value = String(bytes: data[..<end], encoding: .utf8) else {
            throw invalid("tar text field is not valid UTF-8")
        }
        return value.trimmingCharacters(in: .newlines)
    }

    private static func parsePAX(_ body: Data) throws -> [String: String] {
        var result: [String: String] = [:]
        var offset = 0
        while offset < body.count {
            guard let space = body[offset...].firstIndex(of: 32) else {
                throw invalid("PAX record has no length delimiter")
            }
            guard
                let lengthText = String(
                    bytes: body[offset ..< space],
                    encoding: .utf8
                ),
                let length = Int(lengthText),
                length > 0,
                length <= body.count - offset
            else {
                throw invalid("PAX record has an invalid length")
            }
            let recordEnd = offset + length
            guard body[recordEnd - 1] == 10 else {
                throw invalid("PAX record is not newline terminated")
            }
            let valueStart = space + 1
            guard let record = String(
                bytes: body[valueStart ..< recordEnd - 1],
                encoding: .utf8
            ) else {
                throw invalid("PAX record is not valid UTF-8")
            }
            guard let equals = record.firstIndex(of: "=") else {
                throw invalid("PAX record has no key/value delimiter")
            }
            result[String(record[..<equals])] = String(record[record.index(after: equals)...])
            offset = recordEnd
        }
        return result
    }

    private static func validatePath(_ path: String, field: String) throws {
        guard !path.isEmpty, !path.hasPrefix("/"), !path.contains("\0") else {
            throw invalid("\(field) must be a non-empty relative path")
        }
        let components = path.split(separator: "/", omittingEmptySubsequences: false)
        guard components.enumerated().allSatisfy({ index, component in
            if component == ".." {
                return false
            }
            if component.isEmpty {
                return index == components.index(before: components.endIndex)
                    && path.hasSuffix("/")
            }
            return true
        }) else {
            throw invalid("\(field) contains an empty or parent component")
        }
    }

    private static func validateSymbolicLink(_ target: String, from path: String) throws {
        guard !target.isEmpty, !target.hasPrefix("/"), !target.contains("\0") else {
            throw invalid("symbolic-link target must be a non-empty relative path")
        }
        var depth = max(0, path.split(separator: "/").count - 1)
        for component in target.split(separator: "/", omittingEmptySubsequences: false) {
            guard !component.isEmpty else {
                throw invalid("symbolic-link target contains an empty component")
            }
            if component == "." {
                continue
            }
            if component == ".." {
                guard depth > 0 else {
                    throw invalid("symbolic-link target escapes the archive root")
                }
                depth -= 1
            } else {
                depth += 1
            }
        }
    }

    private static func adding(_ left: UInt64, _ right: UInt64) throws -> UInt64 {
        let (result, overflow) = left.addingReportingOverflow(right)
        guard !overflow else {
            throw invalid("tar expanded size overflowed")
        }
        return result
    }

    private static func invalid(_ message: String) -> DevContainerError {
        DevContainerError(.invalidRequest, message: message)
    }
}
