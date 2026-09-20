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

import Darwin
import Foundation

/// Owns the two ends of process standard input. Apple process descriptor
/// transfer can retain copied descriptors after the host closes its handle.
/// A socket half-close signals EOF independently of those retained copies.
struct AppleProcessInputChannel: @unchecked Sendable {
    let processEnd: FileHandle
    let hostEnd: FileHandle
    let requiresHalfClose: Bool

    init(pipe: Pipe) {
        processEnd = pipe.fileHandleForReading
        hostEnd = pipe.fileHandleForWriting
        requiresHalfClose = false
    }

    static func socketPair() throws -> AppleProcessInputChannel {
        var descriptors: [Int32] = [-1, -1]
        guard Darwin.socketpair(
            AF_UNIX,
            SOCK_STREAM,
            0,
            &descriptors
        ) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        do {
            for descriptor in descriptors {
                guard fcntl(descriptor, F_SETFD, FD_CLOEXEC) == 0 else {
                    throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
                }
            }
        } catch {
            Darwin.close(descriptors[0])
            Darwin.close(descriptors[1])
            throw error
        }
        return AppleProcessInputChannel(
            processEnd: FileHandle(
                fileDescriptor: descriptors[0],
                closeOnDealloc: true
            ),
            hostEnd: FileHandle(
                fileDescriptor: descriptors[1],
                closeOnDealloc: true
            ),
            requiresHalfClose: true
        )
    }

    private init(
        processEnd: FileHandle,
        hostEnd: FileHandle,
        requiresHalfClose: Bool
    ) {
        self.processEnd = processEnd
        self.hostEnd = hostEnd
        self.requiresHalfClose = requiresHalfClose
    }
}
