//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

@Suite(.serialized)
struct AppleContainerRuntimeLifecycleRaceTests {
    @Test
    func `restart waits for an in-flight start then executes`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setState("created")
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        async let start: Void = runtime.startContainer(
            id: "fixture",
            context: context
        )
        let deadline = ContinuousClock.now + .seconds(1)
        while !((try? fixture.log()) ?? "").contains("start fixture"),
              ContinuousClock.now < deadline
        {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(try fixture.log().contains("start fixture"))
        try await runtime.restartContainer(
            id: "fixture",
            timeout: nil,
            context: context
        )
        try await start

        let lifecycleCommands = try fixture.log().split(separator: "\n")
            .filter { $0 == "start fixture" || $0 == "restart fixture" }
        #expect(lifecycleCommands == ["start fixture", "restart fixture"])
    }

    @Test
    func `automatic removal does not delete a restarted generation`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()
        let container = try await runtime.createContainer(
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                autoRemove: true
            ),
            context: context
        )
        try fixture.setState("stopped")
        await runtime.handleContainerExit(
            AppleContainerRuntime.ContainerExit(
                code: 23,
                finishedAt: Date()
            ),
            id: container.runtimeID.rawValue
        )

        try fixture.setState("running")
        try await Task.sleep(for: .milliseconds(1100))

        #expect(try !fixture.log().contains("delete --force fixture"))
    }
}
