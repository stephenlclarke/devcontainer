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
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

// Runtime scenarios stay in one serialized suite until direct-client live
// fixtures can be split from CLI fallback fixtures.
// swiftlint:disable file_length

@Suite(.serialized)
struct AppleContainerRuntimeTests {
    @Test
    func `native build DNS arguments use valid resolver nameservers`() {
        let configuration = """
        # Generated resolver configuration
        nameserver 192.0.2.53
        nameserver 2001:db8::53
        nameserver fe80::1%en0
        nameserver 192.0.2.53
        nameserver invalid.example
        search example.test

        """

        #expect(
            AppleContainerRuntime.buildDNSArguments(
                resolverConfiguration: configuration
            ) == [
                "--dns", "192.0.2.53",
                "--dns", "2001:db8::53",
                "--dns", "fe80::1%en0"
            ]
        )
        #expect(
            AppleContainerRuntime.requiresHostDNS(
                ContainerSpec(
                    name: "buildx_buildkit_devcontainer0",
                    image: "moby/buildkit:buildx-stable-1"
                )
            )
        )
        #expect(
            !AppleContainerRuntime.requiresHostDNS(
                ContainerSpec(name: "application", image: "buildkit-client:latest")
            )
        )
    }

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
    func `native states events and file modes map to Docker values`() {
        #expect(AppleContainerRuntime.eventAction(for: .running) == .start)
        #expect(AppleContainerRuntime.eventAction(for: .stopped) == .stop)
        #expect(AppleContainerRuntime.eventAction(for: .unknown) == nil)
        #expect(
            AppleContainerRuntime.containerState(
                "stopped",
                createdByThisEngine: true,
                wasStarted: false
            ) == .created
        )
        #expect(AppleContainerRuntime.dockerFileTypeMode(S_IFDIR) == 1 << 31)
        #expect(AppleContainerRuntime.dockerFileTypeMode(S_IFLNK) == 1 << 27)
        #expect(AppleContainerRuntime.dockerFileTypeMode(S_IFREG) == 0)
        #expect(AppleContainerRuntime.dockerModeBit(S_ISUID, mask: S_ISUID, bit: 8) == 8)
        #expect(
            AppleContainerRuntime.isTransientContainerCopyFailure(
                AppleCommandResult(
                    standardOutput: Data(),
                    standardError: Data(
                        #"internalError: invalidState: "cannot copyOut: container is not running""#.utf8
                    ),
                    exitCode: 1
                )
            )
        )
        #expect(
            !AppleContainerRuntime.isTransientContainerCopyFailure(
                AppleCommandResult(
                    standardOutput: Data(),
                    standardError: Data("permission denied".utf8),
                    exitCode: 1
                )
            )
        )
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

        try await assertContainerInventory(runtime, context: context)
        try await assertImageInventory(runtime, context: context)
        try await assertNetworkInventory(runtime, context: context)
        try await assertVolumeInventory(runtime, context: context)
    }

    @Test
    func `stock create rejects options it cannot enforce before native creation`() async throws {
        let fixture = try FakeAppleCLI(enhancedCreateOptions: false)
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        do {
            _ = try await runtime.createContainer(
                spec: ContainerSpec(
                    name: "fixture",
                    image: "fixture:latest",
                    hostname: "requested-host"
                ),
                context: context
            )
            Issue.record("stock hostname creation unexpectedly succeeded")
        } catch let error as DevContainerError {
            #expect(error.code == .unsupportedCapability)
            #expect(error.message.contains("--hostname"))
        }

        do {
            _ = try await runtime.createContainer(
                spec: ContainerSpec(
                    name: "fixture",
                    image: "fixture:latest",
                    securityOptions: ["no-new-privileges=true"]
                ),
                context: context
            )
            Issue.record("stock security option creation unexpectedly succeeded")
        } catch let error as DevContainerError {
            #expect(error.code == .unsupportedCapability)
            #expect(error.message.contains("--security-opt"))
        }

        do {
            _ = try await runtime.createContainer(
                spec: ContainerSpec(
                    name: "fixture",
                    image: "fixture:latest",
                    privileged: true
                ),
                context: context
            )
            Issue.record("stock privileged creation unexpectedly succeeded")
        } catch let error as DevContainerError {
            #expect(error.code == .unsupportedCapability)
            #expect(error.message.contains("--privileged"))
        }
        let log = try fixture.log()
        #expect(log.contains("create --help"))
        #expect(!log.contains("create --name fixture"))
        #expect(!log.contains("--cap-add ALL"))
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
    func `internal Apple builder is excluded and stale metadata is removed`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setMode("internal-builder")
        let store = TestMetadataStore()
        await store.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: RuntimeID(rawValue: "buildkit"),
                dockerID: DockerID(rawValue: "docker-buildkit"),
                spec: ContainerSpec(name: "buildkit", image: "builder:latest"),
                createdAt: Date(timeIntervalSince1970: 1)
            )
        )
        let runtime = try fixture.runtime(metadataStore: store)

        let containers = try await runtime.listContainers(
            all: true,
            labels: [:],
            context: RuntimeRequestContext()
        )

        #expect(containers.isEmpty)
        #expect(await store.containerMetadata(id: "buildkit") == nil)
    }

    @Test
    func `native compose containers receive stable Docker identities`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setMode("recreated")
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store)

        let first = try #require(
            try await runtime.listContainers(
                all: true,
                labels: [:],
                context: RuntimeRequestContext()
            ).first
        )
        let second = try #require(
            try await runtime.listContainers(
                all: true,
                labels: [:],
                context: RuntimeRequestContext()
            ).first
        )

        #expect(first.dockerID == second.dockerID)
        #expect(first.dockerID.rawValue.count == 64)
        #expect(
            first.dockerID.rawValue.allSatisfy {
                $0.isNumber || ("a" ... "f").contains(String($0))
            }
        )
        #expect(
            await store.containerMetadata(id: "fixture")?.dockerID
                == first.dockerID
        )
    }

    @Test
    func `metadata for externally removed native containers is pruned`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore()
        await store.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: RuntimeID(rawValue: "externally-removed"),
                dockerID: DockerID(rawValue: "stale-docker-id"),
                spec: ContainerSpec(
                    name: "externally-removed",
                    image: "fixture:latest"
                ),
                createdAt: Date()
            )
        )
        let runtime = try fixture.runtime(metadataStore: store)

        _ = try await runtime.listContainers(
            all: true,
            labels: [:],
            context: RuntimeRequestContext()
        )

        #expect(
            await store.containerMetadata(id: "externally-removed") == nil
        )
    }

    @Test
    func `container listing batches metadata reads`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store)

        _ = try await runtime.listContainers(
            all: true,
            labels: [:],
            context: RuntimeRequestContext()
        )
        await store.resetAccessCounts()

        _ = try await runtime.listContainers(
            all: true,
            labels: [:],
            context: RuntimeRequestContext()
        )

        let counts = await store.accessCounts()
        #expect(counts.list == 1)
        #expect(counts.lookup == 0)
    }

    @Test
    func `container listing excludes missing label values`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()

        let containers = try await runtime.listContainers(
            all: true,
            labels: ["missing": "yes"],
            context: RuntimeRequestContext()
        )

        #expect(containers.isEmpty)
    }

    @Test
    func `container resolution distinguishes missing ambiguous and exact identifiers`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let first = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "native-first"),
            dockerID: DockerID(rawValue: "abcdef000000"),
            spec: ContainerSpec(name: "first", image: "fixture:latest"),
            state: .running,
            createdAt: Date(timeIntervalSince1970: 1)
        )
        let second = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "native-second"),
            dockerID: DockerID(rawValue: "abcdef111111"),
            spec: ContainerSpec(name: "second", image: "fixture:latest"),
            state: .running,
            createdAt: Date(timeIntervalSince1970: 2)
        )

        let exact = try await runtime.resolvedContainerSnapshot(
            id: first.dockerID.rawValue,
            in: [first, second]
        )
        #expect(exact == first)
        do {
            _ = try await runtime.resolvedContainerSnapshot(id: "abcdef", in: [first, second])
            Issue.record("ambiguous prefix unexpectedly resolved")
        } catch let error as DevContainerError {
            #expect(error.code == .invalidRequest)
        }
        do {
            _ = try await runtime.resolvedContainerSnapshot(id: "missing", in: [first, second])
            Issue.record("missing identifier unexpectedly resolved")
        } catch let error as DevContainerError {
            #expect(error.code == .notFound)
        }
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
        await runtime.handleContainerExit(
            AppleContainerRuntime.ContainerExit(
                code: 99,
                finishedAt: Date()
            ),
            id: "fixture"
        )
        #expect(await runtime.containerExits["fixture"]?.code == 99)

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
        #expect(await runtime.requestedContainers["fixture"] == nil)
        #expect(await runtime.containerExits["fixture"] == nil)
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
    func `metadata failure leaves an Apple container rename unchanged`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime(metadataStore: FailingMetadataStore())
        let context = RuntimeRequestContext()

        await #expect(throws: MetadataTestError.self) {
            try await runtime.renameContainer(
                id: "fixture",
                name: "renamed",
                context: context
            )
        }

        #expect(
            try await runtime.inspectContainer(
                id: "fixture",
                context: context
            ).spec.name == "fixture"
        )
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.inspectContainer(
                id: "renamed",
                context: context
            )
        }
    }

    @Test
    func `automatic removal follows process exit without a wait subscriber`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store)
        let context = RuntimeRequestContext()
        let container = try await runtime.createContainer(
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                autoRemove: true
            ),
            context: context
        )
        try await runtime.startContainer(
            id: container.runtimeID.rawValue,
            context: context
        )

        await runtime.handleContainerExit(
            AppleContainerRuntime.ContainerExit(
                code: 23,
                finishedAt: Date()
            ),
            id: container.runtimeID.rawValue
        )
        #expect(
            try await runtime.waitContainer(
                id: container.runtimeID.rawValue,
                context: context
            ) == 23
        )

        let deadline = ContinuousClock.now + .seconds(5)
        while await store.containerMetadata(id: "fixture") != nil,
              ContinuousClock.now < deadline
        {
            try await Task.sleep(for: .milliseconds(25))
        }
        #expect(try fixture.log().contains("delete --force fixture"))
        #expect(await store.containerMetadata(id: "fixture") == nil)
    }

    @Test
    func `container lifecycle encodes every supported Apple CLI option`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        _ = try await runtime.createContainer(spec: supportedContainerSpec(), context: context)
        try await runtime.startContainer(id: "fixture", context: context)
        try await runtime.stopContainer(id: "fixture", timeout: .milliseconds(1001), context: context)
        try await runtime.killContainer(id: "fixture", signal: "SIGTERM", context: context)
        try await runtime.renameContainer(id: "fixture", name: "renamed", context: context)
        try await runtime.removeContainer(id: "renamed", force: true, context: context)
        #expect(await runtime.requestedContainers["docker-fixture"] == nil)
        #expect(await runtime.requestedContainers["renamed"] == nil)

        try assertLifecycleLog(fixture.log())

        let metadata = #"{"postCreateCommand":"value=$(printf A=B)"}"#
        let metadataContainer = try await runtime.createContainer(
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture",
                command: ["printf ok"],
                entrypoint: ["/bin/sh", "-c"],
                labels: ["devcontainer.metadata": metadata]
            ),
            context: context
        )
        #expect(
            try fixture.log().contains(
                "create --name fixture --entrypoint /bin/sh fixture -c printf ok"
            )
        )
        #expect(try !fixture.log().contains("devcontainer.metadata"))
        #expect(
            metadataContainer.spec.labels["devcontainer.metadata"] == metadata
        )
        try await runtime.renameContainer(id: "fixture", name: "renamed", context: context)
        #expect(
            try await runtime.inspectContainer(id: "renamed", context: context).spec.name
                == "renamed"
        )
    }

    @Test
    func `concurrent starts share one native bootstrap`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setState("created")
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        try await withThrowingTaskGroup(of: Void.self) { group in
            for _ in 0 ..< 4 {
                group.addTask {
                    try await runtime.startContainer(
                        id: "fixture",
                        context: context
                    )
                }
            }
            try await group.waitForAll()
        }

        let starts = try fixture.log().split(separator: "\n")
            .filter { $0 == "start fixture" }
        #expect(starts.count == 1)
    }

    @Test
    func `exec logs attach pull and build stream process output`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        try await assertContainerLogsAndAttach(
            fixture: fixture,
            runtime: runtime,
            context: context
        )
        try await assertExecSession(runtime, context: context)
        try await assertImageStreams(fixture: fixture, runtime: runtime, context: context)
    }

    @Test
    func `direct exec launch failure completes its snapshot`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime(useDirectProcessAPI: true)
        let context = RuntimeRequestContext()
        let exec = try await runtime.createExec(
            containerID: "fixture",
            spec: ExecSpec(command: []),
            context: context
        )

        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.startExec(id: exec.id, context: context)
        }
        let completed = try await runtime.inspectExec(id: exec.id, context: context)
        #expect(!completed.running)
        #expect(completed.exitCode == 255)
    }

    @Test
    func `network volume image and archive mutations delegate safely`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        try await exerciseNetworkAndVolumeMutations(runtime, context: context)
        try await exerciseImageAndArchiveMutations(
            fixture: fixture,
            runtime: runtime,
            context: context
        )
        try assertMutationLog(fixture.log())
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
            until: Date().addingTimeInterval(15),
            labels: ["fixture": "yes"],
            context: RuntimeRequestContext()
        )
        try fixture.setState("running")

        var actions: [RuntimeEventAction] = []
        events: for try await event in stream {
            #expect(event.resourceID == "docker-fixture")
            #expect(event.attributes["name"] == "fixture")
            actions.append(event.action)
            switch event.action {
            case .start:
                try fixture.setState("stopped")
            case .stop:
                try fixture.setState("missing")
            case .destroy:
                break events
            default:
                continue
            }
        }
        #expect(actions == [.create, .start, .stop, .destroy])
    }
}

struct FakeAppleCLI {
    let root: URL
    let executable: URL
    let logURL: URL
    private let stateURL: URL
    private let modeURL: URL
    private let enhancedCreateOptions: Bool
    private let distribution: String

    init(
        enhancedCreateOptions: Bool = true,
        distribution: String = "apple"
    ) throws {
        self.enhancedCreateOptions = enhancedCreateOptions
        self.distribution = distribution
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
        metadataStore: (any RuntimeMetadataStore)? = nil,
        useDirectProcessAPI: Bool = false
    ) throws -> AppleContainerRuntime {
        try AppleContainerRuntime(
            executable: executable,
            environment: [:],
            useDirectProcessAPI: useDirectProcessAPI,
            useDirectContainerAPI: false,
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
        let createHelp = enhancedCreateOptions
            ? "--hostname\\n--publish\\n--privileged\\n--security-opt\\n--dns"
            : "--cap-add\\n--cap-drop\\n--publish"
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
              "distribution":"\(distribution)"
            }]'
            ;;
          "create --help")
            printf '%b\\n' '\(createHelp)'
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
            if [ "$mode" = internal-builder ]; then
              printf '%s\\n' '[{
                "id":"buildkit",
                "configuration":{
                  "image":{"reference":"builder:latest"},
                  "initProcess":{
                    "executable":"buildkitd",
                    "arguments":[],
                    "environment":[],
                    "workingDirectory":"/",
                    "terminal":false
                  },
                  "labels":{
                    "com.apple.container.resource.role":"builder",
                    "com.apple.container.plugin":"builder"
                  },
                  "mounts":[],
                  "publishedPorts":[],
                  "creationDate":"2026-07-29T17:31:09Z"
                },
                "status":{"state":"stopped"}
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
                  "environment":["A=1","EMPTY=","A=last"],
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
                  "hostPort":0,
                  "protocol":"tcp",
                  "hostAddress":"127.0.0.1"
                }],
                "networks":[{
                  "network":"bridge",
                  "options":{"aliases":["workspace"]}
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
            if grep -q '^ADD context.tar /tmp/build-features/$' "$3"; then
              test -f "$(dirname "$3")/context.tar"
              printf '%s\n' prepared-feature-context >> "$LOG"
            fi
            printf '%s\\n' 'build-progress'
            ;;
          "start fixture")
            if [ "$state" = created ]; then
              sleep 0.1
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
