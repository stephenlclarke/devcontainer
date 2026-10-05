// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationOCI
import ContainerResource
import CryptoKit
import Darwin
import DevContainerModel
import Foundation

/// A verified package-owned Linux helper admitted for one native launch.
struct NativeTerminalLauncherAsset: Equatable, Sendable {
    let source: URL
    let architecture: String
    let sha256: String
    let device: UInt64
    let inode: UInt64

    var attestation: String {
        "\(architecture):\(sha256):\(device):\(inode)"
    }
}

/// Resolves, attests, and projects the fixed helper used for initial TTY sizing.
enum NativeTerminalLauncher {
    static let guestPath = "/run/devcontainer/terminal-launcher"
    static let mountDestination = guestPath
    static let label = "io.devcontainer.terminal-launcher"
    static let assetDirectory = "libexec/devcontainer/terminal-launcher"
    static let maximumAssetSize = 16 * 1024 * 1024

    /// Returns hashes embedded in the signed production engine, or none in development builds.
    static func expectedSHA256ByArchitecture() -> [String: String] {
        #if DEVCONTAINER_NATIVE_TERMINAL_LAUNCHER
            NativeTerminalLauncherIdentity.sha256ByArchitecture
        #else
            [:]
        #endif
    }

    /// Resolves the helper beneath the installed engine prefix and verifies its embedded identity.
    static func resolveInstalled(
        architecture: String = Platform.current.architecture,
        executableURL: URL? = Bundle.main.executableURL,
        sha256ByArchitecture: [String: String] = expectedSHA256ByArchitecture()
    ) throws -> NativeTerminalLauncherAsset {
        guard let executableURL else {
            throw unavailable("cannot locate the installed engine executable")
        }
        return try resolve(
            executableURL: executableURL,
            architecture: architecture,
            sha256ByArchitecture: sha256ByArchitecture
        )
    }

    /// Returns a requested TTY size, with `[0, 0]` meaning the native default.
    static func requestedSize(spec: ContainerSpec) throws -> (width: UInt16, height: UInt16)? {
        guard spec.terminal, spec.terminalWidth != nil || spec.terminalHeight != nil else { return nil }
        guard let width = spec.terminalWidth, let height = spec.terminalHeight else {
            throw DevContainerError(.invalidRequest, message: "initial terminal size requires both dimensions")
        }
        return width == 0 && height == 0 ? nil : (width, height)
    }

    /// Resolves and verifies a helper using an injected identity map for deterministic tests.
    static func resolve(
        executableURL: URL,
        architecture: String,
        sha256ByArchitecture: [String: String]
    ) throws -> NativeTerminalLauncherAsset {
        guard architecture == "arm64" || architecture == "amd64",
              let expected = sha256ByArchitecture[architecture], isSHA256(expected)
        else {
            throw unavailable("no trusted terminal launcher is available for Linux \(architecture)")
        }
        let source = try installedSource(executableURL: executableURL, architecture: architecture)
        let (data, fileInfo) = try readVerifiedFile(at: source)
        let actual = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard actual == expected else {
            throw unavailable("installed terminal launcher differs from the signed package identity")
        }
        return NativeTerminalLauncherAsset(
            source: source,
            architecture: architecture,
            sha256: actual,
            device: UInt64(fileInfo.st_dev),
            inode: UInt64(fileInfo.st_ino)
        )
    }

    private static func installedSource(executableURL: URL, architecture: String) throws -> URL {
        let engine = executableURL.standardizedFileURL.resolvingSymlinksInPath()
        guard engine.lastPathComponent == "devcontainer-engine",
              engine.deletingLastPathComponent().lastPathComponent == "bin"
        else {
            throw unavailable("engine executable is outside the installed package layout")
        }
        let prefix = engine.deletingLastPathComponent().deletingLastPathComponent()
        let source = prefix.appendingPathComponent(assetDirectory, isDirectory: true)
            .appendingPathComponent("devcontainer-terminal-linux-\(architecture)")
        guard source.standardizedFileURL.resolvingSymlinksInPath() == source.standardizedFileURL else {
            throw unavailable("installed terminal launcher path is not canonical")
        }
        return source
    }

    private static func readVerifiedFile(at source: URL) throws -> (Data, stat) {
        let descriptor = open(source.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
        guard descriptor >= 0 else { throw unavailable("installed terminal launcher cannot be opened") }
        defer { close(descriptor) }
        var before = stat()
        guard fstat(descriptor, &before) == 0,
              before.st_mode & S_IFMT == S_IFREG, before.st_nlink == 1,
              before.st_size > 0, before.st_size <= maximumAssetSize,
              before.st_mode & 0o111 == 0o111
        else {
            throw unavailable("installed terminal launcher is not a bounded executable file")
        }
        var pathInfo = stat()
        guard lstat(source.path, &pathInfo) == 0,
              pathInfo.st_dev == before.st_dev, pathInfo.st_ino == before.st_ino
        else {
            throw unavailable("installed terminal launcher changed while being admitted")
        }
        let data = try FileHandle(fileDescriptor: descriptor, closeOnDealloc: false).readToEnd() ?? Data()
        var after = stat()
        guard fstat(descriptor, &after) == 0,
              after.st_dev == before.st_dev, after.st_ino == before.st_ino,
              after.st_size == before.st_size, data.count == before.st_size
        else {
            throw unavailable("installed terminal launcher changed while being read")
        }
        var finalPathInfo = stat()
        guard lstat(source.path, &finalPathInfo) == 0,
              finalPathInfo.st_dev == before.st_dev, finalPathInfo.st_ino == before.st_ino,
              finalPathInfo.st_mode & 0o111 == 0o111
        else {
            throw unavailable("installed terminal launcher path changed after verification")
        }
        return (data, before)
    }

    /// Builds a process configuration that sets the initial terminal size before workload exec.
    static func invocation(
        process: ProcessConfiguration,
        spec: ContainerSpec
    ) throws -> ProcessConfiguration {
        guard let size = try requestedSize(spec: spec) else {
            throw DevContainerError(.invalidRequest, message: "initial terminal size requires a nonzero TTY size")
        }
        var process = process
        let originalExecutable = process.executable
        process.executable = guestPath
        process.arguments = [String(size.height), String(size.width), "--", originalExecutable] + process.arguments
        return process
    }

    /// Binds the unchanged process configuration to the admitted helper identity.
    static func processAttestation(
        _ process: ProcessConfiguration, asset: NativeTerminalLauncherAsset
    ) throws -> String {
        let original = try processFingerprint(process)
        return "\(asset.attestation):\(original)"
    }

    /// Verifies an adopted native configuration against the signed installed helper identity.
    @discardableResult
    static func verify(configuration: ContainerConfiguration) throws -> NativeTerminalLauncherAsset? {
        guard let attestation = configuration.labels[label] else { return nil }
        let fields = try attestationFields(attestation)
        let asset = try resolveInstalled(architecture: String(fields[0]))
        return try verify(configuration: configuration, fields: fields, asset: asset)
    }

    /// Validates stored launcher metadata using an asset already admitted by the package resolver.
    @discardableResult
    static func verify(
        configuration: ContainerConfiguration,
        asset: NativeTerminalLauncherAsset
    ) throws -> NativeTerminalLauncherAsset? {
        guard let attestation = configuration.labels[label] else { return nil }
        return try verify(configuration: configuration, fields: attestationFields(attestation), asset: asset)
    }

    private static func attestationFields(_ attestation: String) throws -> [Substring] {
        let fields = attestation.split(separator: ":", omittingEmptySubsequences: false)
        guard fields.count == 5, !fields[0].isEmpty,
              UInt64(fields[2]) != nil, UInt64(fields[3]) != nil
        else {
            throw DevContainerError(.stateCorruption, message: "native terminal launcher identity is malformed")
        }
        return fields
    }

    private static func verify(
        configuration: ContainerConfiguration,
        fields: [Substring],
        asset: NativeTerminalLauncherAsset
    ) throws -> NativeTerminalLauncherAsset {
        let arguments = configuration.initProcess.arguments
        guard asset.attestation == fields.prefix(4).joined(separator: ":"),
              arguments.count >= 4,
              let height = UInt16(arguments[0]),
              let width = UInt16(arguments[1]),
              width > 0 || height > 0
        else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "native terminal launcher identity or dimensions changed"
            )
        }
        try verify(configuration: configuration, width: width, height: height, asset: asset)
        guard try processFingerprint(originalProcess(configuration.initProcess)) == String(fields[4]) else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "native terminal launcher workload process changed"
            )
        }
        return asset
    }

    /// Verifies both the helper identity and the requested workload specification.
    static func verify(configuration: ContainerConfiguration, spec: ContainerSpec) throws {
        let installed = try verify(configuration: configuration)
        guard try requestedSize(spec: spec) != nil else {
            guard installed == nil else {
                throw DevContainerError(
                    .providerProtocolMismatch,
                    message: "native terminal launcher is not present in the requested container spec"
                )
            }
            return
        }
        guard let installed else {
            throw DevContainerError(.providerProtocolMismatch, message: "native terminal launcher is missing")
        }
        try verify(configuration: configuration, spec: spec, asset: installed)
    }

    /// Verifies a projected configuration using its already admitted helper asset.
    static func verify(
        configuration: ContainerConfiguration, spec: ContainerSpec, asset: NativeTerminalLauncherAsset
    ) throws {
        guard let size = try requestedSize(spec: spec) else {
            guard configuration.labels[label] == nil else {
                throw DevContainerError(
                    .providerProtocolMismatch,
                    message: "native terminal launcher is not present in the requested container spec"
                )
            }
            return
        }
        try verify(configuration: configuration, width: size.width, height: size.height, asset: asset)
        let expected = try processAttestation(originalProcess(configuration.initProcess), asset: asset)
        guard configuration.labels[label] == expected else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "native terminal launcher workload process changed"
            )
        }
    }

    private static func verify(
        configuration: ContainerConfiguration,
        width: UInt16,
        height: UInt16,
        asset: NativeTerminalLauncherAsset
    ) throws {
        let mounts = configuration.mounts.filter { $0.destination == mountDestination }
        guard mounts.count == 1, let mount = mounts.first,
              mount.isVirtiofs, mount.options.readonly, mount.source == asset.source.path,
              configuration.labels[label]?.hasPrefix(asset.attestation + ":") == true,
              configuration.initProcess.terminal,
              configuration.initProcess.executable == guestPath,
              Array(configuration.initProcess.arguments.prefix(3)) == [String(height), String(width), "--"],
              configuration.initProcess.arguments.count >= 4
        else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "native initial terminal launcher configuration changed"
            )
        }
    }

    private static func isSHA256(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy {
            ($0 >= 48 && $0 <= 57) || ($0 >= 97 && $0 <= 102)
        }
    }

    private static func unavailable(_ message: String) -> DevContainerError {
        DevContainerError(.unsupportedCapability, message: message)
    }

    private static func processFingerprint(_ process: ProcessConfiguration) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(process)
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func originalProcess(_ process: ProcessConfiguration) -> ProcessConfiguration {
        var original = process
        original.executable = process.arguments[3]
        original.arguments = Array(process.arguments.dropFirst(4))
        return original
    }
}
