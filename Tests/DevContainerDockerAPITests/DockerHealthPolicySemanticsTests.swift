// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import Foundation
import Testing

struct DockerHealthPolicySemanticsTests {
    private let start = Date(timeIntervalSince1970: 1000)

    private func record(
        _ registry: ContainerHealthRegistry,
        policy: ContainerHealthcheck,
        exit: Int32,
        elapsed: Double
    ) async throws -> DockerContainerHealth {
        let now = start.addingTimeInterval(elapsed)
        return try await recordHealthObservation(
            registry,
            id: "app",
            startedAt: start,
            healthcheck: policy,
            observation: .init(exitCode: exit, started: now, ended: now)
        )
    }

    private func cached(
        _ registry: ContainerHealthRegistry,
        policy: ContainerHealthcheck,
        elapsed: Double,
        startedAt: Date?
    ) async -> Bool {
        let decision = await registry.decision(
            id: "app",
            startedAt: startedAt,
            healthcheck: policy,
            now: start.addingTimeInterval(elapsed)
        )
        if case .cached = decision {
            return true
        }
        return false
    }

    @Test
    func `short intervals are not clamped and concurrent probes stay serialized`() async throws {
        let registry = ContainerHealthRegistry()
        let policy = ContainerHealthcheck(test: ["CMD", "true"], intervalNanoseconds: 10_000_000)
        _ = try await record(registry, policy: policy, exit: 0, elapsed: 0)
        #expect(await cached(registry, policy: policy, elapsed: 0.005, startedAt: start))
        let token = try await healthReservation(
            registry,
            id: "app",
            startedAt: start,
            healthcheck: policy,
            now: start.addingTimeInterval(0.011)
        )
        #expect(await cached(registry, policy: policy, elapsed: 1, startedAt: start))
        let end = start.addingTimeInterval(1)
        #expect(await registry.record(
            id: "app",
            startedAt: start,
            reservation: token,
            healthcheck: policy,
            observation: .init(exitCode: 0, started: end, ended: end)
        ) != nil)
        #expect(await !cached(registry, policy: policy, elapsed: 1.011, startedAt: start))
        await registry.reset(id: "app")
        #expect(await !cached(registry, policy: policy, elapsed: 2, startedAt: nil))
        #expect(await cached(registry, policy: policy, elapsed: 3, startedAt: nil))
    }

    @Test(arguments: [Int64(0), 60_000_000_000])
    func `success ends startup grace and failures preserve health until threshold`(grace: Int64) async throws {
        let registry = ContainerHealthRegistry()
        let policy = ContainerHealthcheck(
            test: ["CMD", "true"],
            intervalNanoseconds: 1_000_000_000,
            retries: 2,
            startPeriodNanoseconds: grace
        )
        #expect(try await record(registry, policy: policy, exit: 0, elapsed: 0).status == "healthy")
        let first = try await record(registry, policy: policy, exit: 1, elapsed: 1)
        #expect(first.status == "healthy")
        #expect(first.failingStreak == 1)
        let second = try await record(registry, policy: policy, exit: 1, elapsed: 2)
        #expect(second.status == "unhealthy")
        #expect(second.failingStreak == 2)
        #expect(try await record(registry, policy: policy, exit: 1, elapsed: 3).status == "unhealthy")
        #expect(try await record(registry, policy: policy, exit: 0, elapsed: 4).status == "healthy")
    }

    @Test
    func `startup grace uses default cadence then switches to configured interval`() async throws {
        let registry = ContainerHealthRegistry()
        let policy = ContainerHealthcheck(
            test: ["CMD", "true"],
            intervalNanoseconds: 1_000_000_000,
            retries: 2,
            startPeriodNanoseconds: 10_000_000_000
        )
        let initial = try await record(registry, policy: policy, exit: 1, elapsed: 0)
        #expect(initial.status == "starting")
        #expect(initial.failingStreak == 0)
        #expect(await cached(registry, policy: policy, elapsed: 1.1, startedAt: start))
        _ = try await record(registry, policy: policy, exit: 0, elapsed: 5.1)
        _ = try await record(registry, policy: policy, exit: 1, elapsed: 6.2)
        #expect(await !cached(registry, policy: policy, elapsed: 11, startedAt: start))
    }

    @Test(arguments: ["reset", "restart", "remove"], [false, true])
    func `late results cannot resurrect state or release a newer probe`(
        operation: String,
        missingDate: Bool
    ) async throws {
        let registry = ContainerHealthRegistry()
        let policy = ContainerHealthcheck(test: ["CMD", "true"])
        let oldStart: Date? = missingDate ? nil : start
        let old = try await healthReservation(
            registry,
            id: "app",
            startedAt: oldStart,
            healthcheck: policy,
            now: start
        )
        if operation == "remove" {
            await registry.remove(id: "app")
        }
        if operation == "reset" || (operation == "restart" && missingDate) {
            await registry.reset(id: "app")
        }
        if operation != "restart" {
            #expect(await finish(registry, token: old, startedAt: oldStart) == nil)
        }
        let newStart = missingDate ? nil : start.addingTimeInterval(1)
        let new = try await healthReservation(
            registry,
            id: "app",
            startedAt: newStart,
            healthcheck: policy,
            now: start.addingTimeInterval(1)
        )
        #expect(old != new)
        #expect(await finish(registry, token: old, startedAt: oldStart) == nil)
        #expect(await cached(registry, policy: policy, elapsed: 100, startedAt: newStart))
        #expect(await finish(registry, token: new, startedAt: newStart)?.status == "healthy")
        #expect(await finish(registry, token: new, startedAt: newStart) == nil)
    }

    private func finish(
        _ registry: ContainerHealthRegistry,
        token: UUID,
        startedAt: Date?
    ) async -> DockerContainerHealth? {
        await registry.record(
            id: "app",
            startedAt: startedAt,
            reservation: token,
            healthcheck: ContainerHealthcheck(test: ["CMD", "true"]),
            observation: .init(exitCode: 0, started: start, ended: start)
        )
    }
}
