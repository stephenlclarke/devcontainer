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

import DevContainerCore
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public struct DockerRouter: Sendable {
    public let runtime: any DevContainerRuntime
    private let execSessions: ExecSessionRegistry
    let healthChecks: ContainerHealthRegistry

    public init(runtime: any DevContainerRuntime) {
        self.runtime = runtime
        execSessions = ExecSessionRegistry()
        healthChecks = ContainerHealthRegistry()
    }

    public func respond(to request: DockerHTTPRequest) async -> DockerHTTPResponse {
        do {
            return try await route(request)
        } catch let error as DevContainerError {
            return errorResponse(error)
        } catch let error as DecodingError {
            return errorResponse(
                DevContainerError(.invalidRequest, message: "invalid Docker request: \(error)")
            )
        } catch {
            return errorResponse(
                DevContainerError(.runtimeUnavailable, message: "runtime request failed: \(error)")
            )
        }
    }
}

private extension DockerRouter {
    private func route(_ request: DockerHTTPRequest) async throws -> DockerHTTPResponse {
        let target = try ParsedTarget(request.target)
        let path = stripAPIVersion(target.path)
        let route = DockerRoute(
            request: request,
            target: target,
            path: path,
            segments: path.split(separator: "/", omittingEmptySubsequences: true).map(String.init),
            context: RuntimeRequestContext()
        )

        if let response = try await daemonResponse(route) {
            return response
        }
        if let response = try await containerResponse(route) {
            return response
        }
        if let response = try await execResponse(route) {
            return response
        }
        if let response = try await imageResponse(route) {
            return response
        }
        if let response = try await resourceResponse(route) {
            return response
        }

        throw DevContainerError(
            .notFound,
            message: "page not found"
        )
    }

    private func daemonResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        try await daemonResponse(
            method: route.request.method,
            path: route.path,
            context: route.context
        )
    }

    private func containerResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        if let response = try await containerCollectionResponse(
            request: route.request,
            target: route.target,
            path: route.path,
            context: route.context
        ) {
            return response
        }
        return try await containerInstanceResponse(route)
    }

    private func containerInstanceResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        if let response = try await containerLifecycleResponse(
            request: route.request,
            target: route.target,
            segments: route.segments,
            context: route.context
        ) {
            return response
        }
        if let response = try await containerExecutionResponse(
            request: route.request,
            target: route.target,
            segments: route.segments,
            context: route.context
        ) {
            return response
        }
        if let response = try await containerStreamResponse(
            request: route.request,
            target: route.target,
            segments: route.segments,
            context: route.context
        ) {
            return response
        }
        if let response = try await containerArchiveResponse(
            request: route.request,
            target: route.target,
            segments: route.segments,
            context: route.context
        ) {
            return response
        }
        return try await containerRemovalResponse(
            method: route.request.method,
            target: route.target,
            segments: route.segments,
            context: route.context
        )
    }

    private func execResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        try await execResponse(
            request: route.request,
            target: route.target,
            segments: route.segments,
            context: route.context
        )
    }

    private func imageResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        if let response = try await imageReadResponse(
            method: route.request.method,
            path: route.path,
            context: route.context
        ) {
            return response
        }
        if let response = try await imageTransferResponse(
            request: route.request,
            target: route.target,
            path: route.path,
            context: route.context
        ) {
            return response
        }
        if let response = try await imageBuildResponse(
            request: route.request,
            target: route.target,
            path: route.path,
            context: route.context
        ) {
            return response
        }
        return try await imageMutationResponse(
            method: route.request.method,
            target: route.target,
            path: route.path,
            context: route.context
        )
    }

    private func resourceResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        if let response = try await networkResponse(route) {
            return response
        }
        if let response = try await volumeResponse(route) {
            return response
        }
        return try await eventResponse(
            method: route.request.method,
            target: route.target,
            path: route.path,
            context: route.context
        )
    }

    private func networkResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        if let response = try await networkCollectionResponse(
            request: route.request,
            path: route.path,
            context: route.context
        ) {
            return response
        }
        return try await networkInstanceResponse(
            request: route.request,
            segments: route.segments,
            context: route.context
        )
    }

    private func volumeResponse(_ route: DockerRoute) async throws -> DockerHTTPResponse? {
        if let response = try await volumeCollectionResponse(
            request: route.request,
            path: route.path,
            context: route.context
        ) {
            return response
        }
        return try await volumeInstanceResponse(
            method: route.request.method,
            target: route.target,
            segments: route.segments,
            context: route.context
        )
    }

    private func daemonResponse(
        method: DockerHTTPMethod,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if let response = pingResponse(method: method, path: path) {
            return response
        }
        if let response = try await versionResponse(method: method, path: path, context: context) {
            return response
        }
        return try await infoResponse(method: method, path: path, context: context)
    }

    private func pingResponse(
        method: DockerHTTPMethod,
        path: String
    ) -> DockerHTTPResponse? {
        if method == .get || method == .head, path == "/_ping" {
            var response = DockerHTTPResponse.text("OK")
            response.headers["API-Version"] = "1.53"
            response.headers["Docker-Experimental"] = "false"
            response.headers["OSType"] = "linux"
            if method == .head {
                response.body = .bytes(Data())
            }
            return response
        }
        return nil
    }

    private func versionResponse(
        method: DockerHTTPMethod,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard method == .get, path == "/version" else {
            return nil
        }
        let descriptor = try await runtime.descriptor(context: context)
        return try .json(
            DockerVersionResponse(
                platform: DockerVersionPlatform(name: "devcontainer Apple runtime bridge"),
                components: [
                    .init(
                        name: "Engine",
                        version: descriptor.providerVersion,
                        details: [
                            "ApiVersion": descriptor.dockerAPIMaximum,
                            "MinAPIVersion": descriptor.dockerAPIMinimum,
                            "Provider": descriptor.provider.rawValue,
                            "Distribution": descriptor.distribution
                        ]
                    )
                ],
                version: descriptor.providerVersion,
                apiVersion: descriptor.dockerAPIMaximum,
                minAPIVersion: descriptor.dockerAPIMinimum,
                gitCommit: descriptor.providerCommit,
                operatingSystem: "linux",
                buildTime: ISO8601DateFormatter().string(from: Date())
            )
        )
    }

    private func infoResponse(
        method: DockerHTTPMethod,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard method == .get, path == "/info" else {
            return nil
        }
        async let containers = runtime.listContainers(all: true, labels: [:], context: context)
        async let images = runtime.listImages(context: context)
        async let descriptor = runtime.descriptor(context: context)
        let (containerValues, imageValues, descriptorValue) = try await (
            containers,
            images,
            descriptor
        )
        return try .json(
            DockerInfoResponse(
                containers: containerValues.count,
                containersRunning: containerValues.count { $0.state == .running },
                containersStopped: containerValues.count { $0.state == .stopped },
                images: imageValues.count,
                serverVersion: descriptorValue.providerVersion
            )
        )
    }

    private func containerCollectionResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if request.method == .get, path == "/containers/json" {
            let all = target.first("all").map(Self.boolValue) ?? false
            let labels = try parseLabelFilters(target.first("filters"))
            let containers = try await runtime.listContainers(
                all: all,
                labels: [:],
                context: context
            )
            let filtered = try containers.filter { snapshot in
                let projected = try RuntimeLabels.projectComposeLabels(snapshot.spec.labels)
                return labels.allSatisfy { key, expected in
                    projected[key].map { expected.isEmpty || $0 == expected } ?? false
                }
            }
            return try .json(filtered.map(containerSummary))
        }

        if request.method == .post, path == "/containers/create" {
            let name = target.first("name") ?? ""
            let decoded = try DockerJSON.decoder.decode(DockerCreateContainerRequest.self, from: request.body)
            var spec = try containerSpec(from: decoded, requestedName: name)
            if spec.labels[RuntimeLabels.dockerID] == nil {
                spec.labels[RuntimeLabels.dockerID] = Self.dockerIdentifier()
            }
            let snapshot = try await runtime.createContainer(spec: spec, context: context)
            return try .json(
                DockerCreateContainerResponse(id: snapshot.dockerID.rawValue, warnings: []),
                status: 201
            )
        }
        return nil
    }

    private func containerLifecycleResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard segments.count >= 3, segments[0] == "containers" else {
            return nil
        }
        let id = segments[1]
        switch (request.method, segments[2]) {
        case (.get, "json"):
            let snapshot = try await runtime.inspectContainer(id: id, context: context)
            let health = try await containerHealth(snapshot: snapshot, context: context)
            return try .json(containerInspect(snapshot, health: health))
        case (.post, "start"):
            try await runtime.startContainer(id: id, context: context)
            await healthChecks.reset(id: id)
            return .empty(status: 204)
        case (.post, "stop"):
            let seconds = target.first("t").flatMap(Int64.init)
            try await runtime.stopContainer(
                id: id,
                timeout: seconds.map(Duration.seconds),
                context: context
            )
            return .empty(status: 204)
        case (.post, "restart"):
            let seconds = target.first("t").flatMap(Int64.init)
            try await runtime.stopContainer(
                id: id,
                timeout: seconds.map(Duration.seconds),
                context: context
            )
            try await runtime.startContainer(id: id, context: context)
            await healthChecks.reset(id: id)
            return .empty(status: 204)
        case (.post, "kill"):
            try await runtime.killContainer(
                id: id,
                signal: target.first("signal") ?? "SIGKILL",
                context: context
            )
            return .empty(status: 204)
        default:
            return nil
        }
    }

    private func containerExecutionResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard segments.count >= 3, segments[0] == "containers" else {
            return nil
        }
        let id = segments[1]
        switch (request.method, segments[2]) {
        case (.post, "wait"):
            return DockerHTTPResponse(
                status: 200,
                headers: ["Content-Type": "application/json"],
                body: .stream(
                    containerWaitStream(
                        id: id,
                        condition: target.first("condition"),
                        context: context
                    )
                )
            )
        case (.post, "exec"):
            let decoded = try DockerJSON.decoder.decode(DockerCreateExecRequest.self, from: request.body)
            guard decoded.privileged != true else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "privileged exec is not supported"
                )
            }
            let exec = try await runtime.createExec(
                containerID: id,
                spec: execSpec(from: decoded),
                context: context
            )
            return try .json(DockerCreateExecResponse(id: exec.id.rawValue), status: 201)
        default:
            return nil
        }
    }

    private func containerStreamResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard segments.count >= 3, segments[0] == "containers" else {
            return nil
        }
        let id = segments[1]
        switch (request.method, segments[2]) {
        case (.get, "logs"):
            let stream = try await runtime.containerLogs(
                id: id,
                follow: target.first("follow").map(Self.boolValue) ?? false,
                standardOutput: target.first("stdout").map(Self.boolValue) ?? true,
                standardError: target.first("stderr").map(Self.boolValue) ?? true,
                context: context
            )
            return DockerHTTPResponse(
                status: 200,
                headers: ["Content-Type": "application/vnd.docker.raw-stream"],
                body: .stream(stream.mapData { DockerStreamFraming.encode($0, terminal: false) })
            )
        case (.post, "attach"):
            let terminal = try await runtime.inspectContainer(id: id, context: context).spec.terminal
            let session = try await runtime.attachContainer(
                id: id,
                terminal: terminal,
                context: context
            )
            return DockerHTTPResponse(
                status: 101,
                headers: [
                    "Connection": "Upgrade",
                    "Upgrade": "tcp",
                    "Content-Type": "application/vnd.docker.raw-stream"
                ],
                body: .hijack(session, terminal: terminal)
            )
        default:
            return nil
        }
    }

    private func execSpec(from request: DockerCreateExecRequest) -> ExecSpec {
        ExecSpec(
            command: request.cmd,
            environment: environmentDictionary(request.env ?? []),
            workingDirectory: request.workingDir,
            user: request.user,
            terminal: request.tty ?? false,
            attachStandardInput: request.attachStdin ?? false,
            attachStandardOutput: request.attachStdout ?? true,
            attachStandardError: request.attachStderr ?? true
        )
    }

    private func containerArchiveResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard segments.count >= 3, segments[0] == "containers", segments[2] == "archive" else {
            return nil
        }
        guard let path = target.first("path"), !path.isEmpty else {
            throw DevContainerError(.invalidRequest, message: "archive path is required")
        }
        let id = segments[1]
        switch request.method {
        case .get:
            let archive = try await runtime.copyArchiveFromContainer(
                id: id,
                path: path,
                context: context
            )
            return try DockerHTTPResponse(
                status: 200,
                headers: [
                    "Content-Type": "application/x-tar",
                    "X-Docker-Container-Path-Stat": archiveStatHeader(archive.stat)
                ],
                body: .bytes(archive.data)
            )
        case .head:
            let archive = try await runtime.copyArchiveFromContainer(
                id: id,
                path: path,
                context: context
            )
            return try DockerHTTPResponse(
                status: 200,
                headers: ["X-Docker-Container-Path-Stat": archiveStatHeader(archive.stat)]
            )
        case .put:
            try await runtime.copyArchiveToContainer(
                id: id,
                path: path,
                archive: request.body,
                context: context
            )
            return .empty(status: 200)
        default:
            return nil
        }
    }

    private func containerRemovalResponse(
        method: DockerHTTPMethod,
        target: ParsedTarget,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard method == .delete, segments.count == 2, segments[0] == "containers" else {
            return nil
        }
        let id = segments[1]
        try await runtime.removeContainer(
            id: id,
            force: target.first("force").map(Self.boolValue) ?? false,
            context: context
        )
        await healthChecks.remove(id: id)
        return .empty(status: 204)
    }

    private func execResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard segments.count >= 3, segments[0] == "exec" else {
            return nil
        }
        let id = ExecID(rawValue: segments[1])
        switch (request.method, segments[2]) {
        case (.get, "json"):
            let exec = try await runtime.inspectExec(id: id, context: context)
            return try .json(execInspect(exec))
        case (.post, "start"):
            return try await startExecResponse(
                request: request,
                id: id,
                context: context
            )
        case (.post, "resize"):
            let width = try unsigned16(target.first("w"), name: "width")
            let height = try unsigned16(target.first("h"), name: "height")
            guard let session = await execSessions.session(id: id) else {
                throw DevContainerError(
                    .conflict,
                    message: "exec \(id) is not running"
                )
            }
            try await session.resize(width: width, height: height)
            return .empty(status: 200)
        default:
            return nil
        }
    }

    private func startExecResponse(
        request: DockerHTTPRequest,
        id: ExecID,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse {
        let options = try DockerJSON.decoder.decode(DockerStartExecRequest.self, from: request.body)
        let exec = try await runtime.inspectExec(id: id, context: context)
        let session = try await runtime.startExec(id: id, context: context)
        let registration = await execSessions.register(session, id: id)
        Task {
            _ = try? await session.wait()
            await execSessions.remove(id: id, registration: registration)
        }
        if options.detach == true {
            return .empty(status: 200)
        }
        return DockerHTTPResponse(
            status: 101,
            headers: [
                "Connection": "Upgrade",
                "Upgrade": "tcp",
                "Content-Type": "application/vnd.docker.raw-stream"
            ],
            body: .hijack(session, terminal: options.tty ?? exec.spec.terminal)
        )
    }

    private func imageReadResponse(
        method: DockerHTTPMethod,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if method == .get, path == "/images/json" {
            return try await .json(
                runtime.listImages(context: context).map(imageSummary)
            )
        }
        if method == .get,
           let reference = identifier(in: path, prefix: "/images/", suffix: "/json")
        {
            return try await .json(
                imageInspect(runtime.inspectImage(reference: reference, context: context))
            )
        }
        return nil
    }

    private func imageTransferResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if request.method == .post, path == "/images/create" {
            guard let source = target.first("fromImage"), !source.isEmpty else {
                throw DevContainerError(.invalidRequest, message: "fromImage is required")
            }
            let reference = target.first("tag").map { "\(source):\($0)" } ?? source
            let stream = try await runtime.pullImage(reference: reference, context: context)
            return DockerHTTPResponse(
                status: 200,
                headers: ["Content-Type": "application/json"],
                body: .stream(dockerProgressStream(stream, status: "Pulling"))
            )
        }
        if request.method == .post, path == "/images/load" {
            guard !request.body.isEmpty else {
                throw DevContainerError(.invalidRequest, message: "image archive is required")
            }
            let stream = try await runtime.loadImage(
                archive: request.body,
                context: context
            )
            return DockerHTTPResponse(
                status: 200,
                headers: ["Content-Type": "application/json"],
                body: .stream(dockerProgressStream(stream, status: "Loading"))
            )
        }
        return nil
    }

    private func imageBuildResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard request.method == .post, path == "/build" else {
            return nil
        }
        let stream = try await runtime.buildImage(
            request: ImageBuildRequest(
                context: request.body,
                dockerfile: target.first("dockerfile") ?? "Dockerfile",
                tags: target.query["t"] ?? [],
                buildArguments: stringDictionary(target.first("buildargs"), name: "buildargs"),
                target: target.first("target"),
                labels: stringDictionary(target.first("labels"), name: "labels")
            ),
            context: context
        )
        return DockerHTTPResponse(
            status: 200,
            headers: ["Content-Type": "application/json"],
            body: .stream(dockerProgressStream(stream, status: nil))
        )
    }

    private func imageMutationResponse(
        method: DockerHTTPMethod,
        target: ParsedTarget,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if method == .post,
           let reference = identifier(in: path, prefix: "/images/", suffix: "/tag")
        {
            guard let repository = target.first("repo"), !repository.isEmpty else {
                throw DevContainerError(.invalidRequest, message: "tag repository is required")
            }
            let destination = target.first("tag").map { "\(repository):\($0)" } ?? repository
            try await runtime.tagImage(source: reference, target: destination, context: context)
            return .empty(status: 201)
        }
        if method == .delete,
           let reference = identifier(in: path, prefix: "/images/", suffix: nil)
        {
            try await runtime.removeImage(
                reference: reference,
                force: target.first("force").map(Self.boolValue) ?? false,
                context: context
            )
            return try .json([DockerImageDeleteResponse(deleted: reference, untagged: nil)])
        }
        return nil
    }

    private func networkCollectionResponse(
        request: DockerHTTPRequest,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if request.method == .get, path == "/networks" {
            return try await .json(
                runtime.listNetworks(context: context).map(networkInspect)
            )
        }
        if request.method == .post, path == "/networks/create" {
            let decoded = try DockerJSON.decoder.decode(DockerNetworkCreateRequest.self, from: request.body)
            guard decoded.driver == nil || decoded.driver == "" || decoded.driver == "bridge" else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "network driver \(decoded.driver ?? "") is not supported"
                )
            }
            let network = try await runtime.createNetwork(
                spec: NetworkSpec(
                    name: decoded.name,
                    labels: decoded.labels ?? [:],
                    driver: decoded.driver.flatMap { $0.isEmpty ? nil : $0 } ?? "bridge",
                    internalNetwork: decoded.internalNetwork ?? false
                ),
                context: context
            )
            return try .json(
                DockerNetworkCreateResponse(id: network.id, warning: ""),
                status: 201
            )
        }
        return nil
    }

    private func networkInstanceResponse(
        request: DockerHTTPRequest,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard segments.count >= 2, segments[0] == "networks" else {
            return nil
        }
        let id = segments[1]
        switch (request.method, segments.count == 3 ? segments[2] : "") {
        case (.get, ""):
            return try await .json(
                networkInspect(runtime.inspectNetwork(id: id, context: context))
            )
        case (.post, "connect"):
            let decoded = try DockerJSON.decoder.decode(DockerNetworkConnectRequest.self, from: request.body)
            try await runtime.connectNetwork(
                id: id,
                containerID: decoded.container,
                aliases: decoded.endpointConfig?.aliases ?? [],
                context: context
            )
            return .empty(status: 200)
        case (.post, "disconnect"):
            let decoded = try DockerJSON.decoder.decode(DockerNetworkDisconnectRequest.self, from: request.body)
            try await runtime.disconnectNetwork(
                id: id,
                containerID: decoded.container,
                force: decoded.force ?? false,
                context: context
            )
            return .empty(status: 200)
        case (.delete, ""):
            try await runtime.removeNetwork(id: id, context: context)
            return .empty(status: 204)
        default:
            return nil
        }
    }

    private func volumeCollectionResponse(
        request: DockerHTTPRequest,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if request.method == .get, path == "/volumes" {
            return try await .json(
                DockerVolumeListResponse(
                    volumes: runtime.listVolumes(context: context).map(volumeInspect),
                    warnings: []
                )
            )
        }
        if request.method == .post, path == "/volumes/create" {
            let decoded = try DockerJSON.decoder.decode(DockerVolumeCreateRequest.self, from: request.body)
            let name = decoded.name.flatMap { $0.isEmpty ? nil : $0 }
                ?? "devcontainer-\(UUID().uuidString.prefix(12).lowercased())"
            guard decoded.driver == nil || decoded.driver == "local" else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "volume driver \(decoded.driver ?? "") is not supported"
                )
            }
            let volume = try await runtime.createVolume(
                spec: VolumeSpec(
                    name: name,
                    labels: decoded.labels ?? [:],
                    driver: decoded.driver ?? "local"
                ),
                context: context
            )
            return try .json(volumeInspect(volume), status: 201)
        }
        return nil
    }

    private func volumeInstanceResponse(
        method: DockerHTTPMethod,
        target: ParsedTarget,
        segments: [String],
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard segments.count == 2, segments[0] == "volumes" else {
            return nil
        }
        let name = segments[1]
        switch method {
        case .get:
            return try await .json(
                volumeInspect(runtime.inspectVolume(name: name, context: context))
            )
        case .delete:
            try await runtime.removeVolume(
                name: name,
                force: target.first("force").map(Self.boolValue) ?? false,
                context: context
            )
            return .empty(status: 204)
        default:
            return nil
        }
    }

    private func eventResponse(
        method: DockerHTTPMethod,
        target: ParsedTarget,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        guard method == .get, path == "/events" else {
            return nil
        }
        let filters = try parseFilters(target.first("filters"))
        let labels = try labelFilters(filters["label"] ?? [])
        let stream = try await runtime.events(
            since: dateQuery(target.first("since"), name: "since"),
            until: dateQuery(target.first("until"), name: "until"),
            labels: RuntimeLabels.translateDockerFilters(labels),
            context: context
        )
        return DockerHTTPResponse(
            status: 200,
            headers: ["Content-Type": "application/json"],
            body: .stream(eventStream(stream, acceptedActions: Set(filters["event"] ?? [])))
        )
    }

    private func errorResponse(_ error: DevContainerError) -> DockerHTTPResponse {
        let status = switch error.code {
        case .invalidRequest:
            400
        case .authentication:
            401
        case .notFound:
            404
        case .conflict:
            409
        case .unsupportedCapability:
            501
        case .build:
            500
        case .cancelled, .deadlineExceeded:
            408
        case .providerProtocolMismatch, .runtimeUnavailable, .stateCorruption:
            500
        }
        return (try? .json(DockerErrorEnvelope(message: error.description), status: status))
            ?? .text(error.description, status: status)
    }
}

private struct DockerRoute {
    let request: DockerHTTPRequest
    let target: ParsedTarget
    let path: String
    let segments: [String]
    let context: RuntimeRequestContext
}

private struct ParsedTarget {
    let path: String
    let query: [String: [String]]

    init(_ target: String) throws {
        guard
            let components = URLComponents(string: target),
            !components.path.isEmpty
        else {
            throw DevContainerError(.invalidRequest, message: "invalid request target")
        }
        path = components.percentEncodedPath
        query = Dictionary(grouping: components.queryItems ?? [], by: \.name)
            .mapValues { $0.compactMap(\.value) }
    }

    func first(_ name: String) -> String? {
        query[name]?.first
    }
}

private extension AsyncThrowingStream where Element == RuntimeIOFrame, Failure == any Error {
    func mapData(
        _ transform: @escaping @Sendable (RuntimeIOFrame) -> Data
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream<Data, any Error> { continuation in
            let task = Task {
                do {
                    for try await element in self {
                        continuation.yield(transform(element))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }
}
