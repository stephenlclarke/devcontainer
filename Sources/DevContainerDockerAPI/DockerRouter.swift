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
    private let healthChecks: ContainerHealthRegistry

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

    private func route(_ request: DockerHTTPRequest) async throws -> DockerHTTPResponse {
        let target = try ParsedTarget(request.target)
        let path = stripAPIVersion(target.path)
        let segments = path.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        let context = RuntimeRequestContext()

        if request.method == .get || request.method == .head, path == "/_ping" {
            var response = DockerHTTPResponse.text("OK")
            response.headers["API-Version"] = "1.53"
            response.headers["Docker-Experimental"] = "false"
            response.headers["OSType"] = "linux"
            if request.method == .head {
                response.body = .bytes(Data())
            }
            return response
        }

        if request.method == .get, path == "/version" {
            let descriptor = try await runtime.descriptor(context: context)
            return try .json(
                DockerVersionResponse(
                    platform: DockerVersionPlatform(
                        name: "devcontainer Apple runtime bridge"
                    ),
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

        if request.method == .get, path == "/info" {
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
                    guard let actual = projected[key] else {
                        return false
                    }
                    return expected.isEmpty || actual == expected
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

        if segments.count >= 3, segments[0] == "containers" {
            let id = segments[1]
            let action = segments[2]
            switch (request.method, action) {
            case (.get, "json"):
                let snapshot = try await runtime.inspectContainer(
                    id: id,
                    context: context
                )
                let health = try await containerHealth(
                    snapshot: snapshot,
                    context: context
                )
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
                    spec: ExecSpec(
                        command: decoded.cmd,
                        environment: environmentDictionary(decoded.env ?? []),
                        workingDirectory: decoded.workingDir,
                        user: decoded.user,
                        terminal: decoded.tty ?? false,
                        attachStandardInput: decoded.attachStdin ?? false,
                        attachStandardOutput: decoded.attachStdout ?? true,
                        attachStandardError: decoded.attachStderr ?? true
                    ),
                    context: context
                )
                return try .json(DockerCreateExecResponse(id: exec.id.rawValue), status: 201)
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
                    body: .stream(
                        stream.mapData { DockerStreamFraming.encode($0, terminal: false) }
                    )
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
            case (.get, "archive"):
                guard let archivePath = target.first("path"), !archivePath.isEmpty else {
                    throw DevContainerError(.invalidRequest, message: "archive path is required")
                }
                let archive = try await runtime.copyArchiveFromContainer(
                    id: id,
                    path: archivePath,
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
            case (.head, "archive"):
                guard let archivePath = target.first("path"), !archivePath.isEmpty else {
                    throw DevContainerError(.invalidRequest, message: "archive path is required")
                }
                let archive = try await runtime.copyArchiveFromContainer(
                    id: id,
                    path: archivePath,
                    context: context
                )
                return try DockerHTTPResponse(
                    status: 200,
                    headers: ["X-Docker-Container-Path-Stat": archiveStatHeader(archive.stat)]
                )
            case (.put, "archive"):
                guard let archivePath = target.first("path"), !archivePath.isEmpty else {
                    throw DevContainerError(.invalidRequest, message: "archive path is required")
                }
                try await runtime.copyArchiveToContainer(
                    id: id,
                    path: archivePath,
                    archive: request.body,
                    context: context
                )
                return .empty(status: 200)
            default:
                break
            }
        }

        if request.method == .delete, segments.count == 2, segments[0] == "containers" {
            try await runtime.removeContainer(
                id: segments[1],
                force: target.first("force").map(Self.boolValue) ?? false,
                context: context
            )
            await healthChecks.remove(id: segments[1])
            return .empty(status: 204)
        }

        if segments.count >= 3, segments[0] == "exec" {
            let id = ExecID(rawValue: segments[1])
            let action = segments[2]
            if request.method == .get, action == "json" {
                let exec = try await runtime.inspectExec(id: id, context: context)
                return try .json(execInspect(exec))
            }
            if request.method == .post, action == "start" {
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
            if request.method == .post, action == "resize" {
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
            }
        }

        if request.method == .get, path == "/images/json" {
            return try await .json(
                runtime.listImages(context: context).map(imageSummary)
            )
        }

        if request.method == .get,
           let reference = identifier(in: path, prefix: "/images/", suffix: "/json")
        {
            return try await .json(
                imageInspect(runtime.inspectImage(reference: reference, context: context))
            )
        }

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

        if request.method == .post, path == "/build" {
            let buildArguments = try stringDictionary(target.first("buildargs"), name: "buildargs")
            let labels = try stringDictionary(target.first("labels"), name: "labels")
            let stream = try await runtime.buildImage(
                request: ImageBuildRequest(
                    context: request.body,
                    dockerfile: target.first("dockerfile") ?? "Dockerfile",
                    tags: target.query["t"] ?? [],
                    buildArguments: buildArguments,
                    target: target.first("target"),
                    labels: labels
                ),
                context: context
            )
            return DockerHTTPResponse(
                status: 200,
                headers: ["Content-Type": "application/json"],
                body: .stream(dockerProgressStream(stream, status: nil))
            )
        }

        if request.method == .post,
           let reference = identifier(in: path, prefix: "/images/", suffix: "/tag")
        {
            guard let repository = target.first("repo"), !repository.isEmpty else {
                throw DevContainerError(.invalidRequest, message: "tag repository is required")
            }
            let destination = target.first("tag").map { "\(repository):\($0)" } ?? repository
            try await runtime.tagImage(source: reference, target: destination, context: context)
            return .empty(status: 201)
        }

        if request.method == .delete,
           let reference = identifier(in: path, prefix: "/images/", suffix: nil)
        {
            try await runtime.removeImage(
                reference: reference,
                force: target.first("force").map(Self.boolValue) ?? false,
                context: context
            )
            return try .json([DockerImageDeleteResponse(deleted: reference, untagged: nil)])
        }

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

        if segments.count >= 2, segments[0] == "networks" {
            let id = segments[1]
            if request.method == .get, segments.count == 2 {
                return try await .json(
                    networkInspect(runtime.inspectNetwork(id: id, context: context))
                )
            }
            if request.method == .post, segments.count == 3, segments[2] == "connect" {
                let decoded = try DockerJSON.decoder.decode(DockerNetworkConnectRequest.self, from: request.body)
                try await runtime.connectNetwork(
                    id: id,
                    containerID: decoded.container,
                    aliases: decoded.endpointConfig?.aliases ?? [],
                    context: context
                )
                return .empty(status: 200)
            }
            if request.method == .post, segments.count == 3, segments[2] == "disconnect" {
                let decoded = try DockerJSON.decoder.decode(DockerNetworkDisconnectRequest.self, from: request.body)
                try await runtime.disconnectNetwork(
                    id: id,
                    containerID: decoded.container,
                    force: decoded.force ?? false,
                    context: context
                )
                return .empty(status: 200)
            }
            if request.method == .delete, segments.count == 2 {
                try await runtime.removeNetwork(id: id, context: context)
                return .empty(status: 204)
            }
        }

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
            return try await .json(
                volumeInspect(
                    runtime.createVolume(
                        spec: VolumeSpec(
                            name: name,
                            labels: decoded.labels ?? [:],
                            driver: decoded.driver ?? "local"
                        ),
                        context: context
                    )
                ),
                status: 201
            )
        }

        if segments.count == 2, segments[0] == "volumes" {
            let name = segments[1]
            if request.method == .get {
                return try await .json(
                    volumeInspect(runtime.inspectVolume(name: name, context: context))
                )
            }
            if request.method == .delete {
                try await runtime.removeVolume(
                    name: name,
                    force: target.first("force").map(Self.boolValue) ?? false,
                    context: context
                )
                return .empty(status: 204)
            }
        }

        if request.method == .get, path == "/events" {
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

        throw DevContainerError(
            .notFound,
            message: "page not found"
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

    private func stripAPIVersion(_ path: String) -> String {
        let components = path.split(separator: "/", omittingEmptySubsequences: true)
        guard let first = components.first, first.hasPrefix("v"), first.dropFirst().contains(".") else {
            return path
        }
        return "/" + components.dropFirst().joined(separator: "/")
    }

    private func parseLabelFilters(_ value: String?) throws -> [String: String] {
        try labelFilters(parseFilters(value)["label"] ?? [])
    }

    private func parseFilters(_ value: String?) throws -> [String: [String]] {
        guard let value, !value.isEmpty else {
            return [:]
        }
        let object = try JSONSerialization.jsonObject(with: Data(value.utf8))
        guard let filters = object as? [String: Any] else {
            throw DevContainerError(.invalidRequest, message: "filters must be a JSON object")
        }
        return try filters.mapValues { rawValue in
            if let values = rawValue as? [String] {
                return values
            }
            if let values = rawValue as? [String: Any] {
                return values.compactMap { key, enabled in
                    if let enabled = enabled as? Bool {
                        return enabled ? key : nil
                    }
                    if let enabled = enabled as? NSNumber {
                        return enabled.boolValue ? key : nil
                    }
                    return nil
                }.sorted()
            }
            throw DevContainerError(.invalidRequest, message: "filter values must be arrays or maps")
        }
    }

    private func labelFilters(_ labels: [String]) throws -> [String: String] {
        var result: [String: String] = [:]
        for label in labels {
            let parts = label.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            let key = String(parts[0])
            let value = parts.count == 2 ? String(parts[1]) : ""
            if let existing = result[key], existing != value {
                throw DevContainerError(.invalidRequest, message: "conflicting label filter \(key)")
            }
            result[key] = value
        }
        return result
    }

    private func stringDictionary(_ value: String?, name: String) throws -> [String: String] {
        guard let value, !value.isEmpty else {
            return [:]
        }
        guard
            let object = try JSONSerialization.jsonObject(with: Data(value.utf8))
            as? [String: String]
        else {
            throw DevContainerError(.invalidRequest, message: "\(name) must be a string dictionary")
        }
        return object
    }

    private func dateQuery(_ value: String?, name: String) throws -> Date? {
        guard let value, !value.isEmpty else {
            return nil
        }
        if let seconds = Double(value) {
            return Date(timeIntervalSince1970: seconds)
        }
        if let date = ISO8601DateFormatter().date(from: value) {
            return date
        }
        throw DevContainerError(.invalidRequest, message: "\(name) is not a valid timestamp")
    }

    private func identifier(in path: String, prefix: String, suffix: String?) -> String? {
        guard path.hasPrefix(prefix) else {
            return nil
        }
        var value = String(path.dropFirst(prefix.count))
        if let suffix {
            guard value.hasSuffix(suffix) else {
                return nil
            }
            value.removeLast(suffix.count)
        } else if value.contains("/") {
            return nil
        }
        guard !value.isEmpty else {
            return nil
        }
        return value.removingPercentEncoding ?? value
    }

    private func unsigned16(_ value: String?, name: String) throws -> UInt16 {
        guard let value, let result = UInt16(value) else {
            throw DevContainerError(.invalidRequest, message: "\(name) must be a 16-bit unsigned integer")
        }
        return result
    }

    private func validateSecurityOptions(_ options: [String]) throws -> [String] {
        var result: [String] = []
        for option in options {
            let normalized = option.lowercased().replacingOccurrences(of: ":", with: "=")
            if normalized == "seccomp=unconfined" {
                // Apple containers do not install Docker's default seccomp
                // profile, so this Docker request is already the native state.
                continue
            }
            if normalized == "no-new-privileges=true" || normalized == "no-new-privileges=false"
                || normalized == "systempaths=unconfined"
            {
                result.append(option)
                continue
            }
            throw DevContainerError(
                .unsupportedCapability,
                message: "security option \(option) cannot be represented by Apple container"
            )
        }
        return result
    }

    private func archiveStatHeader(_ value: ArchivePathStat) throws -> String {
        let stat = DockerArchivePathStat(
            name: value.name,
            size: value.size,
            mode: Int64(value.mode),
            modificationTime: ISO8601DateFormatter().string(
                from: value.modificationTime
            ),
            linkTarget: value.linkTarget
        )
        return try DockerJSON.encoder.encode(stat).base64EncodedString()
    }

    private func waitForContainerRemoval(
        id: String,
        context: RuntimeRequestContext
    ) async throws {
        while !Task.isCancelled {
            do {
                _ = try await runtime.inspectContainer(id: id, context: context)
            } catch let error as DevContainerError where error.code == .notFound {
                return
            }
            try await Task.sleep(for: .milliseconds(100))
        }
        throw DevContainerError(.cancelled, message: "container removal wait was cancelled")
    }

    private func containerWaitStream(
        id: String,
        condition: String?,
        context: RuntimeRequestContext
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let exitCode = try await runtime.waitContainer(id: id, context: context)
                    if condition == "removed" {
                        try await waitForContainerRemoval(id: id, context: context)
                    }
                    try continuation.yield(
                        DockerJSON.encoder.encode(
                            DockerWaitResponse(statusCode: exitCode)
                        )
                    )
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func dockerProgressStream(
        _ stream: AsyncThrowingStream<Data, any Error>,
        status: String?
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await data in stream {
                        let text = String(data: data, encoding: .utf8)
                            ?? "non-UTF-8 progress output"
                        let object: [String: String] = status.map {
                            ["status": $0, "progress": text]
                        } ?? ["stream": text]
                        var encoded = try DockerJSON.encoder.encode(object)
                        encoded.append(0x0A)
                        continuation.yield(encoded)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func eventStream(
        _ stream: AsyncThrowingStream<RuntimeEvent, any Error>,
        acceptedActions: Set<String>
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await event in stream
                        where acceptedActions.isEmpty || acceptedActions.contains(event.action.rawValue)
                    {
                        let nanoseconds = Int64(event.timestamp.timeIntervalSince1970 * 1_000_000_000)
                        let message = DockerEventMessage(
                            status: event.action.rawValue,
                            id: event.resourceID,
                            type: event.resourceType,
                            action: event.action.rawValue,
                            actor: DockerEventActor(id: event.resourceID, attributes: event.attributes),
                            time: Int64(event.timestamp.timeIntervalSince1970),
                            timeNano: nanoseconds
                        )
                        var data = try DockerJSON.encoder.encode(message)
                        data.append(0x0A)
                        continuation.yield(data)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func containerSpec(
        from request: DockerCreateContainerRequest,
        requestedName: String
    ) throws -> ContainerSpec {
        try ContainerSpec(
            name: requestedName.isEmpty ? "devcontainer-\(UUID().uuidString.prefix(12).lowercased())" : requestedName,
            image: request.image,
            command: request.cmd ?? [],
            entrypoint: request.entrypoint?.values ?? [],
            environment: environmentDictionary(request.env ?? []),
            labels: request.labels ?? [:],
            workingDirectory: request.workingDir,
            user: request.user,
            hostname: request.hostname,
            mounts: containerMounts(request),
            ports: portBindings(request.hostConfig?.portBindings ?? [:]),
            networks: networkAttachments(request),
            terminal: request.tty ?? false,
            openStandardInput: request.openStdin ?? false,

            privileged: request.hostConfig?.privileged ?? false,
            initProcess: request.hostConfig?.initProcess ?? false,
            autoRemove: request.hostConfig?.autoRemove ?? false,
            capabilitiesToAdd: request.hostConfig?.capabilitiesToAdd ?? [],
            capabilitiesToDrop: request.hostConfig?.capabilitiesToDrop ?? [],
            securityOptions: validateSecurityOptions(request.hostConfig?.securityOptions ?? []),
            healthcheck: request.healthcheck.map {
                ContainerHealthcheck(
                    test: $0.test ?? [],
                    intervalNanoseconds: $0.interval ?? 30_000_000_000,
                    timeoutNanoseconds: $0.timeout ?? 30_000_000_000,
                    retries: $0.retries ?? 3,
                    startPeriodNanoseconds: $0.startPeriod ?? 0
                )
            }
        )
    }

    private func containerMounts(
        _ request: DockerCreateContainerRequest
    ) throws -> [RuntimeMount] {
        var mounts = try bindMounts(request.hostConfig?.binds ?? [])
        mounts += try structuredMounts(
            (request.hostConfig?.mounts ?? []) + (request.mounts ?? [])
        )
        let destinations = Set(mounts.map(\.destination))
        for destination in (request.volumes ?? [:]).keys.sorted()
            where !destinations.contains(destination)
        {
            mounts.append(
                RuntimeMount(
                    type: .volume,
                    source: Self.anonymousVolumeName(),
                    destination: destination,
                    anonymous: true
                )
            )
        }
        return mounts
    }

    private func bindMounts(_ values: [String]) throws -> [RuntimeMount] {
        try values.map { bind in
            let parts = bind.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
            guard parts.count >= 2 else {
                throw DevContainerError(.invalidRequest, message: "invalid bind mount \(bind)")
            }
            return RuntimeMount(
                type: Self.bindSourceIsHostPath(String(parts[0])) ? .bind : .volume,
                source: String(parts[0]),
                destination: String(parts[1]),
                readOnly: parts.count == 3 && parts[2].split(separator: ",").contains("ro")
            )
        }
    }

    private func structuredMounts(
        _ values: [DockerMountRequest]
    ) throws -> [RuntimeMount] {
        try values.map { mount in
            guard let type = RuntimeMountType(rawValue: mount.type) else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "unsupported mount type \(mount.type)"
                )
            }
            let anonymous = type == .volume && (mount.source?.isEmpty ?? true)
            return RuntimeMount(
                type: type,
                source: anonymous ? Self.anonymousVolumeName() : mount.source ?? "",
                destination: mount.target,
                readOnly: mount.readOnly ?? false,
                anonymous: anonymous
            )
        }
    }

    private func portBindings(
        _ values: [String: [DockerPortBindingRequest]]
    ) throws -> [PortBinding] {
        try values.flatMap { containerKey, hostBindings in
            let keyParts = containerKey.split(separator: "/", maxSplits: 1)
            guard let containerPort = UInt16(keyParts[0]) else {
                throw DevContainerError(.invalidRequest, message: "invalid port \(containerKey)")
            }
            let protocolName = keyParts.count == 2 ? String(keyParts[1]) : "tcp"
            if hostBindings.isEmpty {
                return [PortBinding(containerPort: containerPort, protocolName: protocolName)]
            }
            return hostBindings.map { binding in
                PortBinding(
                    containerPort: containerPort,
                    hostPort: binding.hostPort.flatMap(UInt16.init),
                    protocolName: protocolName,
                    hostAddress: binding.hostIP.flatMap { $0.isEmpty ? nil : $0 } ?? "127.0.0.1"
                )
            }
        }
    }

    private func networkAttachments(
        _ request: DockerCreateContainerRequest
    ) -> [NetworkAttachment] {
        var networks = (request.networkingConfig?.endpointsConfig ?? [:]).map {
            NetworkAttachment(
                name: $0.key,
                aliases: ($0.value.aliases ?? []).sorted()
            )
        }
        if networks.isEmpty,
           let mode = request.hostConfig?.networkMode,
           !mode.isEmpty,
           !["bridge", "default"].contains(mode)
        {
            networks.append(NetworkAttachment(name: mode))
        }
        return networks.sorted { $0.name < $1.name }
    }

    private func environmentDictionary(_ values: [String]) -> [String: String] {
        var result: [String: String] = [:]
        for value in values {
            let parts = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            result[String(parts[0])] = parts.count == 2 ? String(parts[1]) : ""
        }
        return result
    }

    private func containerSummary(_ snapshot: ContainerSnapshot) throws -> DockerContainerSummary {
        let labels = try RuntimeLabels.projectComposeLabels(snapshot.spec.labels)
        return DockerContainerSummary(
            id: snapshot.dockerID.rawValue,
            names: ["/\(snapshot.spec.name)"],
            image: snapshot.spec.image,
            imageID: snapshot.spec.image,
            command: (snapshot.spec.entrypoint + snapshot.spec.command).joined(separator: " "),
            created: Int64(snapshot.createdAt.timeIntervalSince1970),
            state: snapshot.state.rawValue,
            status: snapshot.state.rawValue,
            ports: snapshot.spec.ports.map {
                DockerPortSummary(
                    address: $0.hostAddress,
                    privatePort: $0.containerPort,
                    publicPort: $0.hostPort,
                    type: $0.protocolName
                )
            },
            labels: labels,
            mounts: snapshot.spec.mounts.map(mountSummary)
        )
    }

    private func containerInspect(
        _ snapshot: ContainerSnapshot,
        health: DockerContainerHealth?
    ) throws -> DockerContainerInspect {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let running = snapshot.state == .running
        let env = environmentList(snapshot.spec.environment)
        let volumeEntries = volumeEntries(snapshot.spec.mounts)
        let networkSettings = networkSettings(snapshot)
        let (executable, args) = containerCommand(snapshot.spec)
        return try DockerContainerInspect(
            id: snapshot.dockerID.rawValue,
            created: formatter.string(from: snapshot.createdAt),
            path: executable,
            args: args,
            name: "/\(snapshot.spec.name)",
            state: DockerContainerState(
                status: snapshot.state.rawValue,
                running: running,
                pid: running ? 1 : 0,
                exitCode: snapshot.exitCode ?? 0,
                startedAt: snapshot.startedAt.map(formatter.string) ?? "",
                finishedAt: snapshot.finishedAt.map(formatter.string) ?? "",
                health: health
            ),
            image: snapshot.spec.image,
            config: DockerContainerConfig(
                hostname: snapshot.spec.hostname ?? snapshot.spec.name,
                user: snapshot.spec.user ?? "",
                attachStdin: snapshot.spec.openStandardInput,
                attachStdout: true,
                attachStderr: true,
                tty: snapshot.spec.terminal,
                openStdin: snapshot.spec.openStandardInput,
                env: env,
                cmd: snapshot.spec.command,
                image: snapshot.spec.image,
                volumes: volumeEntries,
                workingDir: snapshot.spec.workingDirectory ?? "",
                entrypoint: snapshot.spec.entrypoint,
                labels: RuntimeLabels.projectComposeLabels(snapshot.spec.labels),
                healthcheck: dockerHealthcheck(snapshot.spec.healthcheck)
            ),
            hostConfig: DockerInspectHostConfig(
                binds: snapshot.spec.mounts.filter { $0.type == .bind }.map {
                    "\($0.source):\($0.destination)\($0.readOnly ? ":ro" : "")"
                }
            ),
            mounts: snapshot.spec.mounts.map(mountSummary),
            networkSettings: networkSettings
        )
    }

    private func dockerHealthcheck(
        _ value: ContainerHealthcheck?
    ) -> DockerHealthcheck? {
        value.map {
            DockerHealthcheck(
                test: $0.test,
                interval: $0.intervalNanoseconds,
                timeout: $0.timeoutNanoseconds,
                retries: $0.retries,
                startPeriod: $0.startPeriodNanoseconds
            )
        }
    }

    private func volumeEntries(
        _ mounts: [RuntimeMount]
    ) -> [String: [String: String]] {
        mounts.reduce(into: [:]) { $0[$1.destination] = [:] }
    }

    private func networkSettings(
        _ snapshot: ContainerSnapshot
    ) -> DockerNetworkSettings {
        let ports: [String: [DockerNetworkPortBinding]?] = Dictionary(
            grouping: snapshot.spec.ports,
            by: { "\($0.containerPort)/\($0.protocolName)" }
        ).mapValues { values -> [DockerNetworkPortBinding]? in
            values.compactMap { binding -> DockerNetworkPortBinding? in
                guard let hostPort = binding.hostPort else {
                    return nil
                }
                return DockerNetworkPortBinding(
                    hostIP: binding.hostAddress,
                    hostPort: String(hostPort)
                )
            }
        }
        let networks = Dictionary(
            uniqueKeysWithValues: snapshot.networkAddresses.map { name, value in
                let address = Self.networkAddress(value)
                let attachment = snapshot.spec.networks.first {
                    $0.name == name
                }
                let aliases = Set(
                    [snapshot.spec.name] + (attachment?.aliases ?? [])
                ).sorted()
                return (
                    name,
                    DockerEndpointSettings(
                        aliases: aliases,
                        networkID: "",
                        endpointID: "",
                        gateway: "",
                        ipAddress: address.address,
                        ipPrefixLen: address.prefixLength,
                        macAddress: ""
                    )
                )
            }
        )
        return DockerNetworkSettings(ports: ports, networks: networks)
    }

    private func containerCommand(_ spec: ContainerSpec) -> (String, [String]) {
        let executable = spec.entrypoint.first ?? spec.command.first ?? ""
        let args: [String] = if spec.entrypoint.isEmpty {
            Array(spec.command.dropFirst())
        } else {
            Array(spec.entrypoint.dropFirst()) + spec.command
        }
        return (executable, args)
    }

    private func environmentList(_ values: [String: String]) -> [String] {
        values.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
    }

    private func containerHealth(
        snapshot: ContainerSnapshot,
        context: RuntimeRequestContext
    ) async throws -> DockerContainerHealth? {
        guard
            snapshot.state == .running,
            let healthcheck = snapshot.spec.healthcheck,
            !healthcheck.test.isEmpty,
            healthcheck.test.first != "NONE"
        else {
            return nil
        }
        let identifier = snapshot.dockerID.rawValue
        let started = Date()
        let decision = await healthChecks.decision(
            id: identifier,
            startedAt: snapshot.startedAt,
            healthcheck: healthcheck,
            now: started
        )
        if case let .cached(value) = decision {
            return value
        }

        let exitCode = await executeHealthCheck(
            containerID: identifier,
            command: healthCommand(healthcheck.test),
            timeoutNanoseconds: healthcheck.timeoutNanoseconds,
            context: context
        )
        return await healthChecks.record(
            id: identifier,
            startedAt: snapshot.startedAt,
            healthcheck: healthcheck,
            observation: ContainerHealthObservation(
                exitCode: exitCode,
                started: started,
                ended: Date()
            )
        )
    }

    private func healthCommand(_ test: [String]) -> [String] {
        switch test.first {
        case "CMD":
            Array(test.dropFirst())
        case "CMD-SHELL":
            [
                "/bin/sh",
                "-c",
                test.dropFirst().joined(separator: " ")
            ]
        default:
            test
        }
    }

    private func executeHealthCheck(
        containerID: String,
        command: [String],
        timeoutNanoseconds: Int64,
        context: RuntimeRequestContext
    ) async -> Int32 {
        do {
            let exec = try await runtime.createExec(
                containerID: containerID,
                spec: ExecSpec(
                    command: command,
                    attachStandardInput: false,
                    attachStandardOutput: false,
                    attachStandardError: false
                ),
                context: context
            )
            let session = try await runtime.startExec(
                id: exec.id,
                context: context
            )
            try await session.closeStandardInput()
            return try await waitForHealthSession(
                session,
                timeoutNanoseconds: timeoutNanoseconds
            )
        } catch {
            return -1
        }
    }

    private func waitForHealthSession(
        _ session: any RuntimeProcessSession,
        timeoutNanoseconds: Int64
    ) async throws -> Int32 {
        guard timeoutNanoseconds > 0 else {
            return try await session.wait()
        }
        return try await withThrowingTaskGroup(of: Int32.self) { group in
            group.addTask {
                try await session.wait()
            }
            group.addTask {
                try await Task.sleep(
                    for: .nanoseconds(timeoutNanoseconds)
                )
                await session.cancel()
                return -1
            }
            guard let result = try await group.next() else {
                throw CancellationError()
            }
            group.cancelAll()
            return result
        }
    }

    private func mountSummary(_ mount: RuntimeMount) -> DockerMountSummary {
        DockerMountSummary(
            type: mount.type.rawValue,
            name: mount.type == .volume ? mount.source : "",
            source: mount.source,
            destination: mount.destination,
            driver: mount.type == .volume ? "local" : "",
            mode: mount.readOnly ? "ro" : "rw",
            readWrite: !mount.readOnly,
            propagation: ""
        )
    }

    static func networkAddress(
        _ value: String
    ) -> (address: String, prefixLength: Int) {
        let components = value.split(
            separator: "/",
            maxSplits: 1,
            omittingEmptySubsequences: false
        )
        guard
            components.count == 2,
            !components[0].isEmpty,
            let prefixLength = Int(components[1]),
            (0 ... (components[0].contains(":") ? 128 : 32)).contains(prefixLength)
        else {
            return (value, 0)
        }
        return (String(components[0]), prefixLength)
    }

    private func execInspect(_ snapshot: ExecSnapshot) -> DockerExecInspect {
        DockerExecInspect(
            id: snapshot.id.rawValue,
            running: snapshot.running,
            exitCode: snapshot.exitCode ?? 0,
            processConfig: DockerExecProcessConfig(
                tty: snapshot.spec.terminal,
                entrypoint: snapshot.spec.command.first ?? "",
                arguments: Array(snapshot.spec.command.dropFirst()),
                user: snapshot.spec.user ?? ""
            ),
            containerID: snapshot.containerID.rawValue
        )
    }

    private func imageSummary(_ image: ImageSnapshot) -> DockerImageSummary {
        DockerImageSummary(
            created: Int64(image.createdAt.timeIntervalSince1970),
            id: image.id,
            repoDigests: image.references.filter { $0.contains("@sha256:") },
            repoTags: image.references.filter { !$0.contains("@sha256:") },
            size: image.size,
            virtualSize: image.size
        )
    }

    private func imageInspect(_ image: ImageSnapshot) -> DockerImageInspect {
        DockerImageInspect(
            id: image.id,
            repoTags: image.references.filter { !$0.contains("@sha256:") },
            repoDigests: image.references.filter { $0.contains("@sha256:") },
            created: ISO8601DateFormatter().string(from: image.createdAt),
            size: image.size,
            virtualSize: image.size,
            architecture: image.architecture,
            variant: image.architecture == "arm64" ? "v8" : "",
            operatingSystem: image.operatingSystem,
            config: DockerImageConfig(
                user: image.user,
                environment: image.environment,
                entrypoint: image.entrypoint.isEmpty ? nil : image.entrypoint,
                command: image.command.isEmpty ? nil : image.command,
                labels: image.labels
            )
        )
    }

    private func networkInspect(_ network: NetworkSnapshot) -> DockerNetworkInspect {
        DockerNetworkInspect(
            name: network.spec.name,
            id: network.id,
            created: ISO8601DateFormatter().string(from: network.createdAt),
            driver: network.spec.driver,
            internalNetwork: network.spec.internalNetwork,
            containers: network.containers.reduce(into: [:]) { result, entry in
                result[entry.key.rawValue] = DockerNetworkContainer(
                    name: entry.key.rawValue,
                    ipv4Address: entry.value
                )
            },
            labels: network.spec.labels
        )
    }

    private func volumeInspect(_ volume: VolumeSnapshot) -> DockerVolumeInspect {
        DockerVolumeInspect(
            createdAt: ISO8601DateFormatter().string(from: volume.createdAt),
            driver: volume.spec.driver,
            labels: volume.spec.labels,
            mountpoint: volume.mountpoint,
            name: volume.name
        )
    }

    private static func boolValue(_ value: String) -> Bool {
        switch value.lowercased() {
        case "1", "true", "yes":
            true
        default:
            false
        }
    }

    private static func dockerIdentifier() -> String {
        let first = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let second = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return first + second
    }

    private static func anonymousVolumeName() -> String {
        "devcontainer-\(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())"
    }

    private static func bindSourceIsHostPath(_ source: String) -> Bool {
        source.hasPrefix("/")
    }
}

enum ContainerHealthDecision: Sendable {
    case check
    case cached(DockerContainerHealth)
}

struct ContainerHealthObservation: Sendable {
    let exitCode: Int32
    let started: Date
    let ended: Date
}

actor ContainerHealthRegistry {
    private struct Entry: Sendable {
        var startedAt: Date?
        var lastCheckedAt: Date?
        var status: String
        var failures: Int
        var logs: [DockerHealthLog]

        var value: DockerContainerHealth {
            DockerContainerHealth(
                status: status,
                failingStreak: failures,
                log: logs
            )
        }
    }

    private var entries: [String: Entry] = [:]

    func decision(
        id: String,
        startedAt: Date?,
        healthcheck: ContainerHealthcheck,
        now: Date
    ) -> ContainerHealthDecision {
        var entry = entries[id]
        if entry?.startedAt != startedAt {
            entry = Entry(
                startedAt: startedAt,
                status: "starting",
                failures: 0,
                logs: []
            )
        }
        guard var current = entry else {
            return .check
        }
        let interval = max(
            0.1,
            Double(healthcheck.intervalNanoseconds) / 1_000_000_000
        )
        if let lastCheckedAt = current.lastCheckedAt,
           now.timeIntervalSince(lastCheckedAt) < interval
        {
            entries[id] = current
            return .cached(current.value)
        }
        // Reserve this check interval so concurrent inspect calls do not
        // launch duplicate health processes in the same container.
        current.lastCheckedAt = now
        entries[id] = current
        return .check
    }

    func record(
        id: String,
        startedAt: Date?,
        healthcheck: ContainerHealthcheck,
        observation: ContainerHealthObservation
    ) -> DockerContainerHealth {
        var entry = entries[id]
        if entry?.startedAt != startedAt {
            entry = Entry(
                startedAt: startedAt,
                status: "starting",
                failures: 0,
                logs: []
            )
        }
        var current = entry ?? Entry(
            startedAt: startedAt,
            status: "starting",
            failures: 0,
            logs: []
        )
        let withinStartPeriod = startedAt.map {
            observation.started.timeIntervalSince($0) * 1_000_000_000
                < Double(healthcheck.startPeriodNanoseconds)
        } ?? false
        if observation.exitCode == 0 {
            current.status = "healthy"
            current.failures = 0
        } else if withinStartPeriod {
            current.status = "starting"
        } else {
            current.failures += 1
            current.status = current.failures >= max(1, healthcheck.retries)
                ? "unhealthy"
                : "starting"
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        current.logs.append(
            DockerHealthLog(
                start: formatter.string(from: observation.started),
                end: formatter.string(from: observation.ended),
                exitCode: observation.exitCode,
                output: ""
            )
        )
        current.logs = Array(current.logs.suffix(5))
        current.lastCheckedAt = observation.ended
        entries[id] = current
        return current.value
    }

    func reset(id: String) {
        entries[id] = nil
    }

    func remove(id: String) {
        entries[id] = nil
    }
}

actor ExecSessionRegistry {
    private struct Entry {
        let registration: UUID
        let session: any RuntimeProcessSession
    }

    private var entries: [ExecID: Entry] = [:]

    func register(
        _ session: any RuntimeProcessSession,
        id: ExecID
    ) -> UUID {
        let registration = UUID()
        entries[id] = Entry(registration: registration, session: session)
        return registration
    }

    func session(id: ExecID) -> (any RuntimeProcessSession)? {
        entries[id]?.session
    }

    func remove(id: ExecID, registration: UUID) {
        guard entries[id]?.registration == registration else {
            return
        }
        entries[id] = nil
    }
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
