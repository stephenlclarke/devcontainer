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

@Suite(.serialized)
struct AppleContainerRuntimeTests {
    @Test
    func `managed hosts replacement preserves unmanaged entries`() {
        let original = """
        127.0.0.1 localhost
        # BEGIN devcontainer managed network hosts
        192.0.2.1 stale
        # END devcontainer managed network hosts
        203.0.113.1 custom

        """
        let managed = """
        # BEGIN devcontainer managed network hosts
        192.0.2.2 current
        # END devcontainer managed network hosts

        """
        #expect(
            AppleContainerRuntime.replacingManagedHosts(
                in: original,
                with: managed
            ) == """
            127.0.0.1 localhost

            203.0.113.1 custom
            # BEGIN devcontainer managed network hosts
            192.0.2.2 current
            # END devcontainer managed network hosts

            """
        )
        #expect(
            AppleContainerRuntime.requiresNativeVolume(
                name: "buildx_buildkit_fixture_state"
            )
        )
        #expect(!AppleContainerRuntime.requiresNativeVolume(name: "user-cache"))
    }

    @Test
    func `descriptor and inventory decode stock Apple records`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        let descriptor = try await runtime.descriptor(context: context)
        #expect(descriptor.provider == .stock)
        #expect(descriptor.providerVersion == "1.1.0")
        #expect(descriptor.providerCommit == "fixture-commit")
        #expect(descriptor.capabilities[.events] == .emulated)

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
        #expect(container.spec.mounts.map(\.type) == [.bind, .volume, .tmpfs])
        #expect(container.spec.ports == [
            PortBinding(
                containerPort: 8080,
                hostPort: 18080,
                hostAddress: "0.0.0.0"
            )
        ])
        #expect(container.spec.securityOptions == [
            "no-new-privileges=true",
            "systempaths=unconfined"
        ])
        #expect(container.networkAddresses == ["bridge": "192.0.2.10/24"])

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

        let network = try #require(try await runtime.listNetworks(context: context).first)
        #expect(network.id == "network-id")
        #expect(network.spec.internalNetwork)
        #expect(try await runtime.inspectNetwork(id: "fixture-network", context: context) == network)

        let volume = try await runtime.createVolume(
            spec: VolumeSpec(
                name: "fixture-volume",
                labels: ["fixture": "yes"]
            ),
            context: context
        )
        #expect(volume.name == "fixture-volume")
        #expect(volume.mountpoint.hasSuffix("/volumes/fixture-volume/_data"))
        #expect(try await runtime.inspectVolume(name: volume.name, context: context) == volume)

        let nativeName = "buildx_buildkit_fixture_state"
        let native = try await runtime.inspectVolume(
            name: nativeName,
            context: context
        )
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
        try await runtime.removeVolume(
            name: nativeName,
            force: true,
            context: context
        )
    }

    @Test
    func `stale metadata is discarded when native compose reuses a name`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore()
        await store.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: RuntimeID(rawValue: "fixture"),
                dockerID: DockerID(rawValue: "stale-docker-id"),
                spec: ContainerSpec(
                    name: "fixture",
                    image: "stale:latest",
                    labels: ["stale": "true"]
                ),
                createdAt: Date(timeIntervalSince1970: 0)
            )
        )
        let runtime = try fixture.runtime(metadataStore: store)
        let container = try #require(
            try await runtime.listContainers(
                all: true,
                labels: [:],
                context: RuntimeRequestContext()
            ).first
        )
        #expect(container.dockerID == DockerID(rawValue: "docker-fixture"))
        #expect(container.spec.labels["stale"] == nil)
        #expect(await store.containerMetadata(id: "fixture") == nil)
        #expect(
            AppleContainerRuntime.sameContainerIncarnation(
                metadataCreatedAt: container.createdAt,
                observedCreatedAt: container.createdAt.addingTimeInterval(
                    0.0005
                )
            )
        )
    }

    @Test
    func `stale in-memory request is discarded when native compose reuses a name`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()
        _ = try await runtime.createContainer(
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                labels: ["com.docker.compose.oneoff": "False"]
            ),
            context: context
        )

        try fixture.setMode("recreated")
        let container = try #require(
            try await runtime.listContainers(
                all: true,
                labels: [:],
                context: context
            ).first
        )

        #expect(container.spec.labels["com.docker.compose.oneoff"] == nil)
        #expect(container.spec.labels["com.apple.container.compose.oneoff"] == "false")
        #expect(container.createdAt > Date(timeIntervalSince1970: 1_800_000_000))
    }

    @Test
    func `metadata is persisted on create and start`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store)
        let context = RuntimeRequestContext()

        let created = try await runtime.createContainer(
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                labels: ["metadata": "persisted"]
            ),
            context: context
        )
        let metadata = try #require(
            await store.containerMetadata(id: created.runtimeID.rawValue)
        )
        #expect(metadata.spec.labels["metadata"] == "persisted")
        #expect(metadata.startedAt == nil)

        try await runtime.startContainer(
            id: created.runtimeID.rawValue,
            context: context
        )
        #expect(
            await store.containerMetadata(id: created.runtimeID.rawValue)?
                .startedAt != nil
        )
    }

    @Test
    func `metadata failure rolls back a newly created Apple container`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime(metadataStore: FailingMetadataStore())

        await #expect(throws: MetadataTestError.self) {
            _ = try await runtime.createContainer(
                spec: ContainerSpec(
                    name: "fixture",
                    image: "fixture:latest"
                ),
                context: RuntimeRequestContext()
            )
        }

        #expect(try fixture.log().contains("delete --force fixture"))
    }

    @Test
    func `container lifecycle encodes every supported Apple CLI option`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()
        let spec = ContainerSpec(
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
                PortBinding(containerPort: 8080, hostPort: 18080, hostAddress: "0.0.0.0"),
                PortBinding(containerPort: 53, protocolName: "udp")
            ],
            networks: [
                NetworkAttachment(
                    name: "fixture-network",
                    aliases: ["app", "api"]
                )
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

        _ = try await runtime.createContainer(spec: spec, context: context)
        try await runtime.startContainer(id: "fixture", context: context)
        try await runtime.stopContainer(id: "fixture", timeout: .milliseconds(1001), context: context)
        try await runtime.killContainer(id: "fixture", signal: "SIGTERM", context: context)
        try await runtime.removeContainer(id: "fixture", force: true, context: context)

        let log = try fixture.log()
        #expect(log.contains(
            "create --name fixture --env A=1 --env B=2 --label a=first --label z=last " +
                "--workdir /workspace --user 501:20 --hostname fixture-host --tty --interactive " +
                "--privileged --init --cap-add SYS_PTRACE --cap-drop NET_RAW " +
                "--security-opt no-new-privileges=true --entrypoint /bin/sh"
        ))
        #expect(log.contains("--mount type=bind,source=/tmp/source,target=/workspace,readonly"))
        #expect(log.contains("--mount type=bind,source="))
        #expect(log.contains("/volumes/cache/_data,target=/cache"))
        #expect(!log.contains("anonymous-cache"))
        #expect(log.contains(
            "--mount type=volume,source=buildx_buildkit_fixture_state,target=/buildkit,readonly"
        ))
        #expect(log.contains("--tmpfs /run"))
        #expect(!log.contains("--publish 0.0.0.0:18080:8080/tcp"))
        #expect(!log.contains("--publish 127.0.0.1:0:53/udp"))
        #expect(log.contains("--network fixture-network fixture:latest"))
        #expect(!log.contains("fixture-network,alias="))
        #expect(log.contains("cp fixture:/etc/hosts"))
        #expect(log.contains("fixture:/etc/hosts"))
        #expect(log.contains("stop --time 2 fixture"))
        #expect(log.contains("kill --signal SIGTERM fixture"))
        #expect(log.contains("delete --force fixture"))

        _ = try await runtime.createContainer(
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture",
                command: ["printf ok"],
                entrypoint: ["/bin/sh", "-c"]
            ),
            context: context
        )
        #expect(try fixture.log().contains(
            "create --name fixture --entrypoint /bin/sh fixture -c printf ok"
        ))
    }

    @Test
    func `exec logs attach pull and build stream process output`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        let logs = try await runtime.containerLogs(
            id: "fixture",
            follow: true,
            standardOutput: true,
            standardError: true,
            context: context
        )
        var logFrames: [RuntimeIOFrame] = []
        for try await frame in logs {
            logFrames.append(frame)
        }
        #expect(logFrames.contains {
            $0.channel == .standardOutput
                && String(data: $0.data, encoding: .utf8) == "log-output\n"
        })
        #expect(logFrames.contains {
            $0.channel == .standardError
                && String(data: $0.data, encoding: .utf8) == "log-error\n"
        })

        try await runtime.startContainer(id: "fixture", context: context)
        try fixture.setState("stopped")
        let attached = try await runtime.attachContainer(
            id: "fixture",
            terminal: false,
            context: context
        )
        #expect(try await attached.wait() == 17)
        try fixture.setState("running")

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
        var execOutput = Data()
        for try await frame in session.frames where frame.channel == .standardOutput {
            execOutput.append(frame.data)
        }
        #expect(try await session.wait() == 0)
        #expect(String(data: execOutput, encoding: .utf8) == "exec-output\n")
        let completed = try await waitForExec(runtime, id: exec.id, context: context)
        #expect(!completed.running)
        #expect(completed.exitCode == 0)
        await #expect(throws: DevContainerError.self) {
            try await session.resize(width: 80, height: 24)
        }

        var pulled = Data()
        for try await chunk in try await runtime.pullImage(reference: "fixture:latest", context: context) {
            pulled.append(chunk)
        }
        #expect(String(data: pulled, encoding: .utf8) == "pull-progress\n")
        #expect(try fixture.log().contains(
            "image pull --progress plain --platform linux/arm64 fixture:latest"
        ))

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
    }

    @Test
    func `network volume image and archive mutations delegate safely`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        _ = try await runtime.createNetwork(
            spec: NetworkSpec(
                name: "fixture-network",
                labels: ["fixture": "yes"],
                internalNetwork: true
            ),
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

        let log = try fixture.log()
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

    @Test
    func `not found malformed output failures and stopped waits are bounded`() async throws {
        let fixture = try FakeAppleCLI()
        let context = RuntimeRequestContext()
        let runtime = try fixture.runtime()

        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectContainer(id: "missing", context: context)
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectImage(reference: "missing", context: context)
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectNetwork(id: "missing", context: context)
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectVolume(name: "missing", context: context)
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.startExec(id: .random(), context: context)
        }

        _ = try await runtime.createContainer(
            spec: ContainerSpec(name: "fixture", image: "fixture:latest"),
            context: context
        )
        try fixture.setState("stopped")
        #expect(
            try await runtime.inspectContainer(id: "fixture", context: context).state == .created
        )
        try await runtime.startContainer(id: "fixture", context: context)
        #expect(
            try await runtime.inspectContainer(id: "fixture", context: context).state == .stopped
        )
        #expect(try await runtime.waitContainer(id: "fixture", context: context) == 17)

        try fixture.setMode("malformed")
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.listContainers(all: true, labels: [:], context: context)
        }
        try fixture.setMode("failure")
        await #expect(throws: DevContainerError.self) {
            try await runtime.startContainer(id: "fixture", context: context)
        }

        #expect(throws: DevContainerError.self) {
            _ = try AppleContainerRuntime(
                executable: fixture.root.appendingPathComponent("missing"),
                environment: [:]
            )
        }
    }

    @Test
    func `events report create start stop and destroy state transitions`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setState("missing")
        let runtime = try fixture.runtime()
        let stream = try await runtime.events(
            since: Date(timeIntervalSince1970: 0),
            until: Date().addingTimeInterval(3),
            labels: ["fixture": "yes"],
            context: RuntimeRequestContext()
        )
        let transitions = Task {
            try await Task.sleep(for: .milliseconds(300))
            try fixture.setState("running")
            try await Task.sleep(for: .milliseconds(500))
            try fixture.setState("stopped")
            try await Task.sleep(for: .milliseconds(500))
            try fixture.setState("missing")
        }

        var actions: [RuntimeEventAction] = []
        for try await event in stream {
            #expect(event.resourceID == "docker-fixture")
            #expect(event.attributes["name"] == "fixture")
            actions.append(event.action)
            if event.action == .destroy {
                break
            }
        }
        _ = try await transitions.value
        #expect(actions == [.create, .start, .stop, .destroy])
    }

    @Test
    func `stream failures and invalid requests surface typed errors`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        try fixture.setState("stopped")
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.createExec(
                containerID: "fixture",
                spec: ExecSpec(command: ["true"]),
                context: context
            )
        }

        try fixture.setMode("failure")
        let pull = try await runtime.pullImage(reference: "fixture", context: context)
        await #expect(throws: DevContainerError.self) {
            for try await _ in pull {}
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.buildImage(
                request: ImageBuildRequest(context: Data("not-a-tar".utf8)),
                context: context
            )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.descriptor(context: context)
        }
    }

    @Test
    func `process session supports input closure cancellation and error output`() async throws {
        let fixture = try FakeAppleCLI()
        let session = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["echo-session"],
            environment: [:],
            input: Data("input".utf8)
        )
        var standardOutput = Data()
        var standardError = Data()
        for try await frame in session.frames {
            switch frame.channel {
            case .standardOutput:
                standardOutput.append(frame.data)
            case .standardError:
                standardError.append(frame.data)
            case .standardInput:
                break
            }
        }
        #expect(try await session.wait() == 0)
        #expect(String(data: standardOutput, encoding: .utf8) == "input")
        #expect(String(data: standardError, encoding: .utf8) == "session-error")
        #expect(throws: DevContainerError.self) {
            try session.write(Data())
        }
        session.cancel()

        let interactive = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["cat-session"],
            environment: [:]
        )
        try interactive.write(Data("interactive".utf8))
        try interactive.closeStandardInput()
        var echoed = Data()
        for try await frame in interactive.frames where frame.channel == .standardOutput {
            echoed.append(frame.data)
        }
        #expect(try await interactive.wait() == 0)
        #expect(String(data: echoed, encoding: .utf8) == "interactive")

        let cancellable = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["sleep-session"],
            environment: [:]
        )
        cancellable.cancel()
        #expect(try await cancellable.wait() != 0)
    }

    private func waitForExec(
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

    private func minimalTar() -> Data {
        var header = Data(repeating: 0, count: 512)
        write("file.txt", into: &header, range: 0 ..< 100)
        write("0000644", into: &header, range: 100 ..< 108)
        write("0000000", into: &header, range: 108 ..< 116)
        write("0000000", into: &header, range: 116 ..< 124)
        write("00000000000", into: &header, range: 124 ..< 136)
        write("00000000000", into: &header, range: 136 ..< 148)
        for index in 148 ..< 156 {
            header[index] = 32
        }
        header[156] = 48
        write("ustar", into: &header, range: 257 ..< 263)
        let checksum = header.reduce(0) { $0 + UInt64($1) }
        write(String(format: "%06o", checksum), into: &header, range: 148 ..< 154)
        header[154] = 0
        header[155] = 32
        var archive = header
        archive.append(Data(repeating: 0, count: 1024))
        return archive
    }

    private func write(_ value: String, into data: inout Data, range: Range<Int>) {
        let bytes = Array(value.utf8.prefix(range.count))
        data.replaceSubrange(range.lowerBound ..< range.lowerBound + bytes.count, with: bytes)
    }
}

private struct FakeAppleCLI {
    let root: URL
    let executable: URL
    let logURL: URL
    private let stateURL: URL
    private let modeURL: URL

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-apple-runtime-tests-\(UUID().uuidString)")
        executable = root.appendingPathComponent("container")
        logURL = root.appendingPathComponent("commands.log")
        stateURL = root.appendingPathComponent("state")
        modeURL = root.appendingPathComponent("mode")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        try Data("running".utf8).write(to: stateURL)
        try Data("normal".utf8).write(to: modeURL)
        try Data(script.utf8).write(to: executable)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: executable.path
        )
    }

    func runtime(
        metadataStore: (any RuntimeMetadataStore)? = nil
    ) throws -> AppleContainerRuntime {
        try AppleContainerRuntime(
            executable: executable,
            environment: [:],
            useDirectProcessAPI: false,
            metadataStore: metadataStore,
            volumeRoot: root.appendingPathComponent("volumes", isDirectory: true)
        )
    }

    func log() throws -> String {
        try String(contentsOf: logURL, encoding: .utf8)
    }

    func setState(_ value: String) throws {
        try Data(value.utf8).write(to: stateURL)
    }

    func setMode(_ value: String) throws {
        try Data(value.utf8).write(to: modeURL)
    }

    private var script: String {
        let log = shellQuote(logURL.path)
        let state = shellQuote(stateURL.path)
        let mode = shellQuote(modeURL.path)
        return """
        #!/bin/sh
        set -eu
        LOG=\(log)
        STATE=\(state)
        MODE=\(mode)
        printf '%s\\n' "$*" >> "$LOG"
        mode=$(cat "$MODE")
        if [ "$mode" = failure ]; then
          printf '%s\\n' 'fixture failure' >&2
          exit 42
        fi
        if [ "$mode" = malformed ]; then
          printf '%s\\n' '{"not":"an array"}'
          exit 0
        fi
        state=$(cat "$STATE")
        case "$1 ${2-}" in
          "system version")
            printf '%s\\n' '[{
              "appName":"container",
              "version":"1.1.0",
              "commit":"fixture-commit",
              "distribution":"apple"
            }]'
            ;;
          "list --all"|"list --format")
            if [ "$state" = missing ]; then
              printf '%s\\n' '[]'
              exit 0
            fi
            if [ "$mode" = recreated ]; then
              printf '%s\\n' '[{
                "id":"fixture",
                "configuration":{
                  "image":{"reference":"fixture:latest"},
                  "initProcess":{
                    "executable":"/bin/sleep",
                    "arguments":["infinity"],
                    "environment":[],
                    "workingDirectory":"/workspace",
                    "terminal":false
                  },
                  "labels":{"com.apple.container.compose.oneoff":"false"},
                  "mounts":[],
                  "publishedPorts":[],
                  "creationDate":"2027-07-26T12:34:56.123Z"
                },
                "status":{"state":"running"}
              }]'
              exit 0
            fi
            if [ "$state" = stopped ] || [ "$state" = created ]; then
              cstate=stopped
            else
              cstate=running
            fi
            printf '%s\\n' '[{
              "id":"fixture",
              "configuration":{
                "image":{"reference":"fixture:latest"},
                "initProcess":{
                  "executable":"/bin/sleep",
                  "arguments":["infinity"],
                  "environment":["A=1","EMPTY="],
                  "workingDirectory":"/workspace",
                  "terminal":true,
                  "user":{"id":{"uid":501,"gid":20}},
                  "noNewPrivileges":true,
                  "privileged":true
                },
                "labels":{
                  "fixture":"yes",
                  "io.github.stephenlclarke.devcontainer.docker-id":"docker-fixture"
                },
                "mounts":[
                  {
                    "destination":"/bind",
                    "source":"/host",
                    "type":{"virtiofs":{}},
                    "options":["ro"]
                  },
                  {
                    "destination":"/volume",
                    "source":"cache",
                    "type":{"volume":{}}
                  },
                  {"destination":"/run","type":{"tmpfs":{}}},
                  {"source":"invalid"}
                ],
                "publishedPorts":[{
                  "containerPort":"8080",
                  "hostPort":18080,
                  "protocol":"tcp",
                  "hostAddress":"0.0.0.0"
                }],
                "creationDate":"2026-07-26T12:34:56.123Z",
                "hostname":"fixture-host",
                "useInit":true,
                "capAdd":["SYS_PTRACE"],
                "capDrop":["NET_RAW"],
                "unconfinedSystemPaths":true
              },
              "status":{
                "state":"'"$cstate"'",
                "exitCode":17,
                "networks":[{
                  "network":"bridge",
                  "ipv4Address":"192.0.2.10/24"
                }]
              }
            }]'
            ;;
          "image list")
            printf '%s\\n' '[{
              "id":"abc123",
              "configuration":{
                "name":"fixture:latest",
                "creationDate":"2026-07-26T12:34:56Z"
              },
              "variants":[
                {
                  "platform":{"architecture":"amd64","os":"linux"},
                  "size":1
                },
                {
                  "platform":{"architecture":"arm64","os":"linux"},
                  "size":12345,
                  "config":{"config":{
                    "User":"vscode",
                    "Env":["A=1"],
                    "Entrypoint":["/bin/sh"],
                    "Cmd":["sleep","infinity"],
                    "Labels":{"fixture":"yes"}
                  }}
                }
              ]
            }]'
            ;;
          "network list")
            printf '%s\\n' '[{
              "id":"network-id",
              "configuration":{
                "name":"fixture-network",
                "labels":{"fixture":"yes"},
                "plugin":"bridge",
                "mode":"isolated",
                "creationDate":"2026-07-26T12:34:56Z"
              }
            }]'
            ;;
          "volume list")
            printf '%s\\n' '[
              {"configuration":{
                "name":"fixture-volume",
                "labels":{"fixture":"yes"},
                "driver":"local",
                "source":"/var/lib/fixture",
                "creationDate":"2026-07-26T12:34:56Z"
              }},
              {"configuration":{
                "name":"buildx_buildkit_fixture_state",
                "labels":{"buildx":"yes"},
                "driver":"local",
                "source":"/native/buildkit",
                "creationDate":"2026-07-26T12:34:57Z"
              }}
            ]'
            ;;
          "logs "*)
            printf '%s\\n' 'log-output'
            printf '%s\\n' 'log-error' >&2
            ;;
          "exec "*)
            printf '%s\\n' 'exec-output'
            ;;
          "image pull")
            printf '%s\\n' 'pull-progress'
            ;;
          "image load")
            test -f "$4"
            printf '%s\\n' 'load-progress'
            ;;
          "build --file")
            printf '%s\\n' 'build-progress'
            ;;
          "start fixture")
            if [ "$state" = created ]; then
              printf '%s' running > "$STATE"
            fi
            ;;
          "stop --time")
            if [ "$4" = fixture ]; then
              printf '%s' stopped > "$STATE"
            fi
            ;;
          "cp "*)
            case "$2" in
              *:*)
                printf '%s' 'copied' > "$3"
                ;;
            esac
            ;;
          "echo-session ")
            input=$(cat)
            printf '%s' "$input"
            printf '%s' 'session-error' >&2
            ;;
          "cat-session ")
            cat
            ;;
          "sleep-session ")
            sleep 5
            ;;
        esac
        """
    }

    private func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}

private actor TestMetadataStore: RuntimeMetadataStore {
    private var values: [String: RuntimeContainerMetadata] = [:]

    func recordContainerMetadata(
        _ metadata: RuntimeContainerMetadata
    ) {
        values[metadata.runtimeID.rawValue] = metadata
    }

    func containerMetadata(
        id: String
    ) -> RuntimeContainerMetadata? {
        values[id]
    }

    func markContainerStarted(id: String, at date: Date) {
        values[id]?.startedAt = date
    }

    func removeContainerMetadata(id: String) {
        values[id] = nil
    }
}

private enum MetadataTestError: Error {
    case writeFailed
}

private actor FailingMetadataStore: RuntimeMetadataStore {
    func recordContainerMetadata(
        _: RuntimeContainerMetadata
    ) throws {
        throw MetadataTestError.writeFailed
    }

    func containerMetadata(
        id _: String
    ) -> RuntimeContainerMetadata? {
        nil
    }

    func markContainerStarted(id _: String, at _: Date) {}

    func removeContainerMetadata(id _: String) {}
}
