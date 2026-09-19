// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import Foundation
import Testing

func healthReservation(
    _ registry: ContainerHealthRegistry,
    id: String,
    startedAt: Date?,
    healthcheck: ContainerHealthcheck,
    now: Date
) async throws -> UUID {
    let decision = await registry.decision(id: id, startedAt: startedAt, healthcheck: healthcheck, now: now)
    let token: UUID? = switch decision {
    case let .check(value): value
    case .cached: nil
    }
    return try #require(token)
}

func recordHealthObservation(
    _ registry: ContainerHealthRegistry,
    id: String,
    startedAt: Date?,
    healthcheck: ContainerHealthcheck,
    observation: ContainerHealthObservation
) async throws -> DockerContainerHealth {
    let token = try await healthReservation(
        registry,
        id: id,
        startedAt: startedAt,
        healthcheck: healthcheck,
        now: observation.started
    )
    return try #require(await registry.record(
        id: id,
        startedAt: startedAt,
        reservation: token,
        healthcheck: healthcheck,
        observation: observation
    ))
}
