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

// Endpoint projections stay together until TEST-005 completes the behavioural
// split by Docker API object family.
// swiftlint:disable file_length

extension DockerRouter {
    func labelsMatch(
        _ actualLabels: [String: String],
        expected expectedLabels: [String: String]
    ) -> Bool {
        expectedLabels.allSatisfy { key, expected in
            guard let actual = actualLabels[key] else {
                return false
            }
            return expected.isEmpty || actual == expected
        }
    }

    func container(
        _ snapshot: ContainerSnapshot,
        matches expectedLabels: [String: String]
    ) throws -> Bool {
        let actualLabels = try RuntimeLabels.projectComposeLabels(snapshot.spec.labels)
        return expectedLabels.allSatisfy { key, expected in
            guard let actual = actualLabels[key] else {
                return false
            }
            return expected.isEmpty || actual == expected
        }
    }

    func stripAPIVersion(_ path: String) -> String {
        let components = path.split(separator: "/", omittingEmptySubsequences: true)
        guard let first = components.first, first.hasPrefix("v"), first.dropFirst().contains(".") else {
            return path
        }
        return "/" + components.dropFirst().joined(separator: "/")
    }

    func parseLabelFilters(_ value: String?) throws -> [String: String] {
        try labelFilters(parseFilters(value)["label"] ?? [])
    }

    func parseFilters(_ value: String?) throws -> [String: [String]] {
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

    func labelFilters(_ labels: [String]) throws -> [String: String] {
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

    func stringDictionary(_ value: String?, name: String) throws -> [String: String] {
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

    func dateQuery(_ value: String?, name: String) throws -> Date? {
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

    func identifier(in path: String, prefix: String, suffix: String?) -> String? {
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

    func unsigned16(_ value: String?, name: String) throws -> UInt16 {
        guard let value, let result = UInt16(value) else {
            throw DevContainerError(.invalidRequest, message: "\(name) must be a 16-bit unsigned integer")
        }
        return result
    }

    func consoleSize(
        _ values: [UInt]?,
        name: String
    ) throws -> (width: UInt16, height: UInt16)? {
        guard let values else {
            return nil
        }
        guard values.count == 2,
              let height = UInt16(exactly: values[0]),
              let width = UInt16(exactly: values[1])
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "\(name) must contain 16-bit unsigned [height,width] values"
            )
        }
        return (width: width, height: height)
    }

    func validateSecurityOptions(_ options: [String]) throws -> [String] {
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

    // swiftlint:disable:next cyclomatic_complexity function_body_length
    func validateCreateContainerRequest(
        _ request: DockerCreateContainerRequest
    ) throws {
        if request.domainname?.isEmpty == false {
            try unsupportedCreateField("Domainname")
        }
        if request.argsEscaped == true {
            try unsupportedCreateField("ArgsEscaped")
        }
        if request.macAddress?.isEmpty == false {
            try unsupportedCreateField("MacAddress")
        }
        if request.networkDisabled == true {
            try unsupportedCreateField("NetworkDisabled")
        }
        if request.onBuild?.isEmpty == false {
            try unsupportedCreateField("OnBuild")
        }
        if request.shell?.isEmpty == false {
            try unsupportedCreateField("Shell")
        }
        if request.stdinOnce == true {
            try unsupportedCreateField("StdinOnce")
        }
        if let stopSignal = request.stopSignal,
           !stopSignal.isEmpty,
           !["SIGTERM", "TERM"].contains(stopSignal.uppercased())
        {
            try unsupportedCreateField("StopSignal")
        }
        if let stopTimeout = request.stopTimeout, stopTimeout != 0 {
            try unsupportedCreateField("StopTimeout")
        }
        for (index, mount) in (request.mounts ?? []).enumerated() {
            try validateAdvancedMountOptions(
                mount,
                prefix: "Mounts.[\(index)]"
            )
        }
        guard let host = request.hostConfig else {
            try validateEndpointConfigurations(request.networkingConfig)
            return
        }

        let numericFields: [(String, Int64?)] = [
            ("HostConfig.Memory", host.memory),
            ("HostConfig.MemorySwap", host.memorySwap),
            ("HostConfig.MemoryReservation", host.memoryReservation),
            ("HostConfig.NanoCpus", host.nanoCPUs),
            ("HostConfig.CpuShares", host.cpuShares),
            ("HostConfig.CpuCount", host.cpuCount),
            ("HostConfig.CpuPeriod", host.cpuPeriod),
            ("HostConfig.CpuPercent", host.cpuPercent),
            ("HostConfig.CpuQuota", host.cpuQuota),
            ("HostConfig.CpuRealtimePeriod", host.cpuRealtimePeriod),
            ("HostConfig.CpuRealtimeRuntime", host.cpuRealtimeRuntime),
            ("HostConfig.PidsLimit", host.pidsLimit),
            ("HostConfig.ShmSize", host.shmSize)
        ]
        if let field = numericFields.first(where: { ($0.1 ?? 0) != 0 })?.0 {
            try unsupportedCreateField(field)
        }
        if host.cpusetCPUs?.isEmpty == false {
            try unsupportedCreateField("HostConfig.CpusetCpus")
        }
        if host.cpusetMems?.isEmpty == false {
            try unsupportedCreateField("HostConfig.CpusetMems")
        }
        if host.deviceRequests?.isEmpty == false {
            try unsupportedCreateField("HostConfig.DeviceRequests")
        }
        if host.devices?.isEmpty == false {
            try unsupportedCreateField("HostConfig.Devices")
        }
        if host.blkioWeight != nil && host.blkioWeight != 0 {
            try unsupportedCreateField("HostConfig.BlkioWeight")
        }
        for (field, values) in [
            ("HostConfig.BlkioWeightDevice", host.blkioWeightDevice),
            ("HostConfig.BlkioDeviceReadBps", host.blkioDeviceReadBPS),
            ("HostConfig.BlkioDeviceWriteBps", host.blkioDeviceWriteBPS),
            ("HostConfig.BlkioDeviceReadIOps", host.blkioDeviceReadIOPS),
            ("HostConfig.BlkioDeviceWriteIOps", host.blkioDeviceWriteIOPS)
        ] where values?.isEmpty == false {
            try unsupportedCreateField(field)
        }
        for (field, values) in [
            ("HostConfig.Dns", host.dns),
            ("HostConfig.DnsOptions", host.dnsOptions),
            ("HostConfig.DnsSearch", host.dnsSearch),
            ("HostConfig.ExtraHosts", host.extraHosts),
            ("HostConfig.GroupAdd", host.groupAdd),
            ("HostConfig.Links", host.links),
            ("HostConfig.DeviceCgroupRules", host.deviceCgroupRules),
            ("HostConfig.MaskedPaths", host.maskedPaths),
            ("HostConfig.ReadonlyPaths", host.readOnlyPaths),
            ("HostConfig.VolumesFrom", host.volumesFrom)
        ] where values?.isEmpty == false {
            try unsupportedCreateField(field)
        }
        for (field, values) in [
            ("HostConfig.Annotations", host.annotations),
            ("HostConfig.StorageOpt", host.storageOptions),
            ("HostConfig.Tmpfs", host.tmpfs)
        ] where values?.isEmpty == false {
            try unsupportedCreateField(field)
        }
        for (field, value) in [
            ("HostConfig.Cgroup", host.cgroup),
            ("HostConfig.CgroupParent", host.cgroupParent),
            ("HostConfig.ContainerIDFile", host.containerIDFile),
            ("HostConfig.Isolation", host.isolation),
            ("HostConfig.Runtime", host.runtime),
            ("HostConfig.VolumeDriver", host.volumeDriver)
        ] where value?.isEmpty == false {
            try unsupportedCreateField(field)
        }
        let initialConsoleSize = try consoleSize(
            host.consoleSize,
            name: "HostConfig.ConsoleSize"
        )
        if request.tty == true,
           let initialConsoleSize,
           initialConsoleSize.width != 0 || initialConsoleSize.height != 0
        {
            try unsupportedCreateField("HostConfig.ConsoleSize")
        }
        if host.logConfig?.type?.isEmpty == false
            || host.logConfig?.config?.isEmpty == false
        {
            try unsupportedCreateField("HostConfig.LogConfig")
        }
        if let memorySwappiness = host.memorySwappiness, memorySwappiness != -1 {
            try unsupportedCreateField("HostConfig.MemorySwappiness")
        }
        if (host.ioMaximumBandwidth ?? 0) != 0 {
            try unsupportedCreateField("HostConfig.IOMaximumBandwidth")
        }
        if (host.ioMaximumIOPS ?? 0) != 0 {
            try unsupportedCreateField("HostConfig.IOMaximumIOps")
        }
        if host.publishAllPorts == true {
            try unsupportedCreateField("HostConfig.PublishAllPorts")
        }
        if host.sysctls?.isEmpty == false {
            try unsupportedCreateField("HostConfig.Sysctls")
        }
        if host.ulimits?.isEmpty == false {
            try unsupportedCreateField("HostConfig.Ulimits")
        }
        if host.readOnlyRootFilesystem == true {
            try unsupportedCreateField("HostConfig.ReadonlyRootfs")
        }
        if host.oomKillDisable == true {
            try unsupportedCreateField("HostConfig.OomKillDisable")
        }
        if let adjustment = host.oomScoreAdjustment, adjustment != 0 {
            try unsupportedCreateField("HostConfig.OomScoreAdj")
        }
        for (field, mode) in [
            ("HostConfig.IpcMode", host.ipcMode),
            ("HostConfig.PidMode", host.pidMode),
            ("HostConfig.UsernsMode", host.userNamespaceMode),
            ("HostConfig.UTSMode", host.utsMode),
            ("HostConfig.CgroupnsMode", host.cgroupNamespaceMode)
        ] where mode?.isEmpty == false {
            try unsupportedCreateField(field)
        }
        if let restart = host.restartPolicy,
           restart.name?.isEmpty == false && restart.name != "no"
           || (restart.maximumRetryCount ?? 0) != 0
        {
            try unsupportedCreateField("HostConfig.RestartPolicy")
        }
        for (index, mount) in (host.mounts ?? []).enumerated() {
            try validateAdvancedMountOptions(
                mount,
                prefix: "HostConfig.Mounts.[\(index)]"
            )
        }
        try validateEndpointConfigurations(request.networkingConfig)
    }

    func validateNetworkCreateRequest(
        _ request: DockerNetworkCreateRequest
    ) throws {
        if request.options?.isEmpty == false {
            try unsupportedCreateField("Options")
        }
        if let ipam = request.ipam,
           ipam.config?.isEmpty == false
           || ipam.options?.isEmpty == false
           || !(ipam.driver ?? "").isEmpty && ipam.driver != "default"
        {
            try unsupportedCreateField("IPAM")
        }
        if request.enableIPv4 == false {
            try unsupportedCreateField("EnableIPv4")
        }
        if request.enableIPv6 == true {
            try unsupportedCreateField("EnableIPv6")
        }
        if request.attachable == true {
            try unsupportedCreateField("Attachable")
        }
        if request.ingress == true {
            try unsupportedCreateField("Ingress")
        }
        if request.configOnly == true {
            try unsupportedCreateField("ConfigOnly")
        }
        if request.configFrom?.network?.isEmpty == false {
            try unsupportedCreateField("ConfigFrom")
        }
        if let scope = request.scope, !scope.isEmpty, scope != "local" {
            try unsupportedCreateField("Scope")
        }
    }

    func validateVolumeCreateRequest(
        _ request: DockerVolumeCreateRequest
    ) throws {
        if request.driverOptions?.isEmpty == false {
            try unsupportedCreateField("DriverOpts")
        }
        if request.clusterVolumeSpecification != nil {
            try unsupportedCreateField("ClusterVolumeSpec")
        }
    }

    func validateEndpointConfiguration(
        _ endpoint: DockerNetworkEndpointConfig?
    ) throws {
        guard let endpoint else {
            return
        }
        try validateEndpointFields(
            endpoint,
            prefix: "EndpointConfig"
        )
    }

    private func validateEndpointConfigurations(
        _ networking: DockerNetworkingConfig?
    ) throws {
        for (name, endpoint) in networking?.endpointsConfig ?? [:] {
            try validateEndpointFields(
                endpoint,
                prefix: "NetworkingConfig.EndpointsConfig.\(name)"
            )
        }
    }

    private func validateEndpointFields(
        _ endpoint: DockerNetworkEndpointConfig,
        prefix: String
    ) throws {
        if endpoint.links?.isEmpty == false {
            try unsupportedCreateField("\(prefix).Links")
        }
        if endpoint.ipamConfig?.ipv4Address?.isEmpty == false
            || endpoint.ipamConfig?.ipv6Address?.isEmpty == false
            || endpoint.ipamConfig?.linkLocalIPs?.isEmpty == false
        {
            try unsupportedCreateField("\(prefix).IPAMConfig")
        }
        if endpoint.macAddress?.isEmpty == false {
            try unsupportedCreateField("\(prefix).MacAddress")
        }
        if endpoint.driverOptions?.isEmpty == false {
            try unsupportedCreateField("\(prefix).DriverOpts")
        }
        if let gatewayPriority = endpoint.gatewayPriority, gatewayPriority != 0 {
            try unsupportedCreateField("\(prefix).GwPriority")
        }
        let unsupportedStrings: [(String, String?)] = [
            ("NetworkID", endpoint.networkID),
            ("EndpointID", endpoint.endpointID),
            ("Gateway", endpoint.gateway),
            ("IPAddress", endpoint.ipAddress),
            ("IPv6Gateway", endpoint.ipv6Gateway),
            ("GlobalIPv6Address", endpoint.globalIPv6Address)
        ]
        if let (field, _) = unsupportedStrings.first(where: { $0.1?.isEmpty == false }) {
            try unsupportedCreateField("\(prefix).\(field)")
        }
        if let prefixLength = endpoint.ipPrefixLength, prefixLength != 0 {
            try unsupportedCreateField("\(prefix).IPPrefixLen")
        }
        if let prefixLength = endpoint.globalIPv6PrefixLength, prefixLength != 0 {
            try unsupportedCreateField("\(prefix).GlobalIPv6PrefixLen")
        }
        if endpoint.dnsNames?.isEmpty == false {
            try unsupportedCreateField("\(prefix).DNSNames")
        }
    }

    private func validateAdvancedMountOptions(
        _ mount: DockerMountRequest,
        prefix: String
    ) throws {
        if mount.consistency?.isEmpty == false {
            try unsupportedCreateField("\(prefix).Consistency")
        }
        if let options = mount.bindOptions,
           options.propagation?.isEmpty == false
           || options.nonRecursive == true
           || options.createMountpoint == true
           || options.readOnlyNonRecursive == true
           || options.readOnlyForceRecursive == true
        {
            try unsupportedCreateField("\(prefix).BindOptions")
        }
        if let options = mount.volumeOptions,
           options.noCopy == true
           || options.labels?.isEmpty == false
           || options.subpath?.isEmpty == false
           || options.driverConfiguration != nil
        {
            try unsupportedCreateField("\(prefix).VolumeOptions")
        }
        if let options = mount.tmpfsOptions,
           (options.sizeBytes ?? 0) != 0 || options.mode != nil
        {
            try unsupportedCreateField("\(prefix).TmpfsOptions")
        }
    }

    private func unsupportedCreateField(_ field: String) throws -> Never {
        throw DevContainerError(
            .unsupportedCapability,
            message: "Docker request field \(field) is not supported by the selected Apple runtime"
        )
    }

    func archiveStatHeader(_ value: ArchivePathStat) throws -> String {
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

    func waitForContainerRemoval(
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

    func containerWaitStream(
        id: String,
        condition: String?,
        context: RuntimeRequestContext
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let snapshot = try await runtime.inspectContainer(
                        id: id,
                        context: context
                    )
                    let exitCode = try await runtime.waitContainer(id: id, context: context)
                    if condition == "removed" || snapshot.spec.autoRemove {
                        try await waitForContainerRemoval(id: id, context: context)
                    }
                    try await reconcileAutomaticRemoval(snapshot)
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

    func dockerProgressStream(
        _ stream: AsyncThrowingStream<Data, any Error>,
        status: String?
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await data in stream {
                        let text =
                            String(data: data, encoding: .utf8)
                                ?? "non-UTF-8 progress output"
                        let object: [String: String] =
                            if let status {
                                ["status": status, "progress": text]
                            } else {
                                ["stream": text]
                            }
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

    func eventStream(
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
                        let message = try DockerEventMessage(
                            status: event.action.rawValue,
                            id: event.resourceID,
                            type: event.resourceType,
                            action: event.action.rawValue,
                            actor: DockerEventActor(
                                id: event.resourceID,
                                attributes: RuntimeLabels.projectComposeLabels(
                                    event.attributes
                                )
                            ),
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

    func containerSpec(
        from request: DockerCreateContainerRequest,
        requestedName: String
    ) throws -> ContainerSpec {
        try ContainerSpec(
            name: requestedName.isEmpty
                ? "devcontainer-\(UUID().uuidString.prefix(12).lowercased())" : requestedName,
            image: request.image,
            command: request.cmd ?? [],
            entrypoint: request.entrypoint?.values ?? [],
            environment: environmentDictionary(request.env ?? []),
            labels: request.labels ?? [:],
            workingDirectory: request.workingDir,
            user: request.user,
            hostname: request.hostname,
            mounts: containerMounts(request),
            ports: portBindings(
                request.hostConfig?.portBindings ?? [:],
                exposedPorts: Set(request.exposedPorts?.keys.map(\.self) ?? [])
            ),
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

    func containerMounts(
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

    func bindMounts(_ values: [String]) throws -> [RuntimeMount] {
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

    func structuredMounts(
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

    func portBindings(
        _ values: [String: [DockerPortBindingRequest]],
        exposedPorts: Set<String> = []
    ) throws -> [PortBinding] {
        var result: [PortBinding] = []
        for containerKey in Set(values.keys).union(exposedPorts).sorted() {
            let keyParts = containerKey.split(separator: "/", maxSplits: 1)
            guard let containerPort = UInt16(keyParts[0]) else {
                throw DevContainerError(.invalidRequest, message: "invalid port \(containerKey)")
            }
            let protocolName = keyParts.count == 2 ? String(keyParts[1]) : "tcp"
            let hostBindings = values[containerKey] ?? []
            if hostBindings.isEmpty {
                result.append(
                    PortBinding(
                        containerPort: containerPort,
                        protocolName: protocolName,
                        published: false
                    )
                )
                continue
            }
            for binding in hostBindings {
                let hostAddress: String =
                    if let requested = binding.hostIP, !requested.isEmpty {
                        requested
                    } else {
                        "0.0.0.0"
                    }
                result.append(
                    PortBinding(
                        containerPort: containerPort,
                        hostPort: binding.hostPort.flatMap(UInt16.init),
                        protocolName: protocolName,
                        hostAddress: hostAddress,
                        published: true
                    )
                )
            }
        }
        return result
    }

    func networkAttachments(
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

    func environmentDictionary(_ values: [String]) -> [String: String] {
        var result: [String: String] = [:]
        for value in values {
            let parts = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            result[String(parts[0])] = parts.count == 2 ? String(parts[1]) : ""
        }
        return result
    }

    func containerSummary(_ snapshot: ContainerSnapshot) throws -> DockerContainerSummary {
        let labels = try RuntimeLabels.projectComposeLabels(snapshot.spec.labels)
        return DockerContainerSummary(
            id: snapshot.dockerID.rawValue,
            names: ["/\(snapshot.spec.name)"],
            image: snapshot.spec.image,
            imageID: snapshot.imageID ?? "",
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

    func containerInspect(
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
            image: snapshot.imageID ?? "",
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
                exposedPorts: exposedPorts(snapshot.spec.ports),
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

    func dockerHealthcheck(
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

    func volumeEntries(
        _ mounts: [RuntimeMount]
    ) -> [String: [String: String]] {
        mounts.reduce(into: [:]) { $0[$1.destination] = [:] }
    }

    func exposedPorts(
        _ ports: [PortBinding]
    ) -> [String: [String: String]] {
        Dictionary(
            uniqueKeysWithValues: Set(ports.map {
                "\($0.containerPort)/\($0.protocolName)"
            }).sorted().map { ($0, [:]) }
        )
    }

    func networkSettings(
        _ snapshot: ContainerSnapshot
    ) -> DockerNetworkSettings {
        let ports: [String: [DockerNetworkPortBinding]?] = Dictionary(
            grouping: snapshot.spec.ports,
            by: { "\($0.containerPort)/\($0.protocolName)" }
        ).mapValues { values -> [DockerNetworkPortBinding]? in
            let published = values.compactMap { binding -> DockerNetworkPortBinding? in
                guard let hostPort = binding.hostPort else {
                    return nil
                }
                return DockerNetworkPortBinding(
                    hostIP: binding.hostAddress,
                    hostPort: String(hostPort)
                )
            }
            return published.isEmpty ? nil : published
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

    func containerCommand(_ spec: ContainerSpec) -> (String, [String]) {
        let executable = spec.entrypoint.first ?? spec.command.first ?? ""
        let args: [String] =
            if spec.entrypoint.isEmpty {
                Array(spec.command.dropFirst())
            } else {
                Array(spec.entrypoint.dropFirst()) + spec.command
            }
        return (executable, args)
    }

    func environmentList(_ values: [String: String]) -> [String] {
        values.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
    }

    func containerHealth(
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

    func healthCommand(_ test: [String]) -> [String] {
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

    func executeHealthCheck(
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

    func waitForHealthSession(
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

    func mountSummary(_ mount: RuntimeMount) -> DockerMountSummary {
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

    func execInspect(_ snapshot: ExecSnapshot) -> DockerExecInspect {
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

    func imageSummary(_ image: ImageSnapshot) -> DockerImageSummary {
        DockerImageSummary(
            created: Int64(image.createdAt.timeIntervalSince1970),
            id: image.id,
            repoDigests: image.references.filter { $0.contains("@sha256:") },
            repoTags: image.references.filter { !$0.contains("@sha256:") },
            size: image.size,
            virtualSize: image.size
        )
    }

    func imageInspect(_ image: ImageSnapshot) -> DockerImageInspect {
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

    func networkInspect(_ network: NetworkSnapshot) -> DockerNetworkInspect {
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

    func volumeInspect(_ volume: VolumeSnapshot) -> DockerVolumeInspect {
        DockerVolumeInspect(
            createdAt: ISO8601DateFormatter().string(from: volume.createdAt),
            driver: volume.spec.driver,
            labels: volume.spec.labels,
            mountpoint: volume.mountpoint,
            name: volume.name
        )
    }

    static func boolValue(_ value: String) -> Bool {
        switch value.lowercased() {
        case "1", "true", "yes":
            true
        default:
            false
        }
    }

    static func dockerIdentifier() -> String {
        let first = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let second = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return first + second
    }

    static func anonymousVolumeName() -> String {
        "devcontainer-\(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())"
    }

    static func bindSourceIsHostPath(_ source: String) -> Bool {
        source.hasPrefix("/")
    }
}

struct DockerRoute {
    let request: DockerHTTPRequest
    let target: ParsedTarget
    let path: String
    let segments: [String]
    let context: RuntimeRequestContext
}

struct ParsedTarget {
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

extension AsyncThrowingStream where Element == RuntimeIOFrame, Failure == any Error {
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
