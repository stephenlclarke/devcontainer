// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineGateway
import ContainerEngineRuntimeSPI
import DevContainerModel
import DevContainerState

extension DevContainerServiceCommand {
    /// Describe the selected implementation before persistent identity enrollment.
    static func providerDeclaration(
        runtime: ProtocolDescriptor,
        resourceOwner: BackendProvider
    ) throws -> ContainerEngineProviderDeclaration {
        let routeCapabilities = try stockRouteIdentifiers.map { identifier in
            try ContainerEngineProviderCapability(
                identifier: "engine.route.\(identifier)",
                status: identifier == "ContainerAttachWebsocket" ? .emulated : .native
            )
        }
        var handoffIdentifiers = [
            "engine.handoff.part.identity-lifecycle-events.v1",
            "engine.handoff.provider-key-enrollment.v1"
        ]
        #if DEVCONTAINER_ENHANCED_RUNTIME
            handoffIdentifiers.append("engine.handoff.part.logging.v1")
            let profile = ContainerEngineProviderProfile.enhanced
        #else
            let profile = ContainerEngineProviderProfile.stock
        #endif
        let handoffCapabilities = try handoffIdentifiers.map {
            try ContainerEngineProviderCapability(identifier: $0, status: .native)
        }
        // The service always constructs the direct Apple runtime, whose barrier
        // and native quiescence probe own this control protocol in both profiles.
        let recoveryCapability = try ContainerEngineProviderCapability(
            identifier: ContainerEngineGatewayResponder.recoveryCapabilityIdentifier,
            version: ContainerEngineGatewayResponder.recoveryCapabilityVersion,
            status: .native
        )
        return try ContainerEngineProviderDeclaration(
            profile: profile,
            kind: .devcontainerStock,
            implementationVersion: BuildInfo.current.version,
            runtimeRevisions: [
                "apple-container": runtime.providerVersion,
                "apple-container-commit": runtime.providerCommit,
                "devcontainer": BuildInfo.current.commit,
                "resource-owner": resourceOwner.rawValue
            ],
            stateSchemaVersion: UInt64(SQLiteStateStore.schemaVersion),
            capabilities: runtime.capabilities.map { capability, status in
                let sharedStatus: ContainerEngineCapabilityStatus = switch status {
                case .native: .native
                case .emulated: .emulated
                case .unsupported: .unavailable
                }
                return try ContainerEngineProviderCapability(
                    identifier: "engine.\(capability.rawValue)", status: sharedStatus
                )
            } + routeCapabilities + handoffCapabilities + [recoveryCapability]
        )
    }

    private static let stockRouteIdentifiers = [
        "SystemPing", "SystemPingHead", "SystemVersion", "SystemInfo",
        "ContainerList", "ContainerCreate", "ContainerInspect", "ContainerStart",
        "ContainerStop", "ContainerRestart", "ContainerKill", "ContainerRename",
        "ContainerWait", "ContainerExec", "ContainerLogs", "ContainerAttach",
        "ContainerAttachWebsocket", "ContainerResize",
        "ContainerArchive", "ContainerArchiveInfo", "PutContainerArchive",
        "ContainerDelete", "ExecInspect", "ExecStart", "ExecResize", "ImageList",
        "ImageInspect", "ImageCreate", "ImageLoad", "ImageBuild", "ImageTag",
        "ImageDelete", "NetworkList", "NetworkCreate", "NetworkInspect",
        "NetworkConnect", "NetworkDisconnect", "NetworkDelete", "VolumeList",
        "VolumeCreate", "VolumeInspect", "VolumeDelete", "SystemEvents"
    ]
}
