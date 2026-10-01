// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Foundation
#if canImport(Darwin)
    import Darwin
#else
    import Glibc
#endif

/// Explicit resolver settings; empty nameservers retain the runtime's defaults.
public struct RuntimeDNSConfiguration: Codable, Equatable, Sendable {
    public var nameservers: [String]
    public var searchDomains: [String]
    public var options: [String]

    public init(nameservers: [String] = [], searchDomains: [String] = [], options: [String] = []) {
        self.nameservers = nameservers
        self.searchDomains = searchDomains
        self.options = options
    }

    public func validate() throws {
        for address in nameservers {
            var ipv4 = in_addr()
            var ipv6 = in6_addr()
            guard !address.contains("\0"),
                  inet_pton(AF_INET, address, &ipv4) == 1 || inet_pton(AF_INET6, address, &ipv6) == 1
            else {
                throw DevContainerError(.invalidRequest, message: "Invalid DNS nameserver: \(address)")
            }
        }
        for value in searchDomains + options {
            guard !value.isEmpty,
                  value.rangeOfCharacter(from: .whitespacesAndNewlines.union(.controlCharacters)) == nil
            else {
                throw DevContainerError(
                    .invalidRequest, message: "DNS search domains and options must be single tokens"
                )
            }
        }
    }
}
