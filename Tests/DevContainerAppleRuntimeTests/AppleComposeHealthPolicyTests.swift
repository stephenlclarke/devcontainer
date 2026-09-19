// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Containerization
import ContainerizationOCI
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleComposeHealthPolicyTests {
    private let key = AppleContainerRuntime.composeHealthPolicyLabel
    private var policy: [String: Any] {
        [
            "version": 1,
            "test": ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/ready"],
            "intervalNanoseconds": 1_000_000_000,
            "timeoutNanoseconds": 1_000_000_000,
            "retries": 20,
            "startPeriodNanoseconds": 0
        ]
    }

    private func encode(_ value: [String: Any]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: value)
        return try #require(String(data: data, encoding: .utf8))
    }

    @Test
    func `native health policy survives JSON adoption and older metadata`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store)
        let labels = try [key: encode(policy)]
        let record = try await runtime.containerRecord([
            "id": "database", "configuration": [
                "creationDate": "2026-09-19T09:00:00Z", "labels": labels,
                "image": ["reference": "python:3.13-alpine"]
            ]
        ])
        let observed = await runtime.containerSnapshot(record)
        #expect(observed.spec.healthcheck?.retries == 20)
        #expect(observed.spec.healthcheck?.test == policy["test"] as? [String])
        _ = try await runtime.containerSnapshotWithMetadata(observed, metadata: nil, imageID: "sha256:actual")
        var metadata = try #require(await store.containerMetadata(id: "database"))
        metadata.spec.healthcheck = nil
        let restarted = try fixture.runtime(metadataStore: store)
        let adopted = try await restarted.containerSnapshotWithMetadata(
            observed,
            metadata: metadata,
            imageID: "sha256:actual"
        )
        #expect(adopted.spec.healthcheck == observed.spec.healthcheck)
        #expect(try AppleContainerRuntime.composeHealthPolicy(labels: [:]) == nil)
    }

    @Test
    func `typed native records adopt the same validated policy`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let image = ImageDescription(reference: "python:3.13-alpine", descriptor: .init(
            mediaType: "application/vnd.oci.image.manifest.v1+json",
            digest: "sha256:" + String(repeating: "a", count: 64), size: 1
        ))
        var configuration = ContainerConfiguration(
            id: "database", image: image, process: ProcessConfiguration(
                executable: "/bin/sh",
                arguments: [],
                environment: []
            )
        )
        configuration.labels = try [key: encode(policy)]
        let snapshot = ContainerResource.ContainerSnapshot(configuration: configuration, status: .running, networks: [])
        let record = try await runtime.containerRecord(snapshot)
        #expect(record.spec.healthcheck?.intervalNanoseconds == 1_000_000_000)
        configuration.labels[key] = "invalid"
        await #expect(throws: DevContainerError.self) {
            try await runtime.containerRecord(ContainerResource.ContainerSnapshot(
                configuration: configuration, status: .running, networks: []
            ))
        }
    }

    @Test(arguments: [
        "version",
        "test",
        "intervalNanoseconds",
        "timeoutNanoseconds",
        "retries",
        "startPeriodNanoseconds"
    ])
    func `missing policy fields fail closed`(field: String) throws {
        var value = policy
        value.removeValue(forKey: field)
        let encoded = try encode(value)
        #expect(throws: DevContainerError.self) { try AppleContainerRuntime.composeHealthPolicy(labels: [key: encoded])
        }
    }

    @Test
    func `malformed unsupported and unbounded policies fail closed`() throws {
        for (field, invalid) in [
            ("version", 2 as Any), ("version", true), ("test", ["CMD", "true"]),
            ("test", ["CMD-SHELL", ""]), ("test", ["CMD-SHELL", "a\0b"]),
            ("test", ["CMD-SHELL", String(repeating: "x", count: 4097)]),
            ("intervalNanoseconds", 0), ("timeoutNanoseconds", -1),
            ("startPeriodNanoseconds", -1), ("retries", 0), ("retries", UInt64(UInt32.max) + 1),
            ("unexpected", 1)
        ] {
            var value = policy
            value[field] = invalid
            let encoded = try encode(value)
            #expect(throws: DevContainerError.self) {
                try AppleContainerRuntime.composeHealthPolicy(labels: [key: encoded])
            }
        }
        for invalid in ["{", String(repeating: " ", count: 16385)] {
            #expect(throws: DevContainerError.self) {
                try AppleContainerRuntime.composeHealthPolicy(labels: [key: invalid])
            }
        }
        var disabled = policy
        disabled["test"] = ["NONE"]
        #expect(try AppleContainerRuntime.composeHealthPolicy(labels: [key: encode(disabled)])?.test == ["NONE"])
    }
}
