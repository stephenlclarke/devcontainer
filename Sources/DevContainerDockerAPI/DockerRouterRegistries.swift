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
import DevContainerRuntimeSPI
import Foundation

enum ContainerHealthDecision: Sendable {
    case check
    case cached(DockerContainerHealth)
}

struct ContainerHealthObservation: Sendable {
    let exitCode: Int32
    let started: Date
    let ended: Date
}

actor ContainerHealthRegistry {
    private struct Entry: Sendable {
        var startedAt: Date?
        var lastCheckedAt: Date?
        var status: String
        var failures: Int
        var logs: [DockerHealthLog]

        var value: DockerContainerHealth {
            DockerContainerHealth(
                status: status,
                failingStreak: failures,
                log: logs
            )
        }
    }

    private var entries: [String: Entry] = [:]

    func decision(
        id: String,
        startedAt: Date?,
        healthcheck: ContainerHealthcheck,
        now: Date
    ) -> ContainerHealthDecision {
        var entry = entries[id]
        if entry?.startedAt != startedAt {
            entry = Entry(
                startedAt: startedAt,
                status: "starting",
                failures: 0,
                logs: []
            )
        }
        guard var current = entry else {
            return .check
        }
        let interval = max(
            0.1,
            Double(healthcheck.intervalNanoseconds) / 1_000_000_000
        )
        if let lastCheckedAt = current.lastCheckedAt,
           now.timeIntervalSince(lastCheckedAt) < interval
        {
            entries[id] = current
            return .cached(current.value)
        }
        // Reserve this check interval so concurrent inspect calls do not
        // launch duplicate health processes in the same container.
        current.lastCheckedAt = now
        entries[id] = current
        return .check
    }

    func record(
        id: String,
        startedAt: Date?,
        healthcheck: ContainerHealthcheck,
        observation: ContainerHealthObservation
    ) -> DockerContainerHealth {
        var entry = entries[id]
        if entry?.startedAt != startedAt {
            entry = Entry(
                startedAt: startedAt,
                status: "starting",
                failures: 0,
                logs: []
            )
        }
        var current = entry ?? Entry(
            startedAt: startedAt,
            status: "starting",
            failures: 0,
            logs: []
        )
        let withinStartPeriod = startedAt.map {
            observation.started.timeIntervalSince($0) * 1_000_000_000
                < Double(healthcheck.startPeriodNanoseconds)
        } ?? false
        if observation.exitCode == 0 {
            current.status = "healthy"
            current.failures = 0
        } else if withinStartPeriod {
            current.status = "starting"
        } else {
            current.failures += 1
            current.status = current.failures >= max(1, healthcheck.retries)
                ? "unhealthy"
                : "starting"
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        current.logs.append(
            DockerHealthLog(
                start: formatter.string(from: observation.started),
                end: formatter.string(from: observation.ended),
                exitCode: observation.exitCode,
                output: ""
            )
        )
        current.logs = Array(current.logs.suffix(5))
        current.lastCheckedAt = observation.ended
        entries[id] = current
        return current.value
    }

    func reset(id: String) {
        entries[id] = nil
    }

    func remove(id: String) {
        entries[id] = nil
    }
}

actor ExecSessionRegistry {
    private struct Entry {
        let registration: UUID
        let session: any RuntimeProcessSession
    }

    private var entries: [ExecID: Entry] = [:]

    func register(
        _ session: any RuntimeProcessSession,
        id: ExecID
    ) -> UUID {
        let registration = UUID()
        entries[id] = Entry(registration: registration, session: session)
        return registration
    }

    func session(id: ExecID) -> (any RuntimeProcessSession)? {
        entries[id]?.session
    }

    func remove(id: ExecID, registration: UUID) {
        guard entries[id]?.registration == registration else {
            return
        }
        entries[id] = nil
    }
}
