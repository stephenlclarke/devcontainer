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

import DevContainerProcess
import Foundation

extension DockerCLIApplication {
    func image(_ arguments: [String]) throws -> DockerCLIResult {
        guard let command = arguments.first else {
            throw DockerCLIError.invalidArguments("image requires a command")
        }
        let values = Array(arguments.dropFirst())
        switch command {
        case "inspect":
            return try inspectResource(values, type: "image")
        case "rm", "remove":
            return try removeImages(values)
        default:
            throw DockerCLIError.unsupported("image \(command)")
        }
    }

    func network(_ arguments: [String]) throws -> DockerCLIResult {
        guard let command = arguments.first else {
            throw DockerCLIError.invalidArguments("network requires a command")
        }
        let values = Array(arguments.dropFirst())
        switch command {
        case "create":
            return try createNetwork(values)
        case "inspect":
            return try inspectNetworks(values)
        case "ls", "list":
            return try listNetworks(values)
        case "rm", "remove":
            return try removeNetworks(values)
        default:
            throw DockerCLIError.unsupported("network \(command)")
        }
    }

    func copy(_ arguments: [String]) throws -> DockerCLIResult {
        guard arguments.count == 2 else {
            throw DockerCLIError.invalidArguments("cp requires source and destination")
        }
        let source = Self.containerPath(arguments[0])
        let destination = Self.containerPath(arguments[1])
        switch (source, destination) {
        case let (.none, .some(remote)):
            try copyToContainer(local: arguments[0], remote: remote)
        case let (.some(remote), .none):
            try copyFromContainer(remote: remote, local: arguments[1])
        default:
            throw DockerCLIError.invalidArguments(
                "cp requires exactly one container path"
            )
        }
        return DockerCLIResult()
    }

    func createVolume(_ arguments: [String]) throws -> DockerCLIResult {
        guard arguments.count == 1, !arguments[0].hasPrefix("-") else {
            throw DockerCLIError.invalidArguments("volume create requires a name")
        }
        let response = try request(
            "POST",
            "/volumes/create",
            body: Self.json(["Name": arguments[0], "Driver": "local"])
        )
        let name = try Self.object(response.body)["Name"] as? String ?? arguments[0]
        return .stdout("\(name)\n")
    }

    private func inspectResource(_ arguments: [String], type: String) throws -> DockerCLIResult {
        var values = arguments
        var options: [String] = ["--type", type]
        if let format = Self.takeOption("--format", short: "-f", from: &values) {
            options += ["--format", format]
        }
        return try inspect(options + values)
    }

    private func removeImages(_ arguments: [String]) throws -> DockerCLIResult {
        let parsed = try Self.removalArguments(arguments, resource: "image")
        for identifier in parsed.identifiers {
            let path = "/images/\(Self.path(identifier))"
            let target = parsed.force ? Self.target(path, query: [("force", "true")]) : path
            _ = try request("DELETE", target)
        }
        return .stdout(parsed.identifiers.joined(separator: "\n") + "\n")
    }

    private func createNetwork(_ arguments: [String]) throws -> DockerCLIResult {
        guard arguments.count == 1, !arguments[0].hasPrefix("-") else {
            throw DockerCLIError.invalidArguments("network create requires a name")
        }
        let response = try request(
            "POST",
            "/networks/create",
            body: Self.json(["Name": arguments[0], "Driver": "bridge", "Labels": [:]])
        )
        let identifier = try Self.object(response.body)["Id"] as? String ?? arguments[0]
        return .stdout("\(identifier)\n")
    }

    private func inspectNetworks(_ arguments: [String]) throws -> DockerCLIResult {
        guard !arguments.isEmpty, arguments.allSatisfy({ !$0.hasPrefix("-") }) else {
            throw DockerCLIError.invalidArguments("network inspect requires a network")
        }
        let objects = try arguments.map {
            try JSONSerialization.jsonObject(
                with: request("GET", "/networks/\(Self.path($0))").body
            )
        }
        return try DockerCLIResult(standardOutput: Self.json(objects) + Data("\n".utf8))
    }

    private func listNetworks(_ arguments: [String]) throws -> DockerCLIResult {
        let options = try DockerResourceListOptions(arguments: arguments, resource: "network")
        let networks = try Self.array(
            request("GET", options.target(path: "/networks")).body
        )
        let identifiers = try networks.map { value in
            guard let identifier = (value as? [String: Any])?["Id"] as? String else {
                throw DockerCLIError.malformedResponse("network list entry has no Id")
            }
            return identifier
        }
        return .stdout(identifiers.isEmpty ? "" : identifiers.joined(separator: "\n") + "\n")
    }

    func listVolumes(_ arguments: [String]) throws -> DockerCLIResult {
        let options = try DockerResourceListOptions(arguments: arguments, resource: "volume")
        let response = try Self.object(request("GET", options.target(path: "/volumes")).body)
        let rawVolumes = response["Volumes"]
        let volumes: [Any]
        if rawVolumes == nil || rawVolumes is NSNull {
            volumes = []
        } else if let values = rawVolumes as? [Any] {
            volumes = values
        } else {
            throw DockerCLIError.malformedResponse("volume list response has invalid Volumes")
        }
        let names = try volumes.map { value in
            guard let name = (value as? [String: Any])?["Name"] as? String else {
                throw DockerCLIError.malformedResponse("volume list entry has no Name")
            }
            return name
        }
        return .stdout(names.isEmpty ? "" : names.joined(separator: "\n") + "\n")
    }

    private func removeNetworks(_ arguments: [String]) throws -> DockerCLIResult {
        let parsed = try Self.removalArguments(arguments, resource: "network")
        guard !parsed.force else {
            throw DockerCLIError.invalidArguments("network rm does not support --force")
        }
        for identifier in parsed.identifiers {
            _ = try request("DELETE", "/networks/\(Self.path(identifier))")
        }
        return .stdout(parsed.identifiers.joined(separator: "\n") + "\n")
    }

    private func copyToContainer(
        local: String,
        remote: (container: String, path: String)
    ) throws {
        let contentsOnly = local.hasSuffix("/.")
        let sourcePath = contentsOnly ? String(local.dropLast(2)) : local
        let source = URL(fileURLWithPath: sourcePath)
        let archiveRoot = contentsOnly ? source : source.deletingLastPathComponent()
        let archiveEntry = contentsOnly ? "." : source.lastPathComponent
        guard FileManager.default.fileExists(atPath: archiveRoot.path) else {
            throw DockerCLIError.invalidArguments("cp source does not exist: \(local)")
        }
        let archive = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: [
                "--no-xattrs", "-cf", "-", "-C", archiveRoot.path, "--",
                archiveEntry
            ],
            environment: ["COPYFILE_DISABLE": "1", "PATH": "/usr/bin:/bin"]
        )
        guard archive.exitCode == 0 else {
            throw DockerCLIError.invalidArguments(
                String(data: archive.standardError, encoding: .utf8) ?? "could not archive cp source"
            )
        }
        _ = try request(
            "PUT",
            Self.target(
                "/containers/\(Self.path(remote.container))/archive",
                query: [("path", remote.path)]
            ),
            body: archive.standardOutput
        )
    }

    private func copyFromContainer(
        remote: (container: String, path: String),
        local: String
    ) throws {
        let fileManager = FileManager.default
        let transferRoot = fileManager.temporaryDirectory.appendingPathComponent(
            "devcontainer-cp-\(UUID().uuidString)",
            isDirectory: true
        )
        try fileManager.createDirectory(
            at: transferRoot,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        defer { try? fileManager.removeItem(at: transferRoot) }
        let (response, archive) = try downloadContainerArchive(
            remote: remote,
            into: transferRoot
        )
        try Self.placeContainerArchive(
            archive,
            stat: Self.copyPathStat(response),
            local: local,
            transferRoot: transferRoot
        )
    }

    private func downloadContainerArchive(
        remote: (container: String, path: String),
        into transferRoot: URL
    ) throws -> (DockerHTTPResponse, URL) {
        let fileManager = FileManager.default
        let archive = transferRoot.appendingPathComponent("archive.tar")
        guard fileManager.createFile(
            atPath: archive.path,
            contents: nil,
            attributes: [.posixPermissions: 0o600]
        ) else {
            throw DockerCLIError.invalidArguments("could not create container copy staging file")
        }
        let archiveWriter = try FileHandle(forWritingTo: archive)
        defer { try? archiveWriter.close() }
        let response = try transport.send(
            DockerHTTPRequest(
                method: "GET",
                target: Self.target(
                    "/containers/\(Self.path(remote.container))/archive",
                    query: [("path", remote.path)]
                )
            ),
            maximumBodyBytes: nil
        ) { chunk in
            try archiveWriter.write(contentsOf: chunk)
        }
        try archiveWriter.close()
        return (response, archive)
    }

    private static func placeContainerArchive(
        _ archive: URL,
        stat: DockerCopyPathStat,
        local: String,
        transferRoot: URL
    ) throws {
        let fileManager = FileManager.default
        let destination = URL(fileURLWithPath: local)
        var destinationIsDirectory = ObjCBool(false)
        let destinationExists = fileManager.fileExists(
            atPath: destination.path,
            isDirectory: &destinationIsDirectory
        )
        guard destinationExists || !local.hasSuffix("/") else {
            throw DockerCLIError.invalidArguments(
                "cp destination directory does not exist: \(local)"
            )
        }
        guard !destinationExists || destinationIsDirectory.boolValue || !stat.isDirectory else {
            throw DockerCLIError.invalidArguments(
                "cannot copy a directory onto a file: \(local)"
            )
        }
        if destinationExists, destinationIsDirectory.boolValue {
            try Self.extractArchive(archive, into: destination)
            return
        }

        let parent = destination.deletingLastPathComponent()
        try fileManager.createDirectory(at: parent, withIntermediateDirectories: true)
        let staging = transferRoot.appendingPathComponent(
            "extracted",
            isDirectory: true
        )
        try fileManager.createDirectory(at: staging, withIntermediateDirectories: false)
        try Self.extractArchive(archive, into: staging)
        let source = staging.appendingPathComponent(stat.name)
        guard fileManager.fileExists(atPath: source.path) else {
            throw DockerCLIError.malformedResponse(
                "container archive does not contain \(stat.name)"
            )
        }
        if destinationExists {
            try fileManager.removeItem(at: destination)
        }
        try fileManager.moveItem(at: source, to: destination)
    }

    private static func extractArchive(_ archive: URL, into destination: URL) throws {
        let extracted = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", archive.path, "-C", destination.path],
            environment: ["PATH": "/usr/bin:/bin"]
        )
        guard extracted.exitCode == 0 else {
            throw DockerCLIError.invalidArguments(
                String(data: extracted.standardError, encoding: .utf8)
                    ?? "could not extract container archive"
            )
        }
    }

    private static func copyPathStat(_ response: DockerHTTPResponse) throws -> DockerCopyPathStat {
        guard let encoded = response.headers.first(where: {
            $0.key.caseInsensitiveCompare("X-Docker-Container-Path-Stat") == .orderedSame
        })?.value,
            let data = Data(base64Encoded: encoded),
            let stat = try? JSONDecoder().decode(DockerCopyPathStat.self, from: data),
            !stat.name.isEmpty,
            stat.name == URL(fileURLWithPath: stat.name).lastPathComponent
        else {
            throw DockerCLIError.malformedResponse(
                "container archive response has no valid path stat"
            )
        }
        return stat
    }

    private static func containerPath(_ value: String) -> (container: String, path: String)? {
        let parts = value.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[0].isEmpty, parts[1].hasPrefix("/") else {
            return nil
        }
        return (String(parts[0]), String(parts[1]))
    }

    static func removalArguments(
        _ arguments: [String],
        resource: String
    ) throws -> (force: Bool, identifiers: [String]) {
        var force = false
        var identifiers: [String] = []
        for argument in arguments {
            switch argument {
            case "-f", "--force": force = true
            case _ where argument.hasPrefix("-"):
                throw DockerCLIError.invalidArguments(
                    "unsupported \(resource) rm option \(argument)"
                )
            default: identifiers.append(argument)
            }
        }
        guard !identifiers.isEmpty else {
            throw DockerCLIError.invalidArguments("\(resource) rm requires a \(resource)")
        }
        return (force, identifiers)
    }

    private static func takeOption(
        _ name: String,
        short: String,
        from arguments: inout [String]
    ) -> String? {
        for (index, argument) in arguments.enumerated()
            where argument == name || argument == short
        {
            guard index + 1 < arguments.count else { return nil }
            let value = arguments[index + 1]
            arguments.removeSubrange(index ... (index + 1))
            return value
        }
        return nil
    }
}

private struct DockerCopyPathStat: Decodable {
    let name: String
    let mode: UInt32

    var isDirectory: Bool {
        mode & (1 << 31) != 0
    }
}

private struct DockerResourceListOptions {
    var labels: [String] = []

    init(arguments: [String], resource: String) throws {
        var index = 0
        var quiet = false
        while index < arguments.count {
            switch arguments[index] {
            case "-q", "--quiet":
                quiet = true
            case "--filter":
                index += 1
                guard index < arguments.count else {
                    throw DockerCLIError.invalidArguments("--filter requires a value")
                }
                try addFilter(arguments[index], resource: resource)
            case let option where option.hasPrefix("--filter="):
                try addFilter(String(option.dropFirst("--filter=".count)), resource: resource)
            default:
                throw DockerCLIError.invalidArguments(
                    "unsupported \(resource) ls option \(arguments[index])"
                )
            }
            index += 1
        }
        guard quiet else {
            throw DockerCLIError.invalidArguments("\(resource) ls requires --quiet")
        }
    }

    func target(path: String) throws -> String {
        guard !labels.isEmpty else { return path }
        let data = try DockerCLIApplication.json(["label": labels])
        guard let filters = String(data: data, encoding: .utf8) else {
            throw DockerCLIError.malformedResponse("could not encode resource filters")
        }
        return DockerCLIApplication.target(path, query: [("filters", filters)])
    }

    private mutating func addFilter(_ value: String, resource: String) throws {
        guard value.hasPrefix("label=") else {
            throw DockerCLIError.invalidArguments(
                "unsupported \(resource) ls filter \(value)"
            )
        }
        labels.append(String(value.dropFirst("label=".count)))
    }
}
