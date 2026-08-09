//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

import ContainerEngineProviderSession
import ContainerEngineRuntimeSPI
import DevContainerAppleRuntime
import DevContainerModel
import Foundation

extension DevContainerServiceCommand {
    // swiftlint:disable:next function_body_length
    static func loggingHandoffResponder(
        runtime: AppleContainerRuntime,
        providerFingerprint: ContainerEngineProviderFingerprint,
        stateRootUUID: UUID,
        stateDirectory: URL,
        providerVersion: String
    ) throws -> any ContainerEngineProviderHandoffControlResponder {
        let codeIdentity = try ProviderHandoffCodeIdentity.current()
        let now = Date().timeIntervalSince1970
        guard now.isFinite, now >= 0, now < Double(UInt64.max) else {
            throw DevContainerError(
                .stateCorruption,
                message: "provider handoff enrollment time is invalid"
            )
        }
        let accountSuffix = String(
            providerFingerprint.digest.dropFirst("sha256:".count)
        )
        let identity = try ProviderHandoffProviderKeyStore(
            service: "io.github.stephenlclarke.devcontainer.provider-handoff",
            account: "provider-\(accountSuffix)"
        ).loadOrCreate(
            context: ProviderHandoffProviderKeyEnrollmentContextV1(
                providerFingerprint: providerFingerprint.digest,
                stateRootUUID: stateRootUUID.uuidString.lowercased(),
                owningBundleIdentifier: codeIdentity.signingIdentifier,
                codeRequirementDigestSHA256:
                codeIdentity.designatedRequirementDigestSHA256,
                teamIdentifier: codeIdentity.teamIdentifier,
                providerRegistrationDigestSHA256: accountSuffix,
                enrolledAtUnixSeconds: UInt64(now.rounded(.down)),
                notBeforeUnixSeconds: UInt64(now.rounded(.down)),
                notAfterUnixSeconds: UInt64.max
            )
        )
        let handoffRoot = stateDirectory.appendingPathComponent(
            "provider-handoff",
            isDirectory: true
        )
        let objectStore = ProviderHandoffBundleObjectStore(
            root: handoffRoot.appendingPathComponent("objects", isDirectory: true)
        )
        let objectControl = ContainerEngineProviderHandoffControlService(
            objectStore: objectStore
        )
        let sourceControl = try ContainerEngineProviderSourceHandoffResponder(
            partKind: .logging,
            mediaType: ProviderHandoffPortableLoggingPayloadCodec.mediaType,
            requiredCapabilities: ["engine.handoff.part.logging.v1"],
            objectStore: objectStore,
            contributionStore: ProviderHandoffSourceContributionStore(
                root: handoffRoot.appendingPathComponent(
                    "source-contributions",
                    isDirectory: true
                )
            ),
            lineageKeyStore: ProviderHandoffLineageKeyStore(
                service:
                "io.github.stephenlclarke.devcontainer.provider-handoff",
                accountPrefix: "lineage-\(accountSuffix)"
            ),
            trustRegistryStore: ProviderHandoffTrustRegistryStore(
                account:
                "trust-registry-v1.\(stateRootUUID.uuidString.lowercased())"
            ),
            providerIdentity: identity,
            exportPackageSourceToDirectory: { request, temporaryDirectoryURL in
                let containers = try await runtime
                    .portableLoggingHandoffContainerSources(
                        resourceIDs: request.selectedResourceIDs,
                        providerVersion: providerVersion,
                        context: RuntimeRequestContext(
                            deadline: Date().addingTimeInterval(5 * 60),
                            providerFingerprint:
                            request.sourceProviderFingerprint
                        )
                    )
                return try await ProviderHandoffPortableLoggingPayloadCodec
                    .packageSource(
                        containers: containers,
                        sourceStateRootUUID: request.sourceStateRootUUID,
                        temporaryDirectoryURL: temporaryDirectoryURL
                    )
            },
            downstream: objectControl
        )
        return ContainerEngineProviderIdentityControlResponder(
            identity: identity,
            downstream: sourceControl
        )
    }
}
