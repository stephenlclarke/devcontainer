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

import ArgumentParser
import DevContainerModel
import Foundation

enum PluginRegistrationStatus: String, Sendable {
    case missing
    case registered
    case conflicting
}

struct PluginRegistration {
    let source: URL
    let destination: URL
    private let canonicalSource: URL

    init(source: URL, installRoot: URL) throws {
        let fileManager = FileManager.default
        let linkSource = source.standardizedFileURL
        let resolvedSource = linkSource.resolvingSymlinksInPath()
        let config = resolvedSource.appendingPathComponent("config.toml")
        let executable = resolvedSource
            .appendingPathComponent("bin", isDirectory: true)
            .appendingPathComponent("devcontainer")
        guard fileManager.fileExists(atPath: config.path),
              fileManager.isExecutableFile(atPath: executable.path)
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "invalid devcontainer plug-in payload at \(resolvedSource.path)"
            )
        }
        let resolvedInstallRoot = installRoot.standardizedFileURL
        var isDirectory: ObjCBool = false
        guard resolvedInstallRoot.path != "/",
              fileManager.fileExists(
                  atPath: resolvedInstallRoot.path,
                  isDirectory: &isDirectory
              ),
              isDirectory.boolValue
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "container install root is invalid at \(resolvedInstallRoot.path)"
            )
        }
        self.source = linkSource
        canonicalSource = resolvedSource
        destination = resolvedInstallRoot
            .appendingPathComponent("libexec/container-plugins/devcontainer")
    }

    func status() -> PluginRegistrationStatus {
        let fileManager = FileManager.default
        guard let attributes = try? fileManager.attributesOfItem(
            atPath: destination.path
        ) else {
            return .missing
        }
        guard attributes[.type] as? FileAttributeType == .typeSymbolicLink,
              let target = try? fileManager.destinationOfSymbolicLink(
                  atPath: destination.path
              )
        else {
            return .conflicting
        }
        let targetURL =
            if target.hasPrefix("/") {
                URL(fileURLWithPath: target)
            } else {
                destination.deletingLastPathComponent()
                    .appendingPathComponent(target)
            }
        return targetURL.standardizedFileURL.resolvingSymlinksInPath() == canonicalSource
            ? .registered
            : .conflicting
    }

    func register() throws {
        switch status() {
        case .registered:
            return
        case .conflicting:
            throw conflictError()
        case .missing:
            break
        }
        try FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try FileManager.default.createSymbolicLink(
            at: destination,
            withDestinationURL: source
        )
    }

    func unregister() throws {
        switch status() {
        case .missing:
            return
        case .conflicting:
            throw conflictError()
        case .registered:
            try FileManager.default.removeItem(at: destination)
        }
    }

    private func conflictError() -> DevContainerError {
        DevContainerError(
            .conflict,
            message: "refusing to replace foreign plug-in registration at \(destination.path)"
        )
    }

    static func packagedPluginURL(
        executable: String = CommandLine.arguments[0]
    ) -> URL {
        let resolvedExecutable = URL(fileURLWithPath: executable)
            .standardizedFileURL
            .resolvingSymlinksInPath()
        let marker = "/Cellar/devcontainer/"
        if let range = resolvedExecutable.path.range(of: marker) {
            let prefix = String(resolvedExecutable.path[..<range.lowerBound])
            return URL(fileURLWithPath: prefix, isDirectory: true)
                .appendingPathComponent("opt/devcontainer", isDirectory: true)
                .appendingPathComponent("libexec/container/plugins/devcontainer")
        }
        return resolvedExecutable
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("libexec/container/plugins/devcontainer")
    }
}

enum ContainerInstallRootResolver {
    static func resolve(container: URL) throws -> URL {
        let standardOutput = Pipe()
        let standardError = Pipe()
        let process = Process()
        process.executableURL = container
        process.arguments = ["system", "status", "--format", "json"]
        process.environment = CLIPaths.safeEnvironment
        process.standardOutput = standardOutput
        process.standardError = standardError
        try process.run()
        process.waitUntilExit()
        let output = try standardOutput.fileHandleForReading.readToEnd() ?? Data()
        let error = try standardError.fileHandleForReading.readToEnd() ?? Data()
        guard process.terminationStatus == 0 else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: String(data: error, encoding: .utf8)
                    ?? "container system status failed"
            )
        }
        return try installRoot(from: output)
    }

    static func installRoot(from output: Data) throws -> URL {
        let object = try JSONSerialization.jsonObject(with: output)
        guard let record = object as? [String: Any],
              let path = record["installRoot"] as? String,
              !path.isEmpty
        else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "container system status did not report installRoot"
            )
        }
        return URL(fileURLWithPath: path, isDirectory: true)
    }
}

private struct PluginOptions: ParsableArguments {
    @Option(name: .long, help: "Apple container executable.")
    var container = CLIPaths.containerExecutable

    @Option(name: .long, help: "Override the active container installation root.")
    var installRoot: String?

    @Option(name: .long, help: "Override the packaged devcontainer plug-in directory.")
    var plugin: String?

    func registration() throws -> PluginRegistration {
        let root = try installRoot
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? ContainerInstallRootResolver.resolve(
                container: URL(fileURLWithPath: container)
            )
        return try PluginRegistration(
            source: plugin
                .map { URL(fileURLWithPath: $0, isDirectory: true) }
                ?? PluginRegistration.packagedPluginURL(),
            installRoot: root
        )
    }
}

struct PluginCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "plugin",
        abstract: "Explicitly manage Apple container CLI plug-in registration",
        subcommands: [
            PluginRegisterCommand.self,
            PluginUnregisterCommand.self,
            PluginStatusCommand.self
        ],
        defaultSubcommand: PluginStatusCommand.self
    )
}

private struct PluginRegisterCommand: ParsableCommand {
    static let configuration = CommandConfiguration(commandName: "register")

    @OptionGroup
    var options: PluginOptions

    func run() throws {
        let registration = try options.registration()
        try registration.register()
        print(registration.destination.path)
    }
}

private struct PluginUnregisterCommand: ParsableCommand {
    static let configuration = CommandConfiguration(commandName: "unregister")

    @OptionGroup
    var options: PluginOptions

    func run() throws {
        let registration = try options.registration()
        try registration.unregister()
        print(registration.destination.path)
    }
}

private struct PluginStatusCommand: ParsableCommand {
    static let configuration = CommandConfiguration(commandName: "status")

    @OptionGroup
    var options: PluginOptions

    func run() throws {
        let registration = try options.registration()
        print("\(registration.status().rawValue) \(registration.destination.path)")
    }
}
