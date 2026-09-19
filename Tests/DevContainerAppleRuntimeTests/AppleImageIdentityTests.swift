// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationError
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct FakeAppleImageIdentityClient: AppleImageIdentityClient {
    static let digest = "sha256:" + String(repeating: "b", count: 64)
    var result = Self.digest
    var layers = ["sha256:" + String(repeating: "d", count: 64)]
    var entrypoint: [String] = []
    var command: [String] = []

    func deleteNamedReference(_: String) async throws {
        throw DevContainerError(.unsupportedCapability, message: "This identity-only fixture cannot delete images")
    }

    func configurationIdentity(
        reference: String,
        descriptor: Data,
        platform: Data
    ) async throws -> AppleImageConfigurationIdentity {
        #expect(["fixture:latest", "fixture:alias"].contains(reference))
        let description = try #require(JSONSerialization.jsonObject(with: descriptor) as? [String: Any])
        #expect(description["digest"] as? String == "sha256:" + String(repeating: "a", count: 64))
        let selected = try #require(JSONSerialization.jsonObject(with: platform) as? [String: String])
        #expect(selected == ["architecture": "arm64", "os": "linux"])
        return .init(digest: result, rootFSLayers: layers, entrypoint: entrypoint, command: command)
    }
}

private actor NamedImageDeletionClient: AppleImageIdentityClient {
    var references: [String] = []
    let failure: Bool

    init(failure: Bool = false) {
        self.failure = failure
    }

    func configurationIdentity(
        reference: String,
        descriptor: Data,
        platform: Data
    ) async throws -> AppleImageConfigurationIdentity {
        try await FakeAppleImageIdentityClient().configurationIdentity(
            reference: reference, descriptor: descriptor, platform: platform
        )
    }

    func deleteNamedReference(_ reference: String) throws {
        if failure {
            throw ContainerizationError(.internalError, message: "Injected image deletion failure")
        }
        references.append(reference)
    }
}

extension AppleContainerRuntimeTests {
    @Test func `native image layers come from descriptor bound config not unverified CLI fields`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let layers = ["sha256:" + String(repeating: "e", count: 64), "sha256:" + String(repeating: "f", count: 64)]
        let runtime = try fixture.runtime(images: FakeAppleImageIdentityClient(layers: layers))
        #expect(try await runtime.inspectImage(reference: "fixture:latest", context: .init()).rootFSLayers == layers)
        let invalid = try fixture.runtime(images: FakeAppleImageIdentityClient(layers: ["sha256:invalid"]))
        await #expect(throws: DevContainerError.self) {
            try await invalid.inspectImage(reference: "fixture:latest", context: .init())
        }
        let empty = try fixture.runtime(images: FakeAppleImageIdentityClient(layers: []))
        #expect(try await empty.inspectImage(reference: "fixture:latest", context: .init()).rootFSLayers == [])
    }

    @Test func `named image deletion uses the native reference without CLI garbage collection`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let client = NamedImageDeletionClient()
        let runtime = try fixture.runtime(
            images: client, creator: LiveAppleContainerCreateClient(client: ContainerClient())
        )
        try await runtime.removeImage(
            reference: "docker.io/library/fixture:latest", force: false, context: RuntimeRequestContext()
        )
        #expect(await client.references == ["fixture:latest"])
        #expect(try !(fixture.log()).contains("image delete"))
        await #expect(throws: DevContainerError.self) {
            try await runtime.removeImage(reference: FakeAppleImageIdentityClient.digest, force: true, context: .init())
        }
        #expect(await client.references.count == 1)
    }

    @Test func `native image deletion preserves missing protected and failed request boundaries`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let client = NamedImageDeletionClient(failure: true)
        let runtime = try fixture.runtime(
            images: client, creator: LiveAppleContainerCreateClient(client: ContainerClient())
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.removeImage(reference: "fixture:latest", force: true, context: .init())
        }
        await #expect(throws: DevContainerError.self) {
            try await runtime.removeImage(
                reference: "fixture:latest", force: false, context: .init(deadline: .distantPast)
            )
        }
        try fixture.setImageInventory([])
        await #expect(throws: DevContainerError.self) {
            try await runtime.removeImage(reference: "protected:latest", force: false, context: .init())
        }
        try await runtime.removeImage(reference: "missing:latest", force: true, context: .init())
        #expect(await client.references.isEmpty)
        #expect(try !(fixture.log()).contains("image delete"))
    }

    @Test func `image config digest lookup never substitutes mutable mutation aliases`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()
        let id = FakeAppleImageIdentityClient.digest
        #expect(try await runtime.inspectImage(reference: id, context: context).id == id)
        #expect(try await runtime.inspectImage(
            reference: "sha256:" + String(repeating: "a", count: 64), context: context
        ).id == id)
        await #expect(throws: DevContainerError.self) {
            try await runtime.tagImage(source: id, target: "another:latest", context: context)
        }
        await #expect(throws: DevContainerError.self) {
            try await runtime.removeImage(reference: id, force: true, context: context)
        }
        let log = try fixture.log()
        #expect(!log.contains("image tag"))
        #expect(!log.contains("image delete"))
    }

    @Test func `image config digest creation does not silently retarget mutable tags`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        let id = FakeAppleImageIdentityClient.digest
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(
                spec: ContainerSpec(name: "fixture", image: id), context: RuntimeRequestContext()
            )
        }
        #expect(!FileManager.default.fileExists(atPath: fixture.logURL.path))
    }

    @Test func `image identity does not match unrelated repository`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        await #expect(throws: DevContainerError.self) {
            try await runtime.inspectImage(
                reference: "unrelated:latest@sha256:" + String(repeating: "a", count: 64),
                context: RuntimeRequestContext()
            )
        }
    }

    @Test(arguments: ["", "sha256:abc123", "sha256:" + String(repeating: "G", count: 64)])
    func `image identity rejects invalid config digest`(digest: String) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime(images: FakeAppleImageIdentityClient(result: digest))
        await #expect(throws: DevContainerError.self) {
            try await runtime.listImages(context: RuntimeRequestContext())
        }
    }

    @Test func `image identity rejects empty repository without crashing`() {
        let native = "sha256:" + String(repeating: "a", count: 64)
        let image = ResolvedAppleImage(
            snapshot: ImageSnapshot(
                id: FakeAppleImageIdentityClient.digest,
                references: ["fixture:latest"],
                createdAt: Date(timeIntervalSince1970: 0),
                size: 1
            ),
            nativeReference: "fixture:latest", nativeDigest: native
        )
        #expect(!image.matches("@" + native))
        #expect(image.matches(native))
        #expect(image.matches("fixture@" + native))
        #expect(image.matches("fixture:other-tag@" + native))
        #expect(image.matches("docker.io/library/fixture@" + native))
        #expect(!image.matches("other@" + native))
        #expect(!image.matches("fixture@sha256:" + String(repeating: "c", count: 64)))
        #expect(AppleContainerRuntime.imageRepository("registry.test:5000/path/image:tag")
            == AppleContainerRuntime.imageRepository("registry.test:5000/path/image@" + native))
    }

    @Test func `image inventory groups tags sharing the same config`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        try fixture.setImageInventory([imageRecord("fixture:latest"), imageRecord("fixture:alias")])
        let runtime = try fixture.runtime()
        let images = try await runtime.listImages(context: RuntimeRequestContext())
        #expect(images.count == 1)
        #expect(images.first?.references == [
            "fixture:alias", "fixture:latest", "fixture@sha256:" + String(repeating: "c", count: 64)
        ])
        #expect(images.first?.id == FakeAppleImageIdentityClient.digest)
        #expect(try await runtime.inspectImage(
            reference: "fixture:alias", context: RuntimeRequestContext()
        ).references == images.first?.references)
        #expect(try await runtime.inspectImage(
            reference: "fixture@sha256:" + String(repeating: "c", count: 64), context: RuntimeRequestContext()
        ).id == images.first?.id)
    }

    @Test func `image inventory rejects missing metadata and stale contexts`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        try fixture.setImageInventory([[:]])
        await #expect(throws: DevContainerError.self) {
            try await runtime.listImages(context: RuntimeRequestContext())
        }
        await #expect(throws: DevContainerError.self) {
            try await runtime.listImages(context: RuntimeRequestContext(deadline: Date.distantPast))
        }
        try fixture.setImageInventory([])
        #expect(try await runtime.listImages(context: RuntimeRequestContext()).isEmpty)
    }

    @Test func `repository digest references preserve registry ports and manifest identity`() throws {
        let digest = "sha256:" + String(repeating: "c", count: 64)
        #expect(try AppleContainerRuntime.repositoryDigestReference("docker.io/library/alpine:3.22.5", digest: digest)
            == "docker.io/library/alpine@" + digest)
        #expect(try AppleContainerRuntime.repositoryDigestReference("registry.test:5000/path/image:tag", digest: digest)
            == "registry.test:5000/path/image@" + digest)
        #expect(try AppleContainerRuntime.repositoryDigestReference("fixture@" + digest, digest: digest)
            == "fixture@" + digest)
        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime.repositoryDigestReference("fixture:latest", digest: "sha256:bad")
        }
        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime.repositoryDigestReference("", digest: digest)
        }
    }

    @Test func `image inventory rejects malformed selected manifest digest`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        var record = imageRecord("fixture:latest")
        var variants = try #require(record["variants"] as? [[String: Any]])
        variants[0]["digest"] = "sha256:invalid"
        record["variants"] = variants
        try fixture.setImageInventory([record])
        let runtime = try fixture.runtime()
        await #expect(throws: DevContainerError.self) {
            try await runtime.listImages(context: RuntimeRequestContext())
        }
    }

    @Test func `live identity client rejects malformed metadata before XPC`() async throws {
        let client = LiveAppleImageIdentityClient()
        await #expect(throws: DecodingError.self) {
            try await client.configurationIdentity(
                reference: "fixture:latest",
                descriptor: Data("{}".utf8),
                platform: Data("{}".utf8)
            )
        }
        let configuration = try #require(imageRecord("fixture:latest")["configuration"] as? [String: Any])
        let descriptor = try #require(configuration["descriptor"] as? [String: Any])
        let data = try JSONSerialization.data(withJSONObject: descriptor)
        await #expect(throws: ContainerizationError.self) {
            try await client.configurationIdentity(
                reference: "fixture:latest",
                descriptor: data,
                platform: Data("{}".utf8)
            )
        }
    }
}

func imageRecord(_ name: String) -> [String: Any] {
    [
        "id": String(repeating: "a", count: 64),
        "configuration": ["name": name, "descriptor": [
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "digest": "sha256:" + String(repeating: "a", count: 64), "size": 123
        ]],
        "variants": [[
            "platform": ["architecture": "arm64", "os": "linux"],
            "digest": "sha256:" + String(repeating: "c", count: 64),
            "config": ["config": [:]],
            "size": 123
        ]]
    ]
}
