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

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

private struct RuntimeTarEntry {
    let name: String
    let body: Data
    let type: UInt8
}

extension AppleContainerRuntimeTests {
    func assertContainerInventory(
        _ runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        let containers = try await runtime.listContainers(
            all: true,
            labels: ["fixture": "yes"],
            context: context
        )
        let container = try #require(containers.first)
        #expect(container.runtimeID == RuntimeID(rawValue: "fixture"))
        #expect(container.dockerID == DockerID(rawValue: "docker-fixture"))
        #expect(container.state == .running)
        #expect(container.startedAt != nil)
        #expect(container.spec.user == "501:20")
        #expect(container.spec.environment == ["A": "last", "EMPTY": ""])
        #expect(container.spec.mounts.map(\.type) == [.bind, .volume, .tmpfs])
        #expect(
            container.spec.ports == [
                PortBinding(containerPort: 8080, hostPort: 0, hostAddress: "127.0.0.1")
            ]
        )
        #expect(
            container.spec.networks == [
                NetworkAttachment(name: "bridge", aliases: ["workspace"])
            ]
        )
        #expect(
            container.spec.securityOptions == [
                "no-new-privileges=true",
                "systempaths=unconfined"
            ]
        )
        #expect(container.networkAddresses == ["bridge": "192.0.2.10/24"])
    }

    func assertImageInventory(
        _ runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        let image = try #require(try await runtime.listImages(context: context).first)
        #expect(image.id == "sha256:abc123")
        #expect(image.references == ["fixture:latest"])
        #expect(image.size == 12345)
        #expect(image.user == "vscode")
        #expect(try await runtime.inspectImage(reference: "fixture:latest", context: context) == image)
        #expect(
            try await runtime.inspectImage(
                reference: "docker.io/library/fixture:latest",
                context: context
            ) == image
        )
        #expect(
            try await runtime.inspectImage(
                reference: "fixture:latest@sha256:abc123",
                context: context
            ) == image
        )
    }

    func assertNetworkInventory(
        _ runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        let network = try #require(try await runtime.listNetworks(context: context).first)
        #expect(network.id == "network-id")
        #expect(network.spec.internalNetwork)
        #expect(try await runtime.inspectNetwork(id: "fixture-network", context: context) == network)
    }

    func assertVolumeInventory(
        _ runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        let volume = try await runtime.createVolume(
            spec: VolumeSpec(name: "fixture-volume", labels: ["fixture": "yes"]),
            context: context
        )
        #expect(volume.name == "fixture-volume")
        #expect(volume.mountpoint.hasSuffix("/volumes/fixture-volume/_data"))
        #expect(try await runtime.inspectVolume(name: volume.name, context: context) == volume)

        let nativeName = "buildx_buildkit_fixture_state"
        let native = try await runtime.inspectVolume(name: nativeName, context: context)
        #expect(native.name == nativeName)
        #expect(native.mountpoint == "/native/buildkit")
        #expect(
            try await runtime.createVolume(
                spec: VolumeSpec(name: nativeName),
                context: context
            ) == native
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.createVolume(
                spec: VolumeSpec(name: nativeName, driver: "remote"),
                context: context
            )
        }
        try await runtime.removeVolume(name: nativeName, force: true, context: context)
    }

    func supportedContainerSpec() -> ContainerSpec {
        ContainerSpec(
            name: "fixture",
            image: "fixture:latest",
            command: ["sleep", "infinity"],
            entrypoint: ["/bin/sh"],
            environment: ["B": "2", "A": "1"],
            labels: ["z": "last", "a": "first"],
            workingDirectory: "/workspace",
            user: "501:20",
            hostname: "fixture-host",
            mounts: [
                RuntimeMount(type: .bind, source: "/tmp/source", destination: "/workspace", readOnly: true),
                RuntimeMount(type: .volume, source: "cache", destination: "/cache"),
                RuntimeMount(
                    type: .volume,
                    source: "anonymous-cache",
                    destination: "/anonymous",
                    anonymous: true
                ),
                RuntimeMount(
                    type: .volume,
                    source: "buildx_buildkit_fixture_state",
                    destination: "/buildkit",
                    readOnly: true
                ),
                RuntimeMount(type: .tmpfs, source: "", destination: "/run")
            ],
            ports: [
                PortBinding(containerPort: 8080, hostAddress: "127.0.0.1"),
                PortBinding(containerPort: 53, protocolName: "udp"),
                PortBinding(
                    containerPort: 8443,
                    hostPort: 18443,
                    hostAddress: "127.0.0.1"
                )
            ],
            networks: [
                NetworkAttachment(name: "fixture-network", aliases: ["app", "api"])
            ],
            terminal: true,
            openStandardInput: true,
            privileged: true,
            initProcess: true,
            autoRemove: true,
            capabilitiesToAdd: ["SYS_PTRACE"],
            capabilitiesToDrop: ["NET_RAW"],
            securityOptions: ["no-new-privileges=true"]
        )
    }

    func assertLifecycleLog(_ log: String) {
        #expect(
            log.contains(
                "create --name fixture --env A=1 --env B=2 --label a=first --label z=last "
                    + "--workdir /workspace --user 501:20 --hostname fixture-host --tty --interactive "
                    + "--init --privileged --cap-add SYS_PTRACE --cap-drop NET_RAW "
                    + "--security-opt no-new-privileges=true --entrypoint /bin/sh"
            )
        )
        #expect(!log.contains("--cap-add ALL"))
        #expect(log.contains("--mount type=bind,source=/tmp/source,target=/workspace,readonly"))
        #expect(log.contains("--mount type=bind,source="))
        #expect(log.contains("/volumes/cache/_data,target=/cache"))
        #expect(!log.contains("anonymous-cache"))
        #expect(
            log.contains(
                "--mount type=volume,source=buildx_buildkit_fixture_state,target=/buildkit,readonly"
            )
        )
        #expect(log.contains("--tmpfs /run"))
        #expect(log.contains("--publish 127.0.0.1:18443:8443/tcp"))
        #expect(!log.contains("127.0.0.1:0:8080"))
        #expect(log.contains("--network fixture-network fixture:latest"))
        #expect(!log.contains("fixture-network,alias="))
        #expect(log.contains("cp fixture:/etc/hosts"))
        #expect(log.contains("fixture:/etc/hosts"))
        #expect(log.contains("stop --time 2 fixture"))
        #expect(log.contains("kill --signal SIGTERM fixture"))
        #expect(log.contains("delete --force fixture"))
    }

    func assertContainerLogsAndAttach(
        fixture: FakeAppleCLI,
        runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        let logs = try await runtime.containerLogs(
            id: "fixture",
            follow: true,
            standardOutput: true,
            standardError: true,
            context: context
        )
        var frames: [RuntimeIOFrame] = []
        for try await frame in logs {
            frames.append(frame)
        }
        #expect(
            frames.contains {
                $0.channel == .standardOutput && String(data: $0.data, encoding: .utf8) == "log-output\n"
            }
        )
        #expect(
            frames.contains {
                $0.channel == .standardError && String(data: $0.data, encoding: .utf8) == "log-error\n"
            }
        )
        try await runtime.startContainer(id: "fixture", context: context)
        try fixture.setState("stopped")
        let attached = try await runtime.attachContainer(
            id: "fixture", terminal: false, context: context
        )
        #expect(try await attached.wait() == 17)
        try fixture.setState("running")
    }

    func assertExecSession(
        _ runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        let exec = try await runtime.createExec(
            containerID: "fixture",
            spec: ExecSpec(
                command: ["printf", "exec-output"],
                environment: ["B": "2", "A": "1"],
                workingDirectory: "/workspace",
                user: "501:20",
                terminal: true,
                attachStandardInput: true
            ),
            context: context
        )
        let session = try await runtime.startExec(id: exec.id, context: context)
        var output = Data()
        for try await frame in session.frames where frame.channel == .standardOutput {
            output.append(frame.data)
        }
        #expect(try await session.wait() == 0)
        #expect(String(data: output, encoding: .utf8) == "exec-output\n")
        let completed = try await runtime.inspectExec(id: exec.id, context: context)
        #expect(!completed.running)
        #expect(completed.exitCode == 0)
        await #expect(throws: DevContainerError.self) {
            try await session.resize(width: 80, height: 24)
        }
    }

    func assertImageStreams(
        fixture: FakeAppleCLI,
        runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        var pulled = Data()
        for try await chunk in try await runtime.pullImage(
            reference: "fixture:latest", context: context
        ) {
            pulled.append(chunk)
        }
        #expect(String(data: pulled, encoding: .utf8) == "pull-progress\n")
        #expect(
            try fixture.log().contains(
                "image pull --progress plain --platform linux/arm64 fixture:latest"
            )
        )
        var loaded = Data()
        for try await chunk in try await runtime.loadImage(
            archive: Data("image-archive".utf8),
            context: context
        ) {
            loaded.append(chunk)
        }
        #expect(String(data: loaded, encoding: .utf8) == "load-progress\n")
        let request = ImageBuildRequest(
            context: minimalTar(),
            dockerfile: "file.txt",
            tags: ["fixture:built"],
            buildArguments: ["MODE": "debug"],
            target: "development",
            labels: ["fixture": "yes"]
        )
        var built = Data()
        for try await chunk in try await runtime.buildImage(request: request, context: context) {
            built.append(chunk)
        }
        #expect(String(data: built, encoding: .utf8) == "build-progress\n")
        let featureRequest = ImageBuildRequest(
            context: featureContentTar(),
            dockerfile: "Dockerfile.buildContent",
            tags: ["fixture:feature-content"],
            buildArguments: [:],
            target: nil,
            labels: [:]
        )
        for try await _ in try await runtime.buildImage(
            request: featureRequest,
            context: context
        ) {}
        #expect(try fixture.log().contains("prepared-feature-context"))
    }

    func exerciseNetworkAndVolumeMutations(
        _ runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        _ = try await runtime.createNetwork(
            spec: NetworkSpec(name: "fixture-network", labels: ["fixture": "yes"], internalNetwork: true),
            context: context
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.connectNetwork(
                id: "fixture-network",
                containerID: "fixture",
                aliases: ["app", "api"],
                context: context
            )
        }
        await #expect(throws: DevContainerError.self) {
            try await runtime.disconnectNetwork(
                id: "fixture-network",
                containerID: "fixture",
                force: true,
                context: context
            )
        }
        try await runtime.removeNetwork(id: "fixture-network", context: context)
        _ = try await runtime.createVolume(
            spec: VolumeSpec(name: "fixture-volume", labels: ["fixture": "yes"]),
            context: context
        )
        try await runtime.removeVolume(name: "fixture-volume", force: true, context: context)
    }

    func exerciseImageAndArchiveMutations(
        fixture: FakeAppleCLI,
        runtime: AppleContainerRuntime,
        context: RuntimeRequestContext
    ) async throws {
        try await runtime.tagImage(source: "fixture:latest", target: "fixture:tagged", context: context)
        try await runtime.removeImage(reference: "fixture:tagged", force: true, context: context)
        let archive = try await runtime.copyArchiveFromContainer(
            id: "fixture",
            path: "/workspace/file.txt",
            context: context
        )
        #expect(!archive.data.isEmpty)
        #expect(archive.stat.mode & (1 << 31) == 0)
        #expect(archive.stat.mode & 0o777 == 0o644)
        try await runtime.copyArchiveToContainer(
            id: "fixture",
            path: "/workspace",
            archive: minimalTar(),
            context: context
        )
        try fixture.setState("created")
        try await runtime.copyArchiveToContainer(
            id: "fixture",
            path: "/workspace",
            archive: minimalTar(),
            context: context
        )
    }

    func assertMutationLog(_ log: String) {
        #expect(log.contains("network create --label fixture=yes --internal fixture-network"))
        #expect(!log.contains("network connect"))
        #expect(!log.contains("network disconnect"))
        #expect(!log.contains("volume create"))
        #expect(log.contains("image tag fixture:latest fixture:tagged"))
        #expect(log.contains("image delete --force fixture:tagged"))
        #expect(log.contains("cp fixture:/workspace/file.txt"))
        #expect(log.contains("cp "))
        #expect(log.contains("fixture:/workspace"))
        #expect(log.contains("start fixture"))
        #expect(log.contains("stop --time 10 fixture"))
    }

    func waitForExec(
        _ runtime: AppleContainerRuntime,
        id: ExecID,
        context: RuntimeRequestContext
    ) async throws -> ExecSnapshot {
        for _ in 0 ..< 100 {
            let snapshot = try await runtime.inspectExec(id: id, context: context)
            if !snapshot.running {
                return snapshot
            }
            try await Task.sleep(for: .milliseconds(5))
        }
        Issue.record("exec did not finish")
        return try await runtime.inspectExec(id: id, context: context)
    }

    func minimalTar() -> Data {
        tar([
            RuntimeTarEntry(
                name: "file.txt",
                body: Data(),
                type: UInt8(ascii: "0")
            )
        ])
    }

    func featureContentTar() -> Data {
        tar([
            RuntimeTarEntry(
                name: "Dockerfile.buildContent",
                body: Data(
                    "\n\tFROM scratch\n\tCOPY . /tmp/build-features/\n".utf8
                ),
                type: UInt8(ascii: "0")
            ),
            RuntimeTarEntry(
                name: "common-utils_0/",
                body: Data(),
                type: UInt8(ascii: "5")
            ),
            RuntimeTarEntry(
                name: "common-utils_0/devcontainer-features-install.sh",
                body: Data("#!/bin/sh\n".utf8),
                type: UInt8(ascii: "0")
            )
        ])
    }

    private func tar(_ entries: [RuntimeTarEntry]) -> Data {
        var archive = Data()
        for entry in entries {
            var header = Data(repeating: 0, count: 512)
            write(entry.name, into: &header, range: 0 ..< 100)
            write(
                entry.type == UInt8(ascii: "5") ? "0000755" : "0000644",
                into: &header,
                range: 100 ..< 108
            )
            write("0000000", into: &header, range: 108 ..< 116)
            write("0000000", into: &header, range: 116 ..< 124)
            write(
                String(format: "%011o", entry.body.count),
                into: &header,
                range: 124 ..< 136
            )
            write("00000000000", into: &header, range: 136 ..< 148)
            for index in 148 ..< 156 {
                header[index] = 32
            }
            header[156] = entry.type
            write("ustar", into: &header, range: 257 ..< 263)
            let checksum = header.reduce(0) { $0 + UInt64($1) }
            write(
                String(format: "%06o", checksum),
                into: &header,
                range: 148 ..< 154
            )
            header[154] = 0
            header[155] = 32
            archive.append(header)
            archive.append(entry.body)
            let padding = (512 - entry.body.count % 512) % 512
            archive.append(Data(repeating: 0, count: padding))
        }
        archive.append(Data(repeating: 0, count: 1024))
        return archive
    }

    func write(_ value: String, into data: inout Data, range: Range<Int>) {
        let bytes = Array(value.utf8.prefix(range.count))
        data.replaceSubrange(range.lowerBound ..< range.lowerBound + bytes.count, with: bytes)
    }
}
