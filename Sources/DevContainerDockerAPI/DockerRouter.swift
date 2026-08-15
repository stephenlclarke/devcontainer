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

import CryptoKit
import Darwin
import DevContainerCore
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

// Route families are being separated under TEST-005; retain the current
// extension while its strict DTO and ownership changes settle.
// swiftlint:disable file_length

public struct DockerRouter: DockerHTTPResponder, Sendable {
    public let runtime: any DevContainerRuntime
    private let execSessions: ExecSessionRegistry
    private let mutationReplays: DockerMutationReplayRegistry
    let healthChecks: ContainerHealthRegistry
    private let coordinator: ProjectCoordinator?
    private let provider: BackendProvider
    private let providerFingerprint: String?
    private let requestTimeout: TimeInterval

    public init(
        runtime: any DevContainerRuntime,
        coordinator: ProjectCoordinator? = nil,
        provider: BackendProvider = .stock,
        providerFingerprint: String? = nil,
        requestTimeout: TimeInterval = 5 * 60
    ) {
        precondition(requestTimeout > 0)
        self.runtime = runtime
        self.coordinator = coordinator
        self.provider = provider
        self.providerFingerprint = providerFingerprint
        self.requestTimeout = requestTimeout
        execSessions = ExecSessionRegistry()
        mutationReplays = DockerMutationReplayRegistry()
        healthChecks = ContainerHealthRegistry()
    }

    public func respond(to request: DockerHTTPRequest) async -> DockerHTTPResponse {
        if let key = idempotencyKey(for: request),
           isReplayableMutation(request)
        {
            do {
                let requestHash = Self.digest(
                    Data("\(request.method.rawValue)\n\(request.target)\n".utf8)
                        + request.body
                )
                let task = try await mutationReplays.task(
                    key: Self.digest(Data(key.utf8)),
                    requestHash: requestHash
                ) {
                    await respondOnce(to: request)
                }
                return await task.value
            } catch let error as DevContainerError {
                return errorResponse(error)
            } catch {
                return errorResponse(
                    DevContainerError(
                        .runtimeUnavailable,
                        message: "idempotency coordination failed: \(error)"
                    )
                )
            }
        }
        return await respondOnce(to: request)
    }

    private func respondOnce(to request: DockerHTTPRequest) async -> DockerHTTPResponse {
        let context = requestContext(for: request)
        do {
            var response = try await RuntimeRequestScope.$context.withValue(context) {
                if let coordinator,
                   let mutation = try await mutation(for: request, context: context)
                {
                    return try await coordinatedResponse(
                        request: request,
                        mutation: mutation,
                        context: context,
                        coordinator: coordinator
                    )
                }
                return try await route(request, context: context)
            }
            response.headers["X-Request-ID"] = context.correlationID
            return response
        } catch let error as DevContainerError {
            var response = errorResponse(error)
            response.headers["X-Request-ID"] = context.correlationID
            return response
        } catch let error as DecodingError {
            var response = errorResponse(
                DevContainerError(.invalidRequest, message: "invalid Docker request: \(error)")
            )
            response.headers["X-Request-ID"] = context.correlationID
            return response
        } catch {
            var response = errorResponse(
                DevContainerError(.runtimeUnavailable, message: "runtime request failed: \(error)")
            )
            response.headers["X-Request-ID"] = context.correlationID
            return response
        }
    }

    private func coordinatedResponse(
        request: DockerHTTPRequest,
        mutation: ProjectMutation,
        context: RuntimeRequestContext,
        coordinator: ProjectCoordinator
    ) async throws -> DockerHTTPResponse {
        let session = try await coordinator.beginMutation(
            request: mutation,
            context: context
        )
        do {
            var response = try await RuntimeRequestScope.$context.withValue(
                session.context
            ) {
                try await route(request, context: session.context)
            }
            if case let .stream(stream) = response.body {
                response.body = .stream(
                    coordinatedMutationStream(
                        stream,
                        coordinator: coordinator,
                        session: session
                    )
                )
            } else {
                try await coordinator.commitMutation(session)
            }
            return response
        } catch {
            await coordinator.failMutation(
                session,
                errorCode: Self.mutationErrorCode(error)
            )
            throw error
        }
    }

    private static func mutationErrorCode(_ error: any Error) -> String? {
        if error is CancellationError {
            return DevContainerErrorCode.cancelled.rawValue
        }
        return (error as? DevContainerError)?.code.rawValue
    }
}

extension DockerRouter {
    private func route(
        _ request: DockerHTTPRequest,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse {
        try context.checkActive()
        let target = try ParsedTarget(request.target)
        let path = stripAPIVersion(target.path)
        let route = DockerRoute(
            request: request,
            target: target,
            path: path,
            segments: path.split(separator: "/", omittingEmptySubsequences: true).map(String.init),
            context: context
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

    private func requestContext(
        for request: DockerHTTPRequest
    ) -> RuntimeRequestContext {
        let requestedCorrelation = request.header("X-Request-ID")?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let correlation = requestedCorrelation.flatMap {
            $0.isEmpty || $0.utf8.count > 128 ? nil : $0
        } ?? UUID().uuidString.lowercased()
        let operationID = idempotencyKey(for: request).map {
            OperationID(rawValue: Self.digest(Data($0.utf8)))
        } ?? .random()
        return RuntimeRequestContext(
            operationID: operationID,
            correlationID: correlation,
            deadline: Date().addingTimeInterval(requestTimeout),
            providerFingerprint: providerFingerprint
        )
    }

    private func idempotencyKey(for request: DockerHTTPRequest) -> String? {
        guard let value = request.header("Idempotency-Key")
            ?? request.header("X-Idempotency-Key")
        else {
            return nil
        }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return nil
        }
        return String(trimmed.prefix(256))
    }

    private func isReplayableMutation(_ request: DockerHTTPRequest) -> Bool {
        guard request.method == .post
            || request.method == .put
            || request.method == .delete,
            let target = try? ParsedTarget(request.target)
        else {
            return false
        }
        let path = stripAPIVersion(target.path)
        if path == "/containers/create"
            || path == "/networks/create"
            || path == "/volumes/create"
        {
            return true
        }
        let segments = path.split(
            separator: "/",
            omittingEmptySubsequences: true
        ).map(String.init)
        if segments.first == "containers", segments.count >= 2 {
            return request.method == .delete
                || ["start", "stop", "restart", "kill", "rename", "exec", "archive"]
                .contains(segments.last ?? "")
        }
        if segments.first == "networks" || segments.first == "volumes" {
            return true
        }
        if segments.first == "images" {
            return request.method == .delete || segments.last == "tag"
        }
        return false
    }

    // swiftlint:disable:next function_body_length
    private func mutation(
        for request: DockerHTTPRequest,
        context: RuntimeRequestContext
    ) async throws -> ProjectMutation? {
        guard request.method == .post
            || request.method == .put
            || request.method == .delete
        else {
            return nil
        }
        let target = try ParsedTarget(request.target)
        let path = stripAPIVersion(target.path)
        let segments = path.split(
            separator: "/",
            omittingEmptySubsequences: true
        ).map(String.init)
        guard isJournalledMutation(
            method: request.method,
            path: path,
            segments: segments
        ) else {
            return nil
        }

        var labels = requestLabels(request.body)
        var resourceKey = path
        if segments.first == "containers",
           segments.count >= 2,
           segments[1] != "create"
        {
            let snapshot = try await runtime.inspectContainer(
                id: segments[1],
                context: context
            )
            labels.merge(snapshot.spec.labels) { current, _ in current }
            resourceKey = snapshot.runtimeID.rawValue
        } else if segments.first == "exec", segments.count >= 2 {
            let exec = try await runtime.inspectExec(
                id: ExecID(rawValue: segments[1]),
                context: context
            )
            let snapshot = try await runtime.inspectContainer(
                id: exec.containerID.rawValue,
                context: context
            )
            labels.merge(snapshot.spec.labels) { current, _ in current }
            resourceKey = snapshot.runtimeID.rawValue
        } else if segments.first == "networks",
                  segments.count >= 2,
                  segments[1] != "create"
        {
            if let container = requestContainerIdentifier(request.body) {
                let snapshot = try await runtime.inspectContainer(
                    id: container,
                    context: context
                )
                labels.merge(snapshot.spec.labels) { current, _ in current }
            } else {
                let network = try await runtime.inspectNetwork(
                    id: segments[1],
                    context: context
                )
                labels.merge(network.spec.labels) { current, _ in current }
                resourceKey = network.id
            }
        } else if segments.first == "volumes",
                  segments.count >= 2,
                  segments[1] != "create"
        {
            let volume = try await runtime.inspectVolume(
                name: segments[1],
                context: context
            )
            labels.merge(volume.spec.labels) { current, _ in current }
            resourceKey = volume.name
        }

        if let requestedProvider = labels[RuntimeLabels.provider],
           requestedProvider != provider.rawValue
        {
            throw DevContainerError(
                .conflict,
                message: "resource ownership selects provider \(requestedProvider), not \(provider.rawValue)"
            )
        }
        let requestHash = Self.digest(
            Data("\(request.method.rawValue)\n\(request.target)\n".utf8)
                + request.body
        )
        let configurationHash = Self.digest(request.body)
        let project = projectKey(labels: labels)
        let composeProject = composeProjectName(labels)
        return ProjectMutation(
            project: project,
            provider: provider,
            composeProject: composeProject,
            projectDirectory: labels[RuntimeLabels.devContainerLocalFolder],
            configurationHash: configurationHash,
            requestKind: "\(request.method.rawValue) \(path)",
            requestHash: requestHash,
            resourceKey: resourceKey,
            releaseProjectWhenEmpty: composeProject == nil
        )
    }

    private func isJournalledMutation(
        method: DockerHTTPMethod,
        path: String,
        segments: [String]
    ) -> Bool {
        if path == "/_ping" || path == "/auth" {
            return false
        }
        if method == .post,
           segments.first == "containers",
           ["wait", "attach"].contains(segments.last ?? "")
        {
            return false
        }
        if method == .post,
           segments.first == "exec",
           segments.last == "resize"
        {
            return false
        }
        return true
    }

    private func requestLabels(_ body: Data) -> [String: String] {
        guard !body.isEmpty,
              let object = try? JSONSerialization.jsonObject(with: body)
              as? [String: Any],
              let labels = object["Labels"] as? [String: String]
        else {
            return [:]
        }
        return labels
    }

    private func requestContainerIdentifier(_ body: Data) -> String? {
        guard !body.isEmpty,
              let object = try? JSONSerialization.jsonObject(with: body)
              as? [String: Any]
        else {
            return nil
        }
        return object["Container"] as? String
    }

    private func projectKey(labels: [String: String]) -> ProjectKey {
        if let explicit = labels[RuntimeLabels.project], !explicit.isEmpty {
            return ProjectKey(rawValue: explicit)
        }
        if let compose = composeProjectName(labels), !compose.isEmpty {
            return ProjectKey(rawValue: "\(getuid()):\(compose.lowercased())")
        }
        let identity = [
            labels[RuntimeLabels.devContainerLocalFolder],
            labels[RuntimeLabels.devContainerConfigFile]
        ].compactMap(\.self).joined(separator: "\n")
        if !identity.isEmpty {
            return ProjectKey(
                rawValue: "\(getuid()):devcontainer:\(Self.digest(Data(identity.utf8)).prefix(24))"
            )
        }
        return ProjectKey(rawValue: "\(getuid()):docker-api")
    }

    private func composeProjectName(_ labels: [String: String]) -> String? {
        labels["com.docker.compose.project"]
            ?? labels["com.apple.container.compose.project"]
    }

    func reconcileAutomaticRemoval(_ snapshot: ContainerSnapshot) async throws {
        guard snapshot.spec.autoRemove, let coordinator else {
            return
        }
        let labels = snapshot.spec.labels
        try await coordinator.reconcileRemovedResource(
            runtimeID: snapshot.runtimeID,
            project: projectKey(labels: labels),
            releaseProjectWhenEmpty: composeProjectName(labels) == nil
        )
    }

    private static func digest(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
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
            target: route.target,
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
            target: route.target,
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
                try container(snapshot, matches: labels)
            }
            return try .json(filtered.map(containerSummary))
        }

        if request.method == .post, path == "/containers/create" {
            let name = target.first("name") ?? ""
            let decoded = try DockerJSON.decode(
                DockerCreateContainerRequest.self,
                from: request.body,
                schema: .createContainer
            )
            try validateCreateContainerRequest(decoded)
            var spec = try containerSpec(from: decoded, requestedName: name)
            try applyOwnershipLabels(to: &spec, context: context)
            if spec.labels[RuntimeLabels.dockerID] == nil {
                spec.labels[RuntimeLabels.dockerID] = Self.dockerIdentifier()
            }
            let snapshot = try await runtime.createContainer(spec: spec, context: context)
            if let coordinator {
                try await coordinator.recordContainer(
                    snapshot,
                    provider: provider,
                    context: context,
                    specificationHash: context.configurationHash ?? "",
                    labelsHash: labelsHash(spec.labels)
                )
            }
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
            try await runtime.restartContainer(
                id: id,
                timeout: seconds.map(Duration.seconds),
                context: context
            )
            await healthChecks.reset(id: id)
            return .empty(status: 204)
        case (.post, "kill"):
            try await runtime.killContainer(
                id: id,
                signal: target.first("signal") ?? "SIGKILL",
                context: context
            )
            return .empty(status: 204)
        case (.post, "rename"):
            guard let name = target.first("name"), !name.isEmpty else {
                throw DevContainerError(.invalidRequest, message: "name is required")
            }
            try await runtime.renameContainer(id: id, name: name, context: context)
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
            let decoded = try DockerJSON.decode(
                DockerCreateExecRequest.self,
                from: request.body,
                schema: .createExec
            )
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
            return try await containerAttachResponse(
                id: id,
                context: context,
                webSocket: false
            )
        case (.get, "attach") where segments.count == 4 && segments[3] == "ws":
            return try await containerAttachResponse(
                id: id,
                context: context,
                webSocket: true
            )
        default:
            return nil
        }
    }

    private func containerAttachResponse(
        id: String,
        context: RuntimeRequestContext,
        webSocket: Bool
    ) async throws -> DockerHTTPResponse {
        let terminal = try await runtime.inspectContainer(id: id, context: context).spec.terminal
        let session = try await runtime.attachContainer(
            id: id,
            terminal: terminal,
            context: context
        )
        let adaptedSession = DockerRuntimeHijackSession(session)
        if webSocket {
            return DockerHTTPResponse(
                status: 101,
                headers: ["Connection": "Upgrade", "Upgrade": "websocket"],
                body: .webSocket(adaptedSession)
            )
        }
        return DockerHTTPResponse(
            status: 101,
            headers: [
                "Connection": "Upgrade",
                "Upgrade": "tcp",
                "Content-Type": "application/vnd.docker.raw-stream"
            ],
            body: .hijack(adaptedSession, terminal: terminal)
        )
    }

    private func execSpec(from request: DockerCreateExecRequest) throws -> ExecSpec {
        let initialConsoleSize = try consoleSize(
            request.consoleSize,
            name: "ConsoleSize"
        )
        return ExecSpec(
            command: request.cmd,
            environment: environmentDictionary(request.env ?? []),
            workingDirectory: request.workingDir,
            user: request.user,
            terminal: request.tty ?? false,
            terminalWidth: initialConsoleSize?.width,
            terminalHeight: initialConsoleSize?.height,
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
        let snapshot = try await runtime.inspectContainer(id: id, context: context)
        try await runtime.removeContainer(
            id: id,
            force: target.first("force").map(Self.boolValue) ?? false,
            context: context
        )
        try await coordinator?.removeResource(runtimeID: snapshot.runtimeID)
        await healthChecks.remove(id: id)
        return .empty(status: 204)
    }

    private func applyOwnershipLabels(
        to spec: inout ContainerSpec,
        context: RuntimeRequestContext
    ) throws {
        spec.labels = try applyingOwnershipLabels(
            to: spec.labels,
            context: context
        )
    }

    private func applyingOwnershipLabels(
        to labels: [String: String],
        context: RuntimeRequestContext
    ) throws -> [String: String] {
        guard let project = context.project,
              let generation = context.generation,
              let configurationHash = context.configurationHash
        else {
            return labels
        }
        var result = labels
        let ownership = RuntimeLabels.projectLabels(
            project: project,
            provider: provider,
            generation: generation,
            operation: context.operationID,
            configurationHash: configurationHash
        )
        for (key, value) in ownership {
            if let existing = result[key], existing != value {
                throw DevContainerError(
                    .conflict,
                    message: "request label \(key) conflicts with mutation ownership"
                )
            }
            result[key] = value
        }
        return result
    }

    private func labelsHash(_ labels: [String: String]) throws -> String {
        let data = try JSONSerialization.data(
            withJSONObject: labels,
            options: [.sortedKeys]
        )
        return Self.digest(data)
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
        let options = try DockerJSON.decode(
            DockerStartExecRequest.self,
            from: request.body,
            schema: .startExec
        )
        let startConsoleSize = try consoleSize(
            options.consoleSize,
            name: "ConsoleSize"
        )
        let exec = try await runtime.inspectExec(id: id, context: context)
        let session = try await runtime.startExec(id: id, context: context)
        if options.tty ?? exec.spec.terminal,
           let startConsoleSize,
           startConsoleSize.width > 0,
           startConsoleSize.height > 0
        {
            try await session.resize(
                width: startConsoleSize.width,
                height: startConsoleSize.height
            )
        }
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
            body: .hijack(
                DockerRuntimeHijackSession(session),
                terminal: options.tty ?? exec.spec.terminal
            )
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
            let reference: String = if let tag = target.first("tag"), !tag.isEmpty {
                tag.hasPrefix("sha256:")
                    ? "\(source)@\(tag)"
                    : "\(source):\(tag)"
            } else {
                source
            }
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

    // swiftlint:disable:next function_body_length
    private func networkCollectionResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if request.method == .get, path == "/networks" {
            let filters = try parseFilters(target.first("filters"))
            let labels = try labelFilters(filters["label"] ?? [])
            let networks = try await runtime.listNetworks(context: context).filter { network in
                labelsMatch(network.spec.labels, expected: labels)
                    && (filters["name"]?.contains(where: {
                        network.spec.name.contains($0)
                    }) ?? true)
                    && (filters["id"]?.contains(where: {
                        network.id.hasPrefix($0)
                    }) ?? true)
                    && (filters["driver"]?.contains(network.spec.driver) ?? true)
            }
            return try .json(
                networks.map(networkInspect)
            )
        }
        if request.method == .post, path == "/networks/create" {
            let decoded = try DockerJSON.decode(
                DockerNetworkCreateRequest.self,
                from: request.body,
                schema: .createNetwork
            )
            try validateNetworkCreateRequest(decoded)
            guard decoded.driver == nil || decoded.driver == "" || decoded.driver == "bridge" else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "network driver \(decoded.driver ?? "") is not supported"
                )
            }
            let labels = try applyingOwnershipLabels(
                to: decoded.labels ?? [:],
                context: context
            )
            let network = try await runtime.createNetwork(
                spec: NetworkSpec(
                    name: decoded.name,
                    labels: labels,
                    driver: decoded.driver.flatMap { $0.isEmpty ? nil : $0 } ?? "bridge",
                    internalNetwork: decoded.internalNetwork ?? false
                ),
                context: context
            )
            if let coordinator {
                try await coordinator.recordNetwork(
                    network,
                    provider: provider,
                    context: context,
                    specificationHash: context.configurationHash ?? "",
                    labelsHash: labelsHash(labels)
                )
            }
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
            let decoded = try DockerJSON.decode(
                DockerNetworkConnectRequest.self,
                from: request.body,
                schema: .connectNetwork
            )
            try validateEndpointConfiguration(decoded.endpointConfig)
            try await runtime.connectNetwork(
                id: id,
                containerID: decoded.container,
                aliases: decoded.endpointConfig?.aliases ?? [],
                context: context
            )
            return .empty(status: 200)
        case (.post, "disconnect"):
            let decoded = try DockerJSON.decode(
                DockerNetworkDisconnectRequest.self,
                from: request.body,
                schema: .disconnectNetwork
            )
            try await runtime.disconnectNetwork(
                id: id,
                containerID: decoded.container,
                force: decoded.force ?? false,
                context: context
            )
            return .empty(status: 200)
        case (.delete, ""):
            let network = try await runtime.inspectNetwork(id: id, context: context)
            try await runtime.removeNetwork(id: id, context: context)
            try await coordinator?.removeResource(
                runtimeID: RuntimeID(rawValue: network.id)
            )
            return .empty(status: 204)
        default:
            return nil
        }
    }

    // swiftlint:disable:next function_body_length
    private func volumeCollectionResponse(
        request: DockerHTTPRequest,
        target: ParsedTarget,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> DockerHTTPResponse? {
        if request.method == .get, path == "/volumes" {
            let filters = try parseFilters(target.first("filters"))
            let labels = try labelFilters(filters["label"] ?? [])
            let volumes = try await runtime.listVolumes(context: context).filter { volume in
                labelsMatch(volume.spec.labels, expected: labels)
                    && (filters["name"]?.contains(where: {
                        volume.name.contains($0)
                    }) ?? true)
                    && (filters["driver"]?.contains(volume.spec.driver) ?? true)
            }
            return try .json(
                DockerVolumeListResponse(
                    volumes: volumes.map(volumeInspect),
                    warnings: []
                )
            )
        }
        if request.method == .post, path == "/volumes/create" {
            let decoded = try DockerJSON.decode(
                DockerVolumeCreateRequest.self,
                from: request.body,
                schema: .createVolume
            )
            try validateVolumeCreateRequest(decoded)
            let name =
                decoded.name.flatMap { $0.isEmpty ? nil : $0 }
                    ?? "devcontainer-\(UUID().uuidString.prefix(12).lowercased())"
            guard decoded.driver == nil || decoded.driver == "local" else {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "volume driver \(decoded.driver ?? "") is not supported"
                )
            }
            let labels = try applyingOwnershipLabels(
                to: decoded.labels ?? [:],
                context: context
            )
            let volume = try await runtime.createVolume(
                spec: VolumeSpec(
                    name: name,
                    labels: labels,
                    driver: decoded.driver ?? "local"
                ),
                context: context
            )
            if let coordinator {
                try await coordinator.recordVolume(
                    volume,
                    provider: provider,
                    context: context,
                    specificationHash: context.configurationHash ?? "",
                    labelsHash: labelsHash(labels)
                )
            }
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
            let volume = try await runtime.inspectVolume(name: name, context: context)
            try await runtime.removeVolume(
                name: name,
                force: target.first("force").map(Self.boolValue) ?? false,
                context: context
            )
            try await coordinator?.removeResource(
                runtimeID: RuntimeID(rawValue: volume.name)
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
        let status =
            switch error.code {
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
