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

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `mutation replay registry evicts its oldest completed entry`() async throws {
    let registry = DockerMutationReplayRegistry(maximumEntries: 1)
    let first = try await registry.task(key: "first", requestHash: "one") {
        .empty(status: 201)
    }.value
    #expect(first.status == 201)

    _ = try await registry.task(key: "second", requestHash: "two") {
        .empty(status: 202)
    }.value
    let replayedAfterEviction = try await registry.task(
        key: "first",
        requestHash: "replacement"
    ) {
        .empty(status: 204)
    }.value
    #expect(replayedAfterEviction.status == 204)
}

@Test
func `active exec registry routes resize to the original session`() async throws {
    let registry = ExecSessionRegistry()
    let identifier = ExecID(rawValue: "exec-registry")
    let session = InMemoryProcessSession(frames: [], exitCode: 0)
    let registration = await registry.register(session, id: identifier)

    let active = try #require(await registry.session(id: identifier))
    try await active.resize(width: 132, height: 43)
    let size = try #require(await session.terminalSize())
    #expect(size.width == 132)
    #expect(size.height == 43)

    await registry.remove(id: identifier, registration: UUID())
    #expect(await registry.session(id: identifier) != nil)
    await registry.remove(id: identifier, registration: registration)
    #expect(await registry.session(id: identifier) == nil)
}

@Test
func `health registry observes intervals retries start periods and resets`() async {
    let registry = ContainerHealthRegistry()
    let start = Date(timeIntervalSince1970: 1000)
    let check = ContainerHealthcheck(
        test: ["CMD", "false"],
        intervalNanoseconds: 1_000_000_000,
        retries: 2
    )
    #expect(await healthDecisionIsCheck(
        registry,
        startedAt: start,
        healthcheck: check,
        now: start
    ))
    let first = await registry.record(
        id: "fixture",
        startedAt: start,
        healthcheck: check,
        observation: ContainerHealthObservation(
            exitCode: 1,
            started: start,
            ended: start.addingTimeInterval(0.1)
        )
    )
    #expect(first.status == "starting")
    #expect(first.failingStreak == 1)
    let cached = await cachedHealth(
        registry,
        startedAt: start,
        healthcheck: check,
        now: start.addingTimeInterval(0.2)
    )
    #expect(cached == first)
    #expect(await healthDecisionIsCheck(
        registry,
        startedAt: start,
        healthcheck: check,
        now: start.addingTimeInterval(1.2)
    ))
    let second = await registry.record(
        id: "fixture",
        startedAt: start,
        healthcheck: check,
        observation: ContainerHealthObservation(
            exitCode: 1,
            started: start.addingTimeInterval(1.2),
            ended: start.addingTimeInterval(1.3)
        )
    )
    #expect(second.status == "unhealthy")
    #expect(second.failingStreak == 2)
}

@Test
func `health registry recovers honors start periods and resets`() async {
    let registry = ContainerHealthRegistry()
    let start = Date(timeIntervalSince1970: 1000)
    let check = ContainerHealthcheck(test: ["CMD", "false"], retries: 2)
    _ = await registry.record(
        id: "fixture",
        startedAt: start,
        healthcheck: check,
        observation: ContainerHealthObservation(
            exitCode: 1,
            started: start,
            ended: start.addingTimeInterval(0.1)
        )
    )
    let recovered = await registry.record(
        id: "fixture",
        startedAt: start,
        healthcheck: check,
        observation: ContainerHealthObservation(
            exitCode: 0,
            started: start.addingTimeInterval(2.4),
            ended: start.addingTimeInterval(2.5)
        )
    )
    #expect(recovered.status == "healthy")
    #expect(recovered.failingStreak == 0)

    let grace = ContainerHealthcheck(
        test: ["CMD", "false"],
        retries: 1,
        startPeriodNanoseconds: 5_000_000_000
    )
    let warming = await registry.record(
        id: "grace",
        startedAt: start,
        healthcheck: grace,
        observation: ContainerHealthObservation(
            exitCode: 1,
            started: start.addingTimeInterval(1),
            ended: start.addingTimeInterval(1.1)
        )
    )
    #expect(warming.status == "starting")
    #expect(warming.failingStreak == 0)
    await registry.reset(id: "fixture")
    #expect(await healthDecisionIsCheck(
        registry,
        startedAt: start,
        healthcheck: check,
        now: start.addingTimeInterval(3)
    ))
    await registry.remove(id: "fixture")
}

private func healthDecisionIsCheck(
    _ registry: ContainerHealthRegistry,
    startedAt: Date?,
    healthcheck: ContainerHealthcheck,
    now: Date
) async -> Bool {
    if case .check = await registry.decision(
        id: "fixture",
        startedAt: startedAt,
        healthcheck: healthcheck,
        now: now
    ) {
        return true
    }
    return false
}

private func cachedHealth(
    _ registry: ContainerHealthRegistry,
    startedAt: Date?,
    healthcheck: ContainerHealthcheck,
    now: Date
) async -> DockerContainerHealth? {
    if case let .cached(health) = await registry.decision(
        id: "fixture",
        startedAt: startedAt,
        healthcheck: healthcheck,
        now: now
    ) {
        return health
    }
    return nil
}
