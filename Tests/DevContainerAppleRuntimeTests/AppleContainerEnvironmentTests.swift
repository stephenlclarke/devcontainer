// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationOCI
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerEnvironmentTests {
    @Test func removesInheritedValuesFromNativeProcessAndAdoption() throws {
        let image = try JSONDecoder().decode(ImageConfig.self, from: Data(
            #"{"Cmd":["/bin/true"],"Env":["REMOVE=secret","KEEP=yes","EMPTY=old"]}"#.utf8
        ))
        let spec = ContainerSpec(name: "fixture", image: "fixture:latest", environment: ["EMPTY": ""],
                                 removedEnvironmentKeys: ["REMOVE"])
        let process = try AppleContainerCreateProjection.process(spec, image: image)
        #expect(!process.environment.contains { $0.hasPrefix("REMOVE=") })
        #expect(process.environment.contains("KEEP=yes"))
        #expect(process.environment.contains("EMPTY="))
        var observed = spec
        observed.environment = ["REMOVE": "stale", "KEEP": "yes", "EMPTY": "old"]
        let adopted = AppleContainerRuntime.effectiveContainerSpec(requested: spec, observed: observed)
        #expect(adopted.environment == ["KEEP": "yes", "EMPTY": ""])
        #expect(adopted.removedEnvironmentKeys == ["REMOVE"])
    }

    @Test func legacyCLIRejectsRemovalBeforeCreate() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: .init(
                name: "fixture", image: "fixture:latest", command: ["/bin/true"], removedEnvironmentKeys: ["REMOVE"]
            ), context: RuntimeRequestContext())
        }
        #expect(try !fixture.log().contains("create --name"))
    }
}
