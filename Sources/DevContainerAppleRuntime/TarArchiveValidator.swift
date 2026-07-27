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

public enum TarArchiveValidator {
    public static let maximumEntries = 100_000
    public static let maximumExpandedSize: UInt64 = 2_147_483_648

    @discardableResult
    public static func validate(_ archive: Data) throws -> Int {
        guard archive.count % 512 == 0 else {
            throw invalid("tar archive is not aligned to 512-byte records")
        }

        var offset = 0
        var entries = 0
        var expandedSize: UInt64 = 0
        var pendingPath: String?
        var pendingLinkPath: String?
        var pax: [String: String] = [:]

        while offset + 512 <= archive.count {
            let header = archive.subdata(in: offset ..< offset + 512)
            if header.allSatisfy({ $0 == 0 }) {
                return entries
            }
            try validateChecksum(header)

            let size = try number(header, range: 124 ..< 136, field: "size")
            let type = header[156]
            let headerName = joinedName(header)
            let bodyStart = offset + 512
            guard
                size <= UInt64(Int.max),
                bodyStart <= archive.count,
                Int(size) <= archive.count - bodyStart
            else {
                throw invalid("tar entry body exceeds the archive")
            }
            let bodyEnd = bodyStart + Int(size)
            let body = archive.subdata(in: bodyStart ..< bodyEnd)

            switch type {
            case 0, 48, 49, 50, 53:
                let path = pax["path"] ?? pendingPath ?? headerName
                let linkPath = pax["linkpath"] ?? pendingLinkPath
                    ?? string(header, range: 157 ..< 257)
                try validatePath(path, field: "entry path")
                if type == 50 {
                    try validateSymbolicLink(linkPath, from: path)
                } else if type == 49 {
                    try validatePath(linkPath, field: "hard-link target")
                }
                entries += 1
                expandedSize = try adding(expandedSize, size)
                guard entries <= maximumEntries else {
                    throw invalid("tar archive exceeds \(maximumEntries) entries")
                }
                guard expandedSize <= maximumExpandedSize else {
                    throw invalid("tar archive expands beyond \(maximumExpandedSize) bytes")
                }
                pendingPath = nil
                pendingLinkPath = nil
                pax = [:]
            case 103:
                _ = try parsePAX(body)
            case 120:
                pax = try parsePAX(body)
            case 76:
                pendingPath = nulTerminated(body)
            case 75:
                pendingLinkPath = nulTerminated(body)
            case 51, 52, 54:
                throw invalid("tar device and FIFO entries are not allowed")
            default:
                throw invalid("unsupported tar entry type \(type)")
            }

            let paddedSize = (Int(size) + 511) & ~511
            guard paddedSize <= archive.count - bodyStart else {
                throw invalid("tar entry padding exceeds the archive")
            }
            offset = bodyStart + paddedSize
        }

        throw invalid("tar archive has no terminating zero record")
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

    private static func joinedName(_ header: Data) -> String {
        let name = string(header, range: 0 ..< 100)
        let prefix = string(header, range: 345 ..< 500)
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
        let value = String(decoding: bytes, as: UTF8.self)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\0 "))
        if value.isEmpty {
            return 0
        }
        guard let result = UInt64(value, radix: 8) else {
            throw invalid("tar \(field) is not valid octal")
        }
        return result
    }

    private static func string(_ data: Data, range: Range<Int>) -> String {
        nulTerminated(data.subdata(in: range))
    }

    private static func nulTerminated(_ data: Data) -> String {
        let end = data.firstIndex(of: 0) ?? data.endIndex
        return String(decoding: data[..<end], as: UTF8.self)
            .trimmingCharacters(in: .newlines)
    }

    private static func parsePAX(_ body: Data) throws -> [String: String] {
        var result: [String: String] = [:]
        var offset = 0
        while offset < body.count {
            guard let space = body[offset...].firstIndex(of: 32) else {
                throw invalid("PAX record has no length delimiter")
            }
            let lengthText = String(decoding: body[offset ..< space], as: UTF8.self)
            guard let length = Int(lengthText), length > 0, length <= body.count - offset else {
                throw invalid("PAX record has an invalid length")
            }
            let recordEnd = offset + length
            guard body[recordEnd - 1] == 10 else {
                throw invalid("PAX record is not newline terminated")
            }
            let valueStart = space + 1
            let record = String(decoding: body[valueStart ..< recordEnd - 1], as: UTF8.self)
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
