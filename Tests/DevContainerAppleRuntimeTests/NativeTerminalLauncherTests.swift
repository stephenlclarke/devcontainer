// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationOCI
import ContainerResource
import CryptoKit
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct NativeTerminalLauncherTests {
    private var digest: String {
        "sha256:" + String(repeating: "a", count: 64)
    }

    private func descriptor() throws -> Descriptor {
        try JSONDecoder().decode(Descriptor.self, from: Data("""
        {"mediaType":"application/vnd.oci.image.index.v1+json","digest":"\(digest)","size":123}
        """.utf8))
    }

    private func imageConfig() throws -> ImageConfig {
        try JSONDecoder().decode(ImageConfig.self, from: Data("""
        {"Entrypoint":["/bin/sh","-c"],"Cmd":["echo image"],"User":"1000:1000",
        "WorkingDir":"/image","Env":["A=original","B=retained"],"StopSignal":"SIGTERM"}
        """.utf8))
    }

    private func configuration(
        _ spec: ContainerSpec, asset: NativeTerminalLauncherAsset? = nil
    ) throws -> ContainerConfiguration {
        try AppleContainerCreateProjection.configuration(
            spec: spec,
            identity: (ImageDescription(reference: "fixture:latest", descriptor: descriptor()), .current),
            imageConfig: imageConfig(), system: .init(), builtinNetwork: "default", terminalLauncher: asset
        )
    }

    @Test func `initial TTY size wraps resolved process and preserves its settings`() throws {
        let spec = ContainerSpec(
            name: "fixture", image: digest, command: ["argument with spaces", "second"],
            entrypoint: ["/opt/workload", "--mode"], environment: ["A": "override", "BINARY": "a=b"],
            workingDirectory: "/workspace", user: "2000:3000", terminal: true,
            terminalWidth: 113, terminalHeight: 37
        )
        let asset = testAsset()
        let config = try configuration(spec, asset: asset)

        #expect(config.initProcess.executable == NativeTerminalLauncher.guestPath)
        #expect(config.initProcess.arguments == [
            "37", "113", "--", "/opt/workload", "--mode", "argument with spaces", "second"
        ])
        #expect(config.initProcess.terminal)
        #expect(config.initProcess.workingDirectory == "/workspace")
        #expect(config.initProcess.user == .raw(userString: "2000:3000"))
        #expect(config.initProcess.environment.contains("A=override"))
        #expect(config.initProcess.environment.contains("BINARY=a=b"))
        #expect(config.initProcess.environment.contains("B=retained"))
        #expect(config.mounts.count == 1)
        #expect(config.mounts[0].source == asset.source.path)
        #expect(config.mounts[0].destination == NativeTerminalLauncher.mountDestination)
        #expect(config.mounts[0].isVirtiofs)
        #expect(config.mounts[0].options.readonly)
        #expect(config.labels[NativeTerminalLauncher.label]?.hasPrefix(asset.attestation + ":") == true)
        try NativeTerminalLauncher.verify(configuration: config, spec: spec, asset: asset)

        var changed = config
        changed.initProcess.arguments[3] = "/tampered-workload"
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: changed, spec: spec, asset: asset)
        }
    }

    @Test func `launcher attestation covers final enhanced process policy`() throws {
        var spec = ContainerSpec(
            name: "fixture", image: digest, command: ["/bin/true"], terminal: true,
            terminalWidth: 80, terminalHeight: 24
        )
        #if DEVCONTAINER_ENHANCED_RUNTIME
            spec.privileged = true
        #endif
        let asset = testAsset()
        let config = try configuration(spec, asset: asset)
        try NativeTerminalLauncher.verify(configuration: config, spec: spec, asset: asset)
        #if DEVCONTAINER_ENHANCED_RUNTIME
            #expect(config.initProcess.privileged)
        #endif
    }

    @Test func `wrapped process uses resolved entrypoint and rejects an absent command`() throws {
        let replacement = try AppleContainerCreateProjection.process(
            ContainerSpec(name: "fixture", image: digest, entrypoint: ["/replacement"]), image: imageConfig()
        )
        #expect(replacement.arguments.isEmpty)
        #expect(throws: DevContainerError.self) {
            try AppleContainerCreateProjection.process(ContainerSpec(name: "fixture", image: digest), image: nil)
        }
        let bare = try AppleContainerCreateProjection.process(
            ContainerSpec(name: "fixture", image: digest, command: ["/bin/true"]), image: nil
        )
        #expect(bare.user == .id(uid: 0, gid: 0))
        #expect(bare.workingDirectory == "/")
    }

    @Test func `initial TTY size needs both dimensions and a trusted launcher only when nonzero`() throws {
        let noInitialSize = try configuration(ContainerSpec(
            name: "fixture", image: digest, entrypoint: ["/bin/true"], terminal: true,
            terminalWidth: 0, terminalHeight: 0
        ))
        #expect(noInitialSize.initProcess.executable == "/bin/true")
        #expect(noInitialSize.mounts.isEmpty)
        #expect(noInitialSize.labels[NativeTerminalLauncher.label] == nil)

        let oneAxis = ContainerSpec(
            name: "fixture", image: digest, command: ["/bin/true"], terminal: true,
            terminalWidth: 113, terminalHeight: 0
        )
        #expect(throws: DevContainerError.self) {
            try configuration(oneAxis)
        }
        #expect(try NativeTerminalLauncher.requestedSize(spec: oneAxis)?.width == 113)
        #expect(try NativeTerminalLauncher.requestedSize(spec: oneAxis)?.height == 0)
        let partial = try configuration(oneAxis, asset: testAsset())
        #expect(partial.initProcess.arguments.prefix(3).elementsEqual(["0", "113", "--"]))
    }

    @Test func `runtime-owned launcher mount conflicts with caller mounts at its path and ancestors`() throws {
        let spec = ContainerSpec(name: "fixture", image: digest, terminal: true, terminalWidth: 80, terminalHeight: 24)
        let config = try configuration(spec, asset: testAsset())
        for destination in [NativeTerminalLauncher.mountDestination, "/run/devcontainer", "/run"] {
            let caller = Filesystem.virtiofs(source: "/caller/source", destination: destination, options: [])
            #expect(throws: DevContainerError.self) {
                try LiveAppleContainerCreateClient.requireDisjointMounts(
                    prepared: config.mounts, requested: [caller]
                )
            }
        }
    }

    @Test func `installed launcher resolver admits only the pinned canonical executable`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("terminal-launcher-test-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let prefix = root.appendingPathComponent("package", isDirectory: true)
        let executable = prefix.appendingPathComponent("bin/devcontainer-engine")
        let directory = prefix.appendingPathComponent(NativeTerminalLauncher.assetDirectory, isDirectory: true)
        let helper = directory.appendingPathComponent("devcontainer-terminal-linux-arm64")
        try FileManager.default.createDirectory(
            at: executable.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Data("engine".utf8).write(to: executable)
        let payload = Data("trusted static ELF fixture".utf8)
        try payload.write(to: helper)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: helper.path)
        let hash = SHA256.hash(data: payload).map { String(format: "%02x", $0) }.joined()

        let admitted = try NativeTerminalLauncher.resolve(
            executableURL: executable, architecture: "arm64", sha256ByArchitecture: ["arm64": hash]
        )
        #expect(admitted.source == helper)
        #expect(admitted.sha256 == hash)
        #expect(admitted.attestation.hasPrefix("arm64:\(hash):"))
        #expect(try NativeTerminalLauncher.resolveInstalled(
            architecture: "arm64", executableURL: executable, sha256ByArchitecture: ["arm64": hash]
        ) == admitted)
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.resolveInstalled(
                architecture: "arm64", executableURL: nil, sha256ByArchitecture: ["arm64": hash]
            )
        }
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.resolve(
                executableURL: executable,
                architecture: "arm64",
                sha256ByArchitecture: ["arm64": String(repeating: "0", count: 64)]
            )
        }
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.resolve(
                executableURL: executable, architecture: "arm64", sha256ByArchitecture: [:]
            )
        }
    }

    @Test func `stored launcher attestation rejects malformed and changed configuration`() throws {
        let spec = ContainerSpec(
            name: "fixture", image: digest, command: ["/bin/true"], terminal: true,
            terminalWidth: 80, terminalHeight: 24
        )
        let asset = testAsset()
        let config = try configuration(spec, asset: asset)
        #expect(try NativeTerminalLauncher.verify(configuration: config, asset: asset) == asset)

        var malformed = config
        malformed.labels[NativeTerminalLauncher.label] = "not-an-attestation"
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: malformed, asset: asset)
        }

        var changedIdentity = config
        changedIdentity.labels[NativeTerminalLauncher.label] = "amd64:" + String(repeating: "b", count: 64) + ":4:9:" +
            String(repeating: "c", count: 64)
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: changedIdentity, asset: asset)
        }

        var changedSize = config
        changedSize.initProcess.arguments[0] = "invalid"
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: changedSize, asset: asset)
        }
        changedSize = config
        changedSize.initProcess.arguments[0] = "0"
        changedSize.initProcess.arguments[1] = "0"
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: changedSize, asset: asset)
        }

        var changedProcess = config
        changedProcess.initProcess.arguments[3] = "/changed"
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: changedProcess, asset: asset)
        }
        var changedMount = config
        changedMount.mounts[0] = .virtiofs(
            source: asset.source.path, destination: NativeTerminalLauncher.mountDestination, options: []
        )
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: changedMount, asset: asset)
        }
    }

    @Test func `launcher verifier preserves the no launcher default and rejects missing identity`() throws {
        let defaultSpec = ContainerSpec(name: "fixture", image: digest, command: ["/bin/true"])
        let defaultConfig = try configuration(defaultSpec)
        let identities = NativeTerminalLauncher.expectedSHA256ByArchitecture()
        #expect(Set(identities.keys).isSubset(of: ["arm64", "amd64"]))
        #expect(identities.values.allSatisfy { $0.count == 64 })
        try NativeTerminalLauncher.verify(configuration: defaultConfig, spec: defaultSpec)
        try NativeTerminalLauncher.verify(configuration: defaultConfig, spec: defaultSpec, asset: testAsset())
        #expect(defaultConfig.labels[NativeTerminalLauncher.label] == nil)

        let sizedSpec = ContainerSpec(
            name: "fixture", image: digest, command: ["/bin/true"], terminal: true,
            terminalWidth: 80, terminalHeight: 24
        )
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: defaultConfig, spec: sizedSpec)
        }

        let projected = try configuration(sizedSpec, asset: testAsset())
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: projected)
        }
        #expect(throws: DevContainerError.self) {
            try NativeTerminalLauncher.verify(configuration: projected, spec: defaultSpec, asset: testAsset())
        }
        let nonTTY = ContainerSpec(
            name: "fixture", image: digest, command: ["/bin/true"], terminal: false,
            terminalWidth: 80, terminalHeight: 24
        )
        #expect(try NativeTerminalLauncher.requestedSize(spec: nonTTY) == nil)
    }

    @Test func `runtime revalidation skips default sizes and rejects a replaced generation`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let inventory = FakeContainerInventory(snapshots: [nativeSnapshot(
            id: "fixture", labels: [:], status: .stopped
        )])
        let runtime = try fixture.runtime(inventory: inventory)
        let originalDate = Date(timeIntervalSince1970: 1)
        let defaultSpec = ContainerSpec(name: "fixture", image: digest, command: ["/bin/true"])

        try await runtime.verifyInitialTerminalLauncher(
            id: "fixture", createdAt: originalDate, spec: defaultSpec
        )
        #expect(await inventory.getCallCount() == 0)

        let sizedSpec = ContainerSpec(
            name: "fixture", image: digest, command: ["/bin/true"], terminal: true,
            terminalWidth: 80, terminalHeight: 24
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.verifyInitialTerminalLauncher(
                id: "fixture", createdAt: originalDate.addingTimeInterval(1), spec: sizedSpec
            )
        }
        #expect(await inventory.getCallCount() == 1)
    }

    private func testAsset() -> NativeTerminalLauncherAsset {
        NativeTerminalLauncherAsset(
            source: URL(fileURLWithPath: "/package/libexec/devcontainer/terminal-launcher/"
                + "devcontainer-terminal-linux-arm64"),
            architecture: "arm64", sha256: String(repeating: "a", count: 64), device: 4, inode: 9
        )
    }
}
