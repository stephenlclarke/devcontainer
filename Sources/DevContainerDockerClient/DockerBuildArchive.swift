// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerProcess
import Foundation

public struct DockerBuildArchive: Sendable {
    public let data: Data
    public let dockerfile: String

    typealias ArchiveRunner = @Sendable ([String], Int) async throws -> CapturedProcessResult

    /// Bounded in-memory request, matching the gateway's buffered build API.
    /// Generated Dockerfiles can be outside the context; stage only that file.
    public static func prepare(_ spec: DockerBuildCommand) async throws -> Self {
        try await withThrowingTaskGroup(of: Self.self) { group in
            defer { group.cancelAll() }
            group.addTask { try await create(spec) }
            group.addTask {
                try await Task.sleep(for: .seconds(60))
                throw DockerFrontendError.usage("build context preparation exceeded 60 seconds")
            }
            return try await group.next()!
        }
    }

    static func create(
        _ spec: DockerBuildCommand,
        maximumArchiveBytes: Int = 64 * 1024 * 1024,
        run: ArchiveRunner = runTar
    ) async throws -> Self {
        let files = FileManager.default
        let context = URL(fileURLWithPath: spec.context).standardizedFileURL.resolvingSymlinksInPath()
        var directory: ObjCBool = false
        guard files.fileExists(atPath: context.path, isDirectory: &directory), directory.boolValue else {
            throw DockerFrontendError.usage("build context must be a local directory")
        }
        let dockerfile = spec.dockerfile.map { URL(fileURLWithPath: $0).standardizedFileURL }
            ?? context.appendingPathComponent("Dockerfile")
        // Never upload files which the operator intended to exclude. The D02
        // contract has no ignore file; unsupported exclusion semantics fail closed.
        for path in [context.appendingPathComponent(".dockerignore").path, dockerfile.path + ".dockerignore"] {
            var info = stat()
            if lstat(path, &info) == 0 {
                throw DockerFrontendError.usage("build context ignore files are not yet supported: \(path)")
            }
            guard errno == ENOENT else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        }
        let content = try readDockerfile(dockerfile)
        let resolvedFile = dockerfile.resolvingSymlinksInPath()
        if context.path != "/", resolvedFile.path.hasPrefix(context.path + "/") {
            let relative = String(resolvedFile.path.dropFirst(context.path.count + 1))
            return try await archive(
                context: context,
                dockerfile: relative,
                extraArguments: [],
                maximumBytes: maximumArchiveBytes,
                run: run
            )
        }
        let name = ".devcontainer-build-" + UUID().uuidString
        guard !files.fileExists(atPath: context.appendingPathComponent(name).path) else {
            throw DockerFrontendError.usage("generated Dockerfile conflicts with the build context")
        }
        let temporary = ProcessInfo.processInfo.environment["TMPDIR"] ?? files.temporaryDirectory.path
        guard temporary.hasPrefix("/"), !temporary.contains("\0") else {
            throw DockerFrontendError.usage("TMPDIR must be an absolute directory")
        }
        // Foundation's cached temporaryDirectory can ignore the caller's TMPDIR.
        // Honour the workflow's explicit SSD selection before its normal fallback.
        let stage = URL(fileURLWithPath: temporary).standardizedFileURL.resolvingSymlinksInPath()
            .appendingPathComponent(name)
        try requireSeparateStage(stage, context: context)
        try files.createDirectory(at: stage, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? files.removeItem(at: stage) }
        // This private directory is unpublished until both writes finish. Avoid
        // Foundation's atomic replacement directory, which can escape TMPDIR.
        try content.write(to: stage.appendingPathComponent(name))
        // Match Docker CLI's external-Dockerfile injection: build tooling can
        // read this file but COPY/ADD must not incorporate it or our ignore file.
        try Data((name + "\n.dockerignore\n").utf8).write(to: stage.appendingPathComponent(".dockerignore"))
        return try await archive(
            context: context,
            dockerfile: name,
            extraArguments: ["-C", stage.path, name, ".dockerignore"],
            maximumBytes: maximumArchiveBytes,
            run: run
        )
    }

    static func requireSeparateStage(_ stage: URL, context: URL) throws {
        let root = context.standardizedFileURL.resolvingSymlinksInPath().path
        let path = stage.standardizedFileURL.resolvingSymlinksInPath().path
        guard root != "/", path != root, !path.hasPrefix(root + "/") else {
            throw DockerFrontendError.usage("build context must not contain its temporary staging directory")
        }
    }

    private static func archive(
        context: URL,
        dockerfile: String,
        extraArguments: [String],
        maximumBytes: Int,
        run: ArchiveRunner
    ) async throws -> Self {
        let result = try await run(
            [
                "--format=pax",
                "--no-xattrs",
                "--no-mac-metadata",
                "-cf",
                "-",
                "-C",
                context.path,
                "."
            ] + extraArguments,
            maximumBytes
        )
        guard result.exitCode == 0, result.omittedStandardOutputBytes == 0,
              result.omittedStandardErrorBytes == 0, !result.standardOutput.isEmpty
        else { throw DockerFrontendError.usage("build context archiving failed or exceeded 64 MiB") }
        return Self(data: result.standardOutput, dockerfile: dockerfile)
    }

    private static func runTar(_ arguments: [String], maximumBytes: Int) async throws -> CapturedProcessResult {
        try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: arguments,
            environment: ["PATH": "/usr/bin:/bin", "COPYFILE_DISABLE": "1", "LC_ALL": "C"],
            maximumOutputBytes: maximumBytes
        )
    }

    private static func readDockerfile(_ file: URL) throws -> Data {
        // Nonblocking + fstat prevent a substituted FIFO/device from hanging the
        // frontend; O_NOFOLLOW makes the final component's policy explicit.
        let descriptor = open(file.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_size > 0, info.st_size <= 1024 * 1024
        else { throw DockerFrontendError.usage("Dockerfile must be a nonempty regular file of at most 1 MiB") }
        let data = try handle.read(upToCount: 1024 * 1024 + 1) ?? Data()
        guard !data.isEmpty, data.count <= 1024 * 1024 else {
            throw DockerFrontendError.usage("Dockerfile changed or exceeded 1 MiB")
        }
        return data
    }
}
