// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleComposeImageReferenceTests {
    private let digest = "sha256:" + String(repeating: "a", count: 64)
    private let key = AppleContainerRuntime.composeImageReferenceLabel

    @Test
    func `digest spelling is verified before adoption and survives older metadata`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store)
        let original = "alpine:3.22@" + digest
        let record = try await runtime.containerRecord([
            "id": "app", "configuration": [
                "creationDate": "2026-09-19T09:00:00Z",
                "labels": [key: original],
                "image": ["reference": "docker.io/library/alpine@" + digest, "descriptor": ["digest": digest]]
            ]
        ])
        let observed = await runtime.containerSnapshot(record)
        #expect(observed.spec.image == original)
        let adopted = try await runtime.containerSnapshotWithMetadata(observed, metadata: nil, imageID: "sha256:actual")
        #expect(adopted.imageID == "sha256:actual")
        var metadata = try #require(await store.containerMetadata(id: "app"))
        #expect(metadata.spec.image == original)
        metadata.spec.image = "docker.io/library/alpine@" + digest
        let restarted = try fixture.runtime(metadataStore: store)
        let projected = try await restarted.containerSnapshotWithMetadata(
            observed, metadata: metadata, imageID: "sha256:actual"
        )
        #expect(projected.spec.image == original)
        #expect(projected.dockerID == adopted.dockerID)
    }

    @Test(arguments: [
        "alpine:3.22",
        "other@",
        "alpine@@",
        "alpine@sha256:short",
        "alpine@sha256:" + String(repeating: "b", count: 64)
    ])
    func `unproved image aliases are rejected`(original: String) throws {
        let value = original.hasSuffix("@") ? original + digest : original
        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime.composeImageReference(
                observed: "docker.io/library/alpine@" + digest,
                descriptorDigest: digest, labels: [key: value]
            )
        }
    }

    @Test
    func `missing native descriptor cannot be replaced by reference text`() throws {
        let original = "alpine:3.22@" + digest
        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime.composeImageReference(
                observed: original,
                descriptorDigest: nil,
                labels: [key: original]
            )
        }
        #expect(try AppleContainerRuntime.composeImageReference(
            observed: "alpine:3.22", descriptorDigest: nil, labels: [:]
        ) == "alpine:3.22")
    }
}
