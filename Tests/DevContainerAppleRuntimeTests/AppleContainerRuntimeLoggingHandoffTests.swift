//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

import ContainerResource
@testable import DevContainerAppleRuntime
import Foundation
import Testing

struct AppleContainerRuntimeLoggingHandoffTests {
    @Test
    func `runtime record preserves stream bytes and nanosecond timestamp`() throws {
        let date = Date(timeIntervalSince1970: 1_786_000_000.123_456_7)
        let value = try AppleContainerRuntime.portableLogRecord(
            ContainerLogRecord(
                timestamp: date,
                stream: .stderr,
                data: Data([0x00, 0xFF, 0x0A])
            )
        )

        #expect(value.secondsSinceUnixEpoch == 1_786_000_000)
        #expect(value.nanoseconds == 123_456_717)
        #expect(value.stream == .stderr)
        #expect(value.data == Data([0x00, 0xFF, 0x0A]))
    }

    @Test
    func `runtime record normalizes a negative fractional timestamp`() throws {
        let value = try AppleContainerRuntime.portableLogRecord(
            ContainerLogRecord(
                timestamp: Date(timeIntervalSince1970: -0.25),
                stream: .stdout,
                data: Data()
            )
        )

        #expect(value.secondsSinceUnixEpoch == -1)
        #expect(value.nanoseconds == 750_000_000)
    }
}
