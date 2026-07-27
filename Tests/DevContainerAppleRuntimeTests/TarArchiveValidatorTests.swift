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

import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct TarArchiveValidatorTests {
    @Test
    func `accepts regular directory and safe symlink entries`() throws {
        let archive = tar([
            Entry(name: "workspace/", type: 53),
            Entry(name: "workspace/file.txt", body: Data("hello".utf8)),
            Entry(name: "workspace/link", type: 50, link: "file.txt")
        ])
        #expect(try TarArchiveValidator.validate(archive) == 3)
    }

    @Test
    func `rejects repeated path separators`() {
        #expect(throws: DevContainerError.self) {
            try TarArchiveValidator.validate(tar([
                Entry(name: "workspace//file.txt")
            ]))
        }
    }

    @Test(arguments: [
        Entry(name: "../escape"),
        Entry(name: "/absolute"),
        Entry(name: "workspace/link", type: 50, link: "../../escape"),
        Entry(name: "workspace/device", type: 51)
    ])
    func `rejects unsafe entries`(_ entry: Entry) {
        #expect(throws: DevContainerError.self) {
            try TarArchiveValidator.validate(tar([entry]))
        }
    }

    @Test
    func `rejects corrupt and truncated archives`() {
        var archive = tar([Entry(name: "file", body: Data("data".utf8))])
        archive[0] ^= 0x01
        #expect(throws: DevContainerError.self) {
            try TarArchiveValidator.validate(archive)
        }
        #expect(throws: DevContainerError.self) {
            try TarArchiveValidator.validate(Data(repeating: 1, count: 511))
        }
    }

    @Test
    func `accepts pax gnu long names and hard links`() throws {
        let archive = tar([
            Entry(
                name: "GlobalPax",
                body: paxRecord("comment=fixture"),
                type: 103
            ),
            Entry(
                name: "PaxHeader",
                body: paxRecord("path=workspace/pax.txt"),
                type: 120
            ),
            Entry(name: "ignored", body: Data("pax".utf8)),
            Entry(
                name: "LongName",
                body: Data("workspace/gnu.txt\0".utf8),
                type: 76
            ),
            Entry(name: "ignored", body: Data("gnu".utf8)),
            Entry(
                name: "LongLink",
                body: Data("gnu.txt\0".utf8),
                type: 75
            ),
            Entry(name: "workspace/link", type: 50),
            Entry(
                name: "workspace/hard",
                type: 49,
                link: "workspace/pax.txt"
            )
        ])
        #expect(try TarArchiveValidator.validate(archive) == 4)
    }

    @Test
    func `rejects malformed pax and unsupported types`() {
        var missingNewline = paxRecord("path=workspace/file")
        missingNewline[missingNewline.index(before: missingNewline.endIndex)] = 120
        let malformed = [
            Data("no-delimiter".utf8),
            Data("99 path=x\n".utf8),
            missingNewline,
            paxRecord("path-without-value")
        ]
        for body in malformed {
            #expect(throws: DevContainerError.self) {
                try TarArchiveValidator.validate(
                    tar([Entry(name: "PaxHeader", body: body, type: 120)])
                )
            }
        }
        #expect(throws: DevContainerError.self) {
            try TarArchiveValidator.validate(
                tar([Entry(name: "unsupported", type: 99)])
            )
        }
    }

    struct Entry: Sendable {
        let name: String
        let body: Data
        let type: UInt8
        let link: String

        init(
            name: String,
            body: Data = Data(),
            type: UInt8 = 48,
            link: String = ""
        ) {
            self.name = name
            self.body = body
            self.type = type
            self.link = link
        }
    }

    private func tar(_ entries: [Entry]) -> Data {
        var result = Data()
        for entry in entries {
            var header = Data(repeating: 0, count: 512)
            write(entry.name, into: &header, range: 0 ..< 100)
            write("0000755", into: &header, range: 100 ..< 108)
            write("0000000", into: &header, range: 108 ..< 116)
            write("0000000", into: &header, range: 116 ..< 124)
            write(
                String(format: "%011o", entry.body.count),
                into: &header,
                range: 124 ..< 136
            )
            write("00000000000", into: &header, range: 136 ..< 148)
            for index in 148 ..< 156 {
                header[index] = 32
            }
            header[156] = entry.type
            write(entry.link, into: &header, range: 157 ..< 257)
            write("ustar", into: &header, range: 257 ..< 263)
            let checksum = header.reduce(0) { $0 + UInt64($1) }
            write(String(format: "%06o", checksum), into: &header, range: 148 ..< 154)
            header[154] = 0
            header[155] = 32
            result.append(header)
            result.append(entry.body)
            let padding = (512 - entry.body.count % 512) % 512
            result.append(Data(repeating: 0, count: padding))
        }
        result.append(Data(repeating: 0, count: 1024))
        return result
    }

    private func write(_ value: String, into data: inout Data, range: Range<Int>) {
        let bytes = Array(value.utf8.prefix(range.count))
        data.replaceSubrange(range.lowerBound ..< range.lowerBound + bytes.count, with: bytes)
    }

    private func paxRecord(_ assignment: String) -> Data {
        var length = assignment.utf8.count + 3
        while true {
            let value = "\(length) \(assignment)\n"
            let actual = value.utf8.count
            if actual == length {
                return Data(value.utf8)
            }
            length = actual
        }
    }
}
