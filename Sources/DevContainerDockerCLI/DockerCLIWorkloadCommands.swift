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
    func createContainer(_ arguments: [String]) throws -> DockerCLIResult {
        let identifier = try createContainer(DockerRunOptions(arguments: arguments))
        return .stdout("\(identifier)\n")
    }

    func build(
        _ arguments: [String],
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        let options = try DockerBuildOptions(arguments: arguments)
        let archive = try options.archive()
        let query = try options.query()
        return try streamRequest(
            "POST",
            Self.target("/build", query: query),
            body: archive,
            maximumBodyBytes: nil,
            streamingOutput: streamingOutput
        )
    }

    func runContainer(
        _ arguments: [String],
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        let options = try DockerRunOptions(arguments: arguments)
        let identifier = try createContainer(options)
        _ = try request("POST", "/containers/\(Self.path(identifier))/start")
        if options.detach {
            return .stdout("\(identifier)\n")
        }
        return try followContainer(identifier, streamingOutput: streamingOutput)
    }

    private func createContainer(_ options: DockerRunOptions) throws -> String {
        let createTarget =
            options.name.map {
                Self.target("/containers/create", query: [("name", $0)])
            } ?? "/containers/create"
        let create = try request(
            "POST",
            createTarget,
            body: Self.json(options.createRequest)
        )
        guard let identifier = try Self.object(create.body)["Id"] as? String else {
            throw DockerCLIError.malformedResponse("container create response has no Id")
        }
        return identifier
    }

    private func followContainer(
        _ identifier: String,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        var decoder = DockerMultiplexedStreamDecoder()
        var captured = Data()
        _ = try transport.send(
            DockerHTTPRequest(
                method: "GET",
                target: Self.target(
                    "/containers/\(Self.path(identifier))/logs",
                    query: [("follow", "true"), ("stdout", "true"), ("stderr", "true")]
                )
            ),
            maximumBodyBytes: nil
        ) { chunk in
            try decoder.append(chunk) { data, standardError in
                if let streamingOutput {
                    try streamingOutput(data, standardError)
                } else {
                    captured.append(data)
                }
            }
        }
        try decoder.finish()
        let wait = try request(
            "POST",
            "/containers/\(Self.path(identifier))/wait"
        )
        let statusCode = try (Self.object(wait.body)["StatusCode"] as? NSNumber)?.int32Value ?? 1
        return DockerCLIResult(
            standardOutput: captured,
            exitCode: statusCode
        )
    }

    func exec(
        _ arguments: [String],
        standardInput: Data?,
        standardInputFileDescriptor: Int32?,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        let options = try DockerExecOptions(arguments: arguments)
        let create = try request(
            "POST",
            "/containers/\(Self.path(options.container))/exec",
            body: Self.json(options.createRequest)
        )
        guard let identifier = try Self.object(create.body)["Id"] as? String else {
            throw DockerCLIError.malformedResponse("exec create response has no Id")
        }
        let streams = try startExec(
            identifier: identifier,
            options: options,
            standardInput: standardInput,
            standardInputFileDescriptor: standardInputFileDescriptor,
            streamingOutput: streamingOutput
        )
        let inspect = try request("GET", "/exec/\(Self.path(identifier))/json")
        let exitCode = try (Self.object(inspect.body)["ExitCode"] as? NSNumber)?.int32Value ?? 1
        return DockerCLIResult(
            standardOutput: streams.output,
            standardError: streams.error,
            exitCode: exitCode
        )
    }

    private func startExec(
        identifier: String,
        options: DockerExecOptions,
        standardInput: Data?,
        standardInputFileDescriptor: Int32?,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> (output: Data, error: Data) {
        var decoder = DockerMultiplexedStreamDecoder(terminal: options.terminal)
        var standardOutput = Data()
        var standardError = Data()
        let startRequest = try DockerHTTPRequest(
            method: "POST",
            target: "/exec/\(Self.path(identifier))/start",
            headers: [
                "Connection": "Upgrade",
                "Content-Type": "application/json",
                "Upgrade": "tcp"
            ],
            body: Self.json([
                "Detach": false,
                "Tty": options.terminal
            ])
        )
        let consume: (Data) throws -> Void = { chunk in
            try decoder.append(chunk) { data, isStandardError in
                if let streamingOutput {
                    try streamingOutput(data, isStandardError)
                } else if isStandardError {
                    standardError.append(data)
                } else {
                    standardOutput.append(data)
                }
            }
        }
        if options.interactive {
            guard let transport = transport as? any DockerEngineHijackTransport else {
                throw DockerCLIError.unsupported("exec --interactive transport")
            }
            _ = try transport.hijack(
                startRequest,
                input: standardInput ?? Data(),
                inputFileDescriptor: standardInputFileDescriptor,
                maximumBodyBytes: nil,
                onBody: consume
            )
        } else {
            _ = try transport.send(startRequest, maximumBodyBytes: nil, onBody: consume)
        }
        try decoder.finish()
        return (standardOutput, standardError)
    }
}

struct DockerBuildOptions: Equatable {
    var dockerfile = "Dockerfile"
    var tags: [String] = []
    var target: String?
    var buildArguments: [String: String] = [:]
    var labels: [String: String] = [:]
    var noCache = false
    var pull = false
    var platform: String?
    var context = "."

    init(arguments: [String]) throws {
        var positional: [String] = []
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            if try consumeNamedOption(argument, arguments: arguments, index: &index) {
                index += 1
                continue
            }
            if try consumeFlag(argument) || consumeInlineOption(argument) {
                index += 1
                continue
            }
            guard !argument.hasPrefix("-") else {
                throw DockerCLIError.invalidArguments("unsupported build option \(argument)")
            }
            positional.append(argument)
            index += 1
        }
        guard positional.count == 1 else {
            throw DockerCLIError.invalidArguments("build requires one context directory")
        }
        context = positional[0]
    }

    private mutating func consumeNamedOption(
        _ option: String,
        arguments: [String],
        index: inout Int
    ) throws -> Bool {
        switch option {
        case "-f", "--file": dockerfile = try Self.value(arguments, &index, for: option)
        case "-t", "--tag": try tags.append(Self.value(arguments, &index, for: option))
        case "--target": target = try Self.value(arguments, &index, for: option)
        case "--build-arg":
            try Self.storePair(
                Self.value(arguments, &index, for: option),
                in: &buildArguments,
                name: option
            )
        case "--label":
            try Self.storePair(
                Self.value(arguments, &index, for: option),
                in: &labels,
                name: option
            )
        case "--platform": platform = try Self.value(arguments, &index, for: option)
        case "--progress":
            let value = try Self.value(arguments, &index, for: option)
            guard value == "plain" else {
                throw DockerCLIError.invalidArguments(
                    "the Apple build adapter supports only --progress plain"
                )
            }
        case "--cache-from", "--security-opt":
            throw DockerCLIError.unsupported("build \(option)")
        default: return false
        }
        return true
    }

    private mutating func consumeFlag(_ option: String) -> Bool {
        switch option {
        case "--no-cache": noCache = true
        case "--pull": pull = true
        default: return false
        }
        return true
    }

    private mutating func consumeInlineOption(_ option: String) throws -> Bool {
        if option.hasPrefix("--build-arg=") {
            try Self.storePair(String(option.dropFirst("--build-arg=".count)), in: &buildArguments, name: "--build-arg")
        } else if option.hasPrefix("--label=") {
            try Self.storePair(String(option.dropFirst("--label=".count)), in: &labels, name: "--label")
        } else if option.hasPrefix("--platform=") {
            platform = String(option.dropFirst("--platform=".count))
        } else if option == "--progress=plain" {
            return true
        } else if option.hasPrefix("--cache-from=") || option.hasPrefix("--security-opt=") {
            throw DockerCLIError.unsupported("build \(option.split(separator: "=")[0])")
        } else if option.hasPrefix("--progress=") {
            throw DockerCLIError.invalidArguments(
                "the Apple build adapter supports only --progress plain"
            )
        } else {
            return false
        }
        return true
    }

    private static func storePair(
        _ value: String,
        in dictionary: inout [String: String],
        name: String
    ) throws {
        let pair = try Self.pair(value, name: name)
        dictionary[pair.0] = pair.1
    }

    func query() throws -> [(String, String)] {
        var result = try [
            ("dockerfile", archivedDockerfile), ("buildargs", Self.jsonString(buildArguments)),
            ("labels", Self.jsonString(labels)),
            ("nocache", noCache ? "true" : "false"),
            ("pull", pull ? "true" : "false")
        ]
        result.append(contentsOf: tags.map { ("t", $0) })
        if let target {
            result.append(("target", target))
        }
        if let platform {
            result.append(("platform", platform))
        }
        return result
    }

    func archive() throws -> Data {
        let contextURL = URL(fileURLWithPath: context).standardizedFileURL
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: contextURL.path, isDirectory: &isDirectory),
              isDirectory.boolValue
        else {
            throw DockerCLIError.invalidArguments("build context is not a directory: \(context)")
        }
        let dockerfileURL = resolvedDockerfileURL(contextURL: contextURL)
        guard FileManager.default.fileExists(atPath: dockerfileURL.path) else {
            throw DockerCLIError.invalidArguments("Dockerfile does not exist: \(dockerfile)")
        }
        // macOS bsdtar otherwise serializes Finder/provenance xattrs as binary
        // PAX values. Build contexts need file contents and modes, not opaque
        // host metadata that cannot be represented as portable PAX text.
        let entries = try archiveEntries(
            contextURL: contextURL,
            dockerfileURL: dockerfileURL
        )
        let archiveURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-build-\(UUID().uuidString).tar")
        defer { try? FileManager.default.removeItem(at: archiveURL) }
        try writeArchive(entries, from: contextURL, to: archiveURL)
        if !dockerfileURL.path.hasPrefix(contextURL.path + "/") {
            try appendDockerfile(dockerfileURL, to: archiveURL)
        }
        return try Data(contentsOf: archiveURL)
    }

    private func writeArchive(_ entries: [String], from root: URL, to archive: URL) throws {
        let result = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: [
                "--no-xattrs", "--no-recursion", "-cf", archive.path, "-C", root.path,
                "--null", "-T", "-"
            ],
            environment: [
                "COPYFILE_DISABLE": "1",
                "PATH": "/usr/bin:/bin"
            ],
            input: Data(entries.joined(separator: "\0").utf8)
        )
        guard result.exitCode == 0 else {
            throw DockerCLIError.invalidArguments(
                String(data: result.standardError, encoding: .utf8) ?? "could not archive build context"
            )
        }
    }

    private func appendDockerfile(_ dockerfile: URL, to archive: URL) throws {
        let result = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: [
                "--no-xattrs", "-rf", archive.path, "-C",
                dockerfile.deletingLastPathComponent().path,
                dockerfile.lastPathComponent
            ],
            environment: ["COPYFILE_DISABLE": "1", "PATH": "/usr/bin:/bin"]
        )
        guard result.exitCode == 0 else {
            throw DockerCLIError.invalidArguments(
                String(data: result.standardError, encoding: .utf8)
                    ?? "could not append Dockerfile to build context"
            )
        }
    }

    private func archiveEntries(
        contextURL: URL,
        dockerfileURL: URL
    ) throws -> [String] {
        let ignoreURL = dockerfileIgnoreURL(
            contextURL: contextURL,
            dockerfileURL: dockerfileURL
        )
        let matcher = try DockerIgnoreMatcher(
            contents: (try? String(contentsOf: ignoreURL, encoding: .utf8)) ?? ""
        )
        let dockerfilePath = relativePath(dockerfileURL, within: contextURL)
        let ignorePath = relativePath(ignoreURL, within: contextURL)
        let enumerator = FileManager.default.enumerator(
            at: contextURL,
            includingPropertiesForKeys: nil,
            options: [.skipsPackageDescendants]
        )
        var entries: [String] = []
        while let url = enumerator?.nextObject() as? URL {
            guard let path = relativePath(url, within: contextURL) else {
                continue
            }
            if path == ".dockerignore" || path == ignorePath {
                continue
            }
            if path == dockerfilePath || matcher.includes(path) {
                entries.append(path)
            }
        }
        return entries.sorted()
    }

    private func dockerfileIgnoreURL(
        contextURL: URL,
        dockerfileURL: URL
    ) -> URL {
        let specific = dockerfileURL.appendingPathExtension("dockerignore")
        if FileManager.default.fileExists(atPath: specific.path) {
            return specific
        }
        return contextURL.appendingPathComponent(".dockerignore")
    }

    private func relativePath(_ url: URL, within root: URL) -> String? {
        let lexicalURL = url.standardizedFileURL
        let lexicalRoot = root.standardizedFileURL
        guard lexicalURL.path.hasPrefix(lexicalRoot.path + "/") else {
            return nil
        }
        return String(lexicalURL.path.dropFirst(lexicalRoot.path.count + 1))
    }

    private var archivedDockerfile: String {
        let contextURL = URL(fileURLWithPath: context).standardizedFileURL
        let dockerfileURL = resolvedDockerfileURL(contextURL: contextURL)
        if dockerfileURL.path.hasPrefix(contextURL.path + "/") {
            return String(dockerfileURL.path.dropFirst(contextURL.path.count + 1))
        }
        return dockerfileURL.lastPathComponent
    }

    private func resolvedDockerfileURL(contextURL: URL) -> URL {
        if dockerfile == "Dockerfile" {
            return contextURL.appendingPathComponent(dockerfile).standardizedFileURL
        }
        return URL(
            fileURLWithPath: dockerfile,
            relativeTo: URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        ).standardizedFileURL
    }

    private static func value(_ arguments: [String], _ index: inout Int, for option: String) throws
        -> String
    {
        index += 1
        guard index < arguments.count else {
            throw DockerCLIError.invalidArguments("\(option) requires a value")
        }
        return arguments[index]
    }

    private static func pair(_ value: String, name: String) throws -> (String, String) {
        let parts = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).map(
            String.init
        )
        guard parts.count == 2, !parts[0].isEmpty else {
            throw DockerCLIError.invalidArguments("\(name) requires NAME=VALUE")
        }
        return (parts[0], parts[1])
    }

    private static func jsonString(_ value: Any) throws -> String {
        guard
            let text = try String(
                data: JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
                encoding: .utf8
            )
        else {
            throw DockerCLIError.malformedResponse("could not encode build options")
        }
        return text
    }
}

struct DockerRunOptions {
    var name: String?
    var detach = false
    var image = ""
    var command: [String] = []
    var environment: [String] = []
    var labels: [String: String] = [:]
    var mounts: [[String: Any]] = []
    var ports: [String: [[String: String]]] = [:]
    var user: String?
    var entrypoint: String?
    var initProcess = false
    var privileged = false
    var capabilities: [String] = []
    var securityOptions: [String] = []
    var network: String?
    var networkAliases: [String] = []
    var autoRemove = false

    init(arguments: [String]) throws {
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            if !argument.hasPrefix("-") {
                image = argument
                command = Array(arguments.dropFirst(index + 1))
                break
            }
            if try consumeFlag(argument)
                || consumeIdentityOption(argument, arguments: arguments, index: &index)
                || consumeResourceOption(argument, arguments: arguments, index: &index)
            {
                index += 1
                continue
            }
            if argument != "--sig-proxy=false" {
                throw DockerCLIError.invalidArguments("unsupported run option \(argument)")
            }
            index += 1
        }
        guard !image.isEmpty else { throw DockerCLIError.invalidArguments("run requires an image") }
    }

    private mutating func consumeFlag(_ option: String) -> Bool {
        switch option {
        case "-d", "--detach": detach = true
        case "--init": initProcess = true
        case "--privileged": privileged = true
        case "--rm": autoRemove = true
        default: return false
        }
        return true
    }

    private mutating func consumeIdentityOption(
        _ option: String,
        arguments: [String],
        index: inout Int
    ) throws -> Bool {
        switch option {
        case "--name": name = try Self.value(arguments, &index, for: option)
        case "-a", "--attach": _ = try Self.value(arguments, &index, for: option)
        case "-e", "--env": try environment.append(Self.value(arguments, &index, for: option))
        case "-l", "--label":
            let pair = try Self.pair(Self.value(arguments, &index, for: option), name: option)
            labels[pair.0] = pair.1
        case "-u", "--user": user = try Self.value(arguments, &index, for: option)
        default: return false
        }
        return true
    }

    private mutating func consumeResourceOption(
        _ option: String,
        arguments: [String],
        index: inout Int
    ) throws -> Bool {
        switch option {
        case "-p", "--publish": try addPort(Self.value(arguments, &index, for: option))
        case "--mount": try mounts.append(Self.mount(Self.value(arguments, &index, for: option)))
        case "--entrypoint": entrypoint = try Self.value(arguments, &index, for: option)
        case "--cap-add": try capabilities.append(Self.value(arguments, &index, for: option))
        case "--security-opt": try securityOptions.append(Self.value(arguments, &index, for: option))
        case "--network": network = try Self.value(arguments, &index, for: option)
        case "--network-alias":
            try networkAliases.append(Self.value(arguments, &index, for: option))
        default: return false
        }
        return true
    }

    var createRequest: [String: Any] {
        var request: [String: Any] = [
            "AttachStderr": !detach,
            "AttachStdout": !detach,
            "Cmd": command,
            "Env": environment,
            "HostConfig": [
                "AutoRemove": autoRemove,
                "CapAdd": capabilities,
                "Init": initProcess,
                "Mounts": mounts,
                "PortBindings": ports,
                "Privileged": privileged,
                "SecurityOpt": securityOptions
            ],
            "Image": image,
            "Labels": labels,
            "OpenStdin": false,
            "Tty": false
        ]
        if let user {
            request["User"] = user
        }
        if let entrypoint {
            request["Entrypoint"] = [entrypoint]
        }
        if let network {
            request["NetworkingConfig"] = [
                "EndpointsConfig": [network: ["Aliases": networkAliases]]
            ]
        }
        return request
    }

    private mutating func addPort(_ value: String) throws {
        let parts = value.split(separator: ":").map(String.init)
        guard parts.count == 2 || parts.count == 3 else {
            throw DockerCLIError.invalidArguments("invalid published port \(value)")
        }
        let hostIP = parts.count == 3 ? parts[0] : "0.0.0.0"
        let hostPort = parts[parts.count - 2]
        let containerPort = parts[parts.count - 1] + "/tcp"
        ports[containerPort, default: []].append(["HostIp": hostIP, "HostPort": hostPort])
    }

    private static func mount(_ value: String) throws -> [String: Any] {
        let pairs = Dictionary(
            uniqueKeysWithValues: value.split(separator: ",").map { component -> (String, String) in
                let pair = component.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                return (String(pair[0]), pair.count == 2 ? String(pair[1]) : "true")
            }
        )
        guard let type = pairs["type"],
              let target = pairs["target"] ?? pairs["dst"] ?? pairs["destination"]
        else {
            throw DockerCLIError.invalidArguments("mount requires type and target")
        }
        var result: [String: Any] = ["Type": type, "Target": target]
        if let source = pairs["source"] ?? pairs["src"] {
            result["Source"] = source
        }
        if pairs["readonly"] == "true" || pairs["ro"] == "true" {
            result["ReadOnly"] = true
        }
        if let consistency = pairs["consistency"] {
            result["Consistency"] = consistency
        }
        return result
    }

    private static func value(_ arguments: [String], _ index: inout Int, for option: String) throws
        -> String
    {
        index += 1
        guard index < arguments.count else {
            throw DockerCLIError.invalidArguments("\(option) requires a value")
        }
        return arguments[index]
    }

    private static func pair(_ value: String, name: String) throws -> (String, String) {
        let parts = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).map(
            String.init
        )
        guard parts.count == 2, !parts[0].isEmpty else {
            throw DockerCLIError.invalidArguments("\(name) requires NAME=VALUE")
        }
        return (parts[0], parts[1])
    }
}

struct DockerExecOptions {
    var interactive = false
    var terminal = false
    var user: String?
    var workingDirectory: String?
    var environment: [String] = []
    var container = ""
    var command: [String] = []

    init(arguments: [String]) throws {
        var index = 0
        while index < arguments.count {
            switch arguments[index] {
            case "-i", "--interactive": interactive = true
            case "-t", "--tty": terminal = true
            case "-u", "--user": user = try Self.value(arguments, &index, for: arguments[index])
            case "-w", "--workdir":
                workingDirectory = try Self.value(arguments, &index, for: arguments[index])
            case "-e", "--env":
                try environment.append(Self.value(arguments, &index, for: arguments[index]))
            default:
                guard !arguments[index].hasPrefix("-") else {
                    throw DockerCLIError.invalidArguments("unsupported exec option \(arguments[index])")
                }
                container = arguments[index]
                command = Array(arguments.dropFirst(index + 1))
                index = arguments.count
                continue
            }
            index += 1
        }
        guard !container.isEmpty, !command.isEmpty else {
            throw DockerCLIError.invalidArguments("exec requires a container and command")
        }
    }

    var createRequest: [String: Any] {
        var result: [String: Any] = [
            "AttachStderr": true,
            "AttachStdin": interactive,
            "AttachStdout": true,
            "Cmd": command,
            "Env": environment,
            "Tty": terminal
        ]
        if let user {
            result["User"] = user
        }
        if let workingDirectory {
            result["WorkingDir"] = workingDirectory
        }
        return result
    }

    private static func value(_ arguments: [String], _ index: inout Int, for option: String) throws
        -> String
    {
        index += 1
        guard index < arguments.count else {
            throw DockerCLIError.invalidArguments("\(option) requires a value")
        }
        return arguments[index]
    }
}

struct DockerMultiplexedStreamDecoder {
    private var buffer = Data()
    private let terminal: Bool

    init(terminal: Bool = false) {
        self.terminal = terminal
    }

    mutating func append(
        _ data: Data,
        handler: (Data, Bool) throws -> Void
    ) throws {
        if terminal {
            try handler(data, false)
            return
        }
        buffer.append(data)
        while buffer.count >= 8 {
            let channel = buffer[buffer.startIndex]
            let lengthBytes = buffer[
                buffer.startIndex.advanced(by: 4) ..< buffer.startIndex.advanced(by: 8)
            ]
            let length = lengthBytes.reduce(0) { ($0 << 8) | Int($1) }
            guard length <= 64 * 1024 * 1024 else {
                throw DockerCLIError.malformedResponse("stream frame is too large")
            }
            guard buffer.count >= 8 + length else { return }
            let start = buffer.startIndex.advanced(by: 8)
            try handler(Data(buffer[start ..< start.advanced(by: length)]), channel == 2)
            buffer.removeFirst(8 + length)
        }
    }

    func finish() throws {
        guard terminal || buffer.isEmpty else {
            throw DockerCLIError.malformedResponse("truncated stream frame")
        }
    }
}
