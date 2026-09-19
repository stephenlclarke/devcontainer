// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import Containerization
import ContainerizationOCI
import ContainerPersistence
import ContainerResource
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

protocol AppleContainerCreateClient: Sendable {
    func prepare(
        spec: ContainerSpec, image: ResolvedAppleImage,
        context: RuntimeRequestContext
    ) async throws -> ContainerConfiguration
    func create(
        configuration: ContainerConfiguration, mountOptions: [String], context: RuntimeRequestContext,
        recordIntent: @Sendable (ContainerConfiguration) async throws -> RuntimeContainerCreation
    ) async throws -> RuntimeContainerCreation
}

struct LiveAppleContainerCreateClient: AppleContainerCreateClient {
    let client: ContainerClient
    var loadKernel: @Sendable () async throws -> Kernel = { try await ClientKernel.getDefaultKernel(for: .current) }

    func prepare(
        spec: ContainerSpec, image: ResolvedAppleImage,
        context: RuntimeRequestContext
    ) async throws -> ContainerConfiguration {
        try context.checkActive()
        let (description, platform) = try image.nativeIdentity()
        let nativeImage = ClientImage(description: description)
        let manifest = try await nativeImage.manifest(for: platform)
        guard manifest.config.digest == image.snapshot.id else {
            throw DevContainerError(.providerProtocolMismatch, message: "Selected image configuration changed")
        }
        let imageConfig = try await nativeImage.config(for: platform).config
        let health = try await ClientHealthCheck.ping(timeout: .seconds(10))
        let system = try await ConfigurationLoader.load(configurationFiles: [
            ConfigurationLoader.configurationFile(in: .init(health.appRoot.path()), of: .appRoot),
            ConfigurationLoader.configurationFile(in: .init(health.installRoot.path()), of: .installRoot)
        ])
        let networkClient = NetworkClient()
        let builtinNetwork = try await networkClient.builtin?.id
        var configuration = try AppleContainerCreateProjection.configuration(
            spec: spec, identity: (description, platform),
            imageConfig: imageConfig, system: system, builtinNetwork: builtinNetwork
        )
        for network in configuration.networks {
            _ = try await networkClient.get(id: network.network)
        }
        if AppleContainerRuntime.requiresHostDNS(spec) {
            let arguments = AppleContainerRuntime.hostBuildDNSArguments()
            configuration.dns = .init(
                nameservers: stride(from: 1, to: arguments.count, by: 2).map { arguments[$0] },
                domain: configuration.dns?.domain,
                searchDomains: configuration.dns?.searchDomains ?? [], options: configuration.dns?.options ?? []
            )
        }
        try context.checkActive()
        return configuration
    }

    func create(
        configuration: ContainerConfiguration, mountOptions: [String], context: RuntimeRequestContext,
        recordIntent: @Sendable (ContainerConfiguration) async throws -> RuntimeContainerCreation
    ) async throws -> RuntimeContainerCreation {
        var configuration = configuration
        let requestedMounts = try await Self.mounts(mountOptions)
        try Self.requireDisjointMounts(prepared: configuration.mounts, requested: requestedMounts)
        // Runtime-owned file mounts use the typed API: the CLI parser accepts
        // only directories. Preserve them in the journal and native submission.
        configuration.mounts += requestedMounts
        let kernel = try await loadKernel()
        try context.checkActive()
        // Finish fallible local preparation before recording possible submission.
        // Once journalled, a failed create/verification keeps its recovery record.
        let creation = try await recordIntent(configuration)
        // The API consumes this descriptor, not its mutable reference. Missing
        // local content fails; it must never turn into a pull of a replacement tag.
        try await client.create(configuration: configuration, kernel: kernel)
        let created = try await client.get(id: configuration.id)
        try AppleContainerCreateProjection.verify(created.configuration, expected: configuration)
        try context.checkActive()
        return creation
    }

    static func requireDisjointMounts(prepared: [Filesystem], requested: [Filesystem]) throws {
        for owned in prepared {
            let destination = URL(fileURLWithPath: owned.destination).standardizedFileURL.path
            for mount in requested {
                let requestedDestination = URL(fileURLWithPath: mount.destination).standardizedFileURL.path
                guard destination != requestedDestination,
                      !destination.hasPrefix(requestedDestination == "/" ? "/" : requestedDestination + "/"),
                      !requestedDestination.hasPrefix(destination == "/" ? "/" : destination + "/")
                else {
                    throw DevContainerError(
                        .invalidRequest, message: "Requested mount overlaps runtime-owned mount at \(destination)"
                    )
                }
            }
        }
    }

    /// Keep parsing testable without contacting the runtime volume service.
    static func mounts(
        _ options: [String],
        inspectVolume: @Sendable (String) async throws -> VolumeConfiguration = { try await ClientVolume.inspect($0) }
    ) async throws -> [Filesystem] {
        var filesystems: [Filesystem] = []
        for index in stride(from: 0, to: options.count, by: 2) {
            guard index + 1 < options.count else {
                throw DevContainerError(.invalidRequest, message: "Incomplete mount option")
            }
            if options[index] == "--tmpfs" {
                filesystems += try Parser.tmpfsMounts([options[index + 1]])
                continue
            }
            guard options[index] == "--mount" else {
                throw DevContainerError(.invalidRequest, message: "Unexpected mount option")
            }
            for mount in try Parser.mounts([options[index + 1]]) {
                switch mount {
                case let .filesystem(filesystem):
                    filesystems.append(filesystem)
                case let .volume(parsed):
                    let volume = try await inspectVolume(parsed.name)
                    filesystems.append(.volume(
                        name: parsed.name, format: volume.format, source: volume.source,
                        destination: parsed.destination, options: parsed.options
                    ))
                #if DEVCONTAINER_ENHANCED_RUNTIME
                    case .image:
                        throw DevContainerError(
                            .unsupportedCapability, message: "Image-backed mounts are not supported"
                        )
                #endif
                }
            }
        }
        return filesystems
    }
}

extension ResolvedAppleImage {
    func nativeIdentity() throws -> (ImageDescription, Platform) {
        guard let descriptor, let platform else {
            throw DevContainerError(.providerProtocolMismatch, message: "Selected image lacks native identity")
        }
        let decoder = JSONDecoder()
        let selected = try decoder.decode(Descriptor.self, from: descriptor)
        guard selected.digest == nativeDigest else {
            throw DevContainerError(.providerProtocolMismatch, message: "Selected image descriptor does not match")
        }
        return try (
            ImageDescription(reference: nativeReference, descriptor: selected),
            decoder.decode(Platform.self, from: platform)
        )
    }
}

enum AppleContainerCreateProjection {
    static func configuration(
        spec: ContainerSpec, identity: (image: ImageDescription, platform: Platform),
        imageConfig: ImageConfig?, system: ContainerSystemConfig, builtinNetwork: String?
    ) throws -> ContainerConfiguration {
        let (image, platform) = identity
        try spec.dns?.validate()
        var configuration = try ContainerConfiguration(
            id: spec.name, image: image, process: process(spec, image: imageConfig)
        )
        configuration.platform = platform
        configuration.rosetta = Platform.current.architecture == "arm64" && platform.architecture == "amd64"
        configuration.resources = try Parser.resources(
            cpus: nil, memory: nil,
            defaultCPUs: system.container.cpus, defaultMemory: system.container.memory
        )
        configuration.labels = spec.labels
        configuration.useInit = spec.initProcess
        configuration.stopSignal = imageConfig?.stopSignal
        let capabilities = try Parser.capabilities(capAdd: spec.capabilitiesToAdd, capDrop: spec.capabilitiesToDrop)
        configuration.capAdd = capabilities.capAdd
        configuration.capDrop = capabilities.capDrop
        configuration.networks = try networks(spec, builtin: builtinNetwork, domain: system.dns.domain)
        configuration.dns = .init(
            nameservers: spec.dns?.nameservers ?? [], domain: system.dns.domain,
            searchDomains: spec.dns?.searchDomains ?? [], options: spec.dns?.options ?? []
        )
        configuration.publishedPorts = try Parser.publishPorts(spec.ports.compactMap {
            AppleContainerRuntime.nativePublishArguments($0)?.last
        })
        guard configuration.publishedPorts.count <= 64, !configuration.publishedPorts.hasOverlaps() else {
            throw DevContainerError(.invalidRequest, message: "Too many or overlapping published ports")
        }
        #if DEVCONTAINER_ENHANCED_RUNTIME
            configuration.hostname = try Parser.hostname(spec.hostname)
            let security = try Parser.securityOptions(spec.securityOptions)
            configuration.initProcess.privileged = spec.privileged
            configuration.initProcess.noNewPrivileges = security.noNewPrivileges
            configuration.unconfinedSystemPaths = security.unconfinedSystemPaths
        #else
            guard spec.hostname?.isEmpty != false, !spec.privileged, spec.securityOptions.isEmpty else {
                throw DevContainerError(
                    .unsupportedCapability, message: "Stock runtime cannot enforce these security or hostname options"
                )
            }
        #endif
        return configuration
    }

    static func process(_ spec: ContainerSpec, image: ImageConfig?) throws -> ProcessConfiguration {
        let resolved = try spec.resolvingImageProcess(
            imageEntrypoint: image?.entrypoint ?? [], imageCommand: image?.cmd ?? []
        )
        let arguments = resolved.entrypoint + resolved.command
        let user = spec.user.flatMap { $0.isEmpty ? nil : $0 } ?? image?.user ?? ""
        let directory = spec.workingDirectory.flatMap { $0.isEmpty ? nil : $0 } ?? image?.workingDir ?? "/"
        return try ProcessConfiguration(
            executable: arguments[0], arguments: Array(arguments.dropFirst()),
            environment: Parser.allEnv(
                imageEnvs: image?.env ?? [], envFiles: [],
                envs: spec.environment.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }
            ),
            workingDirectory: directory.isEmpty ? "/" : directory,
            terminal: spec.terminal, user: user.isEmpty ? .id(uid: 0, gid: 0) : .raw(userString: user)
        )
    }

    static func networks(_ spec: ContainerSpec, builtin: String?, domain: String?) throws -> [AttachmentConfiguration] {
        var names = spec.networks.map(\.name)
        if names.contains(NetworkClient.noNetworkName) {
            guard names.count == 1 else {
                throw DevContainerError(
                    .invalidRequest, message: "The none network cannot be combined with other networks"
                )
            }
            return []
        }
        if names.isEmpty {
            guard let builtin else {
                throw DevContainerError(.runtimeUnavailable, message: "Builtin network is unavailable")
            }
            names = [builtin]
        }
        let firstHostname = spec.name.contains(".") ? "\(spec.name)." : domain.map { "\(spec.name).\($0)." } ?? spec
            .name
        return names.enumerated().map { index, name in
            AttachmentConfiguration(network: name, options: .init(
                hostname: index == 0 ? firstHostname : spec.name, mtu: 1280
            ))
        }
    }

    static func verify(_ actual: ContainerConfiguration, expected: ContainerConfiguration) throws {
        guard actual.id == expected.id, actual.image.descriptor == expected.image.descriptor,
              actual.platform == expected.platform
        else {
            throw DevContainerError(
                .providerProtocolMismatch, message: "Created container does not match selected image identity"
            )
        }
    }
}
