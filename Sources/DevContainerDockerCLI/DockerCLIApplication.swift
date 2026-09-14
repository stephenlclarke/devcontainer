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

import DevContainerCore
import DevContainerModel
import DevContainerProcess
import Foundation

public enum DockerCLIError: Error, Equatable, CustomStringConvertible {
    case invalidArguments(String)
    case unsupported(String)
    case malformedResponse(String)

    public var description: String {
        switch self {
        case let .invalidArguments(message): message
        case let .unsupported(command):
            "docker command is outside the Dev Containers compatibility scope: \(command)"
        case let .malformedResponse(message):
            "invalid engine response: \(message)"
        }
    }
}

public struct DockerCLIResult: Equatable, Sendable {
    public var standardOutput: Data
    public var standardError: Data
    public var exitCode: Int32

    public init(
        standardOutput: Data = Data(),
        standardError: Data = Data(),
        exitCode: Int32 = 0
    ) {
        self.standardOutput = standardOutput
        self.standardError = standardError
        self.exitCode = exitCode
    }
}

public final class DockerCLIApplication: @unchecked Sendable {
    public static let compatibilityVersion = "26.1.0"

    let transport: any DockerEngineTransport

    public init(transport: any DockerEngineTransport) {
        self.transport = transport
    }

    public static func configured(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) throws -> DockerCLIApplication {
        let selection = try DevContainerRuntimeSelectionResolver.resolve(
            environment: environment
        )
        let socketTransport = try UnixSocketDockerTransport(socketPath: selection.socket)
        return DockerCLIApplication(
            transport: DevContainerEngineTransport(transport: socketTransport)
        )
    }

    public static func requiresInteractiveInput(arguments: [String]) throws -> Bool {
        let commandArguments = try stripGlobalOptions(arguments)
        guard let command = commandArguments.first else {
            return false
        }
        let arguments = Array(commandArguments.dropFirst())
        switch command {
        case "exec":
            return try DockerExecOptions(arguments: arguments).interactive
        case "run":
            let options = try DockerRunOptions(arguments: arguments)
            return options.interactive && !options.detach
        default:
            return false
        }
    }

    public func run(
        arguments: [String],
        standardInput: Data? = nil,
        standardInputFileDescriptor: Int32? = nil,
        streamingOutput: ((Data, Bool) throws -> Void)? = nil
    ) throws -> DockerCLIResult {
        let arguments = try Self.stripGlobalOptions(arguments)
        guard let command = arguments.first else {
            throw DockerCLIError.invalidArguments("a Docker command is required")
        }
        let tail = Array(arguments.dropFirst())
        switch command {
        case "-v", "--version":
            return .stdout(
                "Docker version \(Self.compatibilityVersion), devcontainer Apple compatibility\n"
            )
        case "version":
            return try version(tail)
        case "info":
            return try info(tail)
        case "buildx":
            return try buildx(tail, originalArguments: arguments)
        default:
            return try runEngineCommand(
                command,
                arguments: tail,
                standardInput: standardInput,
                standardInputFileDescriptor: standardInputFileDescriptor,
                streamingOutput: streamingOutput
            )
        }
    }

    private func buildx(
        _ arguments: [String],
        originalArguments: [String]
    ) throws -> DockerCLIResult {
        guard arguments == ["version"] else {
            throw DockerCLIError.unsupported(originalArguments.joined(separator: " "))
        }
        return DockerCLIResult(
            standardError: Data(
                "docker: 'buildx' is not available; using the supported classic build path\n".utf8
            ),
            exitCode: 1
        )
    }

    private func runEngineCommand(
        _ command: String,
        arguments: [String],
        standardInput: Data?,
        standardInputFileDescriptor: Int32?,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        switch command {
        case "cp":
            try copy(arguments)
        case "image":
            try image(arguments)
        case "network":
            try network(arguments)
        case "pull":
            try pull(arguments, streamingOutput: streamingOutput)
        case "tag":
            try tag(arguments)
        case "volume":
            try volume(arguments)
        default:
            try runContainerCommand(
                command,
                arguments: arguments,
                standardInput: standardInput,
                standardInputFileDescriptor: standardInputFileDescriptor,
                streamingOutput: streamingOutput
            )
        }
    }

    private func runContainerCommand(
        _ command: String,
        arguments: [String],
        standardInput: Data?,
        standardInputFileDescriptor: Int32?,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        switch command {
        case "create":
            try createContainer(arguments)
        case "inspect":
            try inspect(arguments)
        case "ps":
            try listContainers(arguments)
        case "rm":
            try removeContainer(arguments)
        case "start":
            try lifecycle(arguments, action: "start")
        case "stop":
            try lifecycle(arguments, action: "stop")
        case "restart":
            try lifecycle(arguments, action: "restart")
        case "kill":
            try killContainer(arguments)
        case "wait":
            try waitContainer(arguments)
        default:
            try runWorkloadCommand(
                command,
                arguments: arguments,
                standardInput: standardInput,
                standardInputFileDescriptor: standardInputFileDescriptor,
                streamingOutput: streamingOutput
            )
        }
    }

    private func runWorkloadCommand(
        _ command: String,
        arguments: [String],
        standardInput: Data?,
        standardInputFileDescriptor: Int32?,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        switch command {
        case "build":
            return try build(arguments, streamingOutput: streamingOutput)
        case "run":
            return try runContainer(
                arguments,
                standardInput: standardInput,
                standardInputFileDescriptor: standardInputFileDescriptor,
                streamingOutput: streamingOutput
            )
        case "exec":
            return try exec(
                arguments,
                standardInput: standardInput,
                standardInputFileDescriptor: standardInputFileDescriptor,
                streamingOutput: streamingOutput
            )
        case "events":
            return try events(arguments, streamingOutput: streamingOutput)
        default:
            throw DockerCLIError.unsupported(command)
        }
    }

    private func version(_ arguments: [String]) throws -> DockerCLIResult {
        let format = Self.optionValue("--format", in: arguments)
        guard arguments.isEmpty || (arguments.count == 2 && format != nil)
            || (arguments.count == 1 && arguments[0].hasPrefix("--format="))
        else {
            throw DockerCLIError.invalidArguments("unsupported version options")
        }
        if let format, format != "{{.Server.Version}}" {
            throw DockerCLIError.invalidArguments("unsupported version format \(format)")
        }
        let response = try request("GET", "/version")
        let object = try Self.object(response.body)
        let version = object["Version"] as? String ?? Self.compatibilityVersion
        if format != nil {
            return .stdout("\(version)\n")
        }
        return .stdout("Docker version \(version), devcontainer Apple compatibility\n")
    }

    private func info(_ arguments: [String]) throws -> DockerCLIResult {
        let format = Self.optionValue("--format", short: "-f", in: arguments)
        guard arguments.isEmpty || (arguments.count == 2 && format != nil)
            || (arguments.count == 1 && arguments[0].hasPrefix("--format="))
        else {
            throw DockerCLIError.invalidArguments("unsupported info options")
        }
        if let format, !format.contains("Runtimes.nvidia") {
            throw DockerCLIError.invalidArguments("unsupported info format \(format)")
        }
        let response = try request("GET", "/info")
        if format != nil {
            return .stdout("\n")
        }
        return DockerCLIResult(standardOutput: response.body + Data("\n".utf8))
    }

    func inspect(_ arguments: [String]) throws -> DockerCLIResult {
        var values = arguments
        let type = Self.removeOption("--type", from: &values) ?? "container"
        let format = Self.removeOption("--format", short: "-f", from: &values)
        guard !values.isEmpty, ["container", "image", "volume"].contains(type) else {
            throw DockerCLIError.invalidArguments("inspect requires --type and at least one identifier")
        }
        var objects: [Any] = []
        for identifier in values {
            let path = switch type {
            case "container": "/containers/\(Self.path(identifier))/json"
            case "image": "/images/\(Self.path(identifier))/json"
            default: "/volumes/\(Self.path(identifier))"
            }
            try objects.append(JSONSerialization.jsonObject(with: request("GET", path).body))
        }
        if let format {
            let output = try objects.map { try Self.inspectValue($0, format: format) }
            return .stdout(output.joined(separator: "\n") + "\n")
        }
        return try DockerCLIResult(standardOutput: Self.json(objects) + Data("\n".utf8))
    }

    private static func inspectValue(_ value: Any, format: String) throws -> String {
        guard let object = value as? [String: Any] else {
            throw DockerCLIError.malformedResponse("inspect response is not an object")
        }
        switch format.replacingOccurrences(of: " ", with: "") {
        case "{{.State.Status}}":
            return ((object["State"] as? [String: Any])?["Status"] as? String) ?? ""
        case "{{.State.ExitCode}}":
            return ((object["State"] as? [String: Any])?["ExitCode"] as? NSNumber)?.stringValue ?? ""
        default:
            guard format.contains(".Config.Labels"),
                  let firstQuote = format.firstIndex(of: "\""),
                  let lastQuote = format.lastIndex(of: "\""),
                  firstQuote != lastQuote
            else {
                throw DockerCLIError.invalidArguments("unsupported inspect format \(format)")
            }
            let key = String(format[format.index(after: firstQuote) ..< lastQuote])
            let labels = (object["Config"] as? [String: Any])?["Labels"] as? [String: Any]
            return labels?[key] as? String ?? ""
        }
    }

    private func listContainers(_ arguments: [String]) throws -> DockerCLIResult {
        let options = try DockerContainerListOptions(arguments: arguments)
        let filters = try Self.jsonString(["label": options.labels])
        let path = Self.target(
            "/containers/json",
            query: [
                ("all", options.all ? "true" : "false"),
                ("filters", filters)
            ]
        )
        let containers = try Self.array(request("GET", path).body).compactMap {
            $0 as? [String: Any]
        }
        let ids = containers.compactMap { $0["Id"] as? String }
        if options.quiet || options.format == "{{.ID}}" {
            return .stdout(ids.isEmpty ? "" : ids.joined(separator: "\n") + "\n")
        }
        guard options.format == nil else {
            throw DockerCLIError.invalidArguments(
                "unsupported ps format \(options.format ?? "")"
            )
        }
        let header = "CONTAINER ID\tIMAGE\tCOMMAND\tCREATED\tSTATUS\tPORTS\tNAMES"
        let rows = containers.map { Self.containerSummary($0, noTrunc: options.noTrunc) }
        return .stdout(([header] + rows).joined(separator: "\n") + "\n")
    }

    private static func containerSummary(
        _ container: [String: Any],
        noTrunc: Bool
    ) -> String {
        let identifier = (container["Id"] as? String).map {
            noTrunc ? $0 : String($0.prefix(12))
        } ?? ""
        let image = container["Image"] as? String ?? ""
        let command = container["Command"] as? String ?? ""
        let created = (container["Created"] as? NSNumber)?.stringValue ?? ""
        let status = container["Status"] as? String ?? container["State"] as? String ?? ""
        let ports = (container["Ports"] as? [[String: Any]] ?? []).compactMap { port in
            guard let privatePort = port["PrivatePort"] as? NSNumber else {
                return nil
            }
            let type = port["Type"] as? String ?? "tcp"
            if let publicPort = port["PublicPort"] as? NSNumber {
                let address = port["IP"] as? String ?? "0.0.0.0"
                return "\(address):\(publicPort)-\(privatePort)/\(type)"
            }
            return "\(privatePort)/\(type)"
        }.joined(separator: ", ")
        let names = (container["Names"] as? [String] ?? []).map {
            $0.hasPrefix("/") ? String($0.dropFirst()) : $0
        }.joined(separator: ",")
        return [identifier, image, command, created, status, ports, names]
            .joined(separator: "\t")
    }

    private func removeContainer(_ arguments: [String]) throws -> DockerCLIResult {
        let parsed = try Self.removalArguments(arguments, resource: "container")
        for identifier in parsed.identifiers {
            let path = "/containers/\(Self.path(identifier))"
            let target = parsed.force
                ? Self.target(path, query: [("force", "true")])
                : path
            _ = try request("DELETE", target)
        }
        return .stdout(parsed.identifiers.joined(separator: "\n") + "\n")
    }

    private func lifecycle(_ arguments: [String], action: String) throws -> DockerCLIResult {
        var timeout: String?
        var identifiers: [String] = []
        var index = 0
        while index < arguments.count {
            if arguments[index] == "-t" || arguments[index] == "--time" {
                index += 1
                guard index < arguments.count else {
                    throw DockerCLIError.invalidArguments("timeout requires a value")
                }
                timeout = arguments[index]
            } else if arguments[index].hasPrefix("-") {
                throw DockerCLIError.invalidArguments("unsupported \(action) option \(arguments[index])")
            } else {
                identifiers.append(arguments[index])
            }
            index += 1
        }
        guard !identifiers.isEmpty else {
            throw DockerCLIError.invalidArguments("\(action) requires a container")
        }
        for identifier in identifiers {
            let base = "/containers/\(Self.path(identifier))/\(action)"
            let target = timeout.map { Self.target(base, query: [("t", $0)]) } ?? base
            _ = try request("POST", target)
        }
        return .stdout(identifiers.joined(separator: "\n") + "\n")
    }

    private func killContainer(_ arguments: [String]) throws -> DockerCLIResult {
        var values = arguments
        let signal = Self.removeOption("--signal", short: "-s", from: &values) ?? "SIGKILL"
        guard !values.isEmpty, values.allSatisfy({ !$0.hasPrefix("-") }) else {
            throw DockerCLIError.invalidArguments("kill requires a container")
        }
        for identifier in values {
            _ = try request(
                "POST",
                Self.target(
                    "/containers/\(Self.path(identifier))/kill",
                    query: [("signal", signal)]
                )
            )
        }
        return .stdout(values.joined(separator: "\n") + "\n")
    }

    private func waitContainer(_ arguments: [String]) throws -> DockerCLIResult {
        guard !arguments.isEmpty, arguments.allSatisfy({ !$0.hasPrefix("-") }) else {
            throw DockerCLIError.invalidArguments("wait requires a container")
        }
        var statuses: [String] = []
        for identifier in arguments {
            let response = try request("POST", "/containers/\(Self.path(identifier))/wait")
            let status = try (Self.object(response.body)["StatusCode"] as? NSNumber)?.stringValue
            guard let status else {
                throw DockerCLIError.malformedResponse("container wait response has no StatusCode")
            }
            statuses.append(status)
        }
        return .stdout(statuses.joined(separator: "\n") + "\n")
    }

    private func pull(
        _ arguments: [String],
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        guard arguments.count == 1 else {
            throw DockerCLIError.invalidArguments("pull requires one image")
        }
        let reference = Self.imageReference(arguments[0])
        let target = Self.target(
            "/images/create",
            query: reference.tag.map {
                [("fromImage", reference.name), ("tag", $0)]
            } ?? [("fromImage", reference.name)]
        )
        return try streamRequest("POST", target, streamingOutput: streamingOutput)
    }

    private func tag(_ arguments: [String]) throws -> DockerCLIResult {
        guard arguments.count == 2 else {
            throw DockerCLIError.invalidArguments("tag requires source and target images")
        }
        let destination = Self.imageReference(arguments[1])
        let query =
            destination.tag.map { [("repo", destination.name), ("tag", $0)] }
                ?? [("repo", destination.name)]
        _ = try request(
            "POST",
            Self.target("/images/\(Self.path(arguments[0]))/tag", query: query)
        )
        return DockerCLIResult()
    }

    private func volume(_ arguments: [String]) throws -> DockerCLIResult {
        guard let command = arguments.first else {
            throw DockerCLIError.invalidArguments("volume requires a command")
        }
        let values = Array(arguments.dropFirst())
        switch command {
        case "create":
            return try createVolume(values)
        case "inspect":
            return try inspect(["--type", "volume"] + values)
        case "ls", "list":
            return try listVolumes(values)
        case "rm", "remove":
            return try removeVolumes(values)
        default:
            throw DockerCLIError.unsupported("volume \(command)")
        }
    }

    private func removeVolumes(_ arguments: [String]) throws -> DockerCLIResult {
        var force = false
        var identifiers: [String] = []
        for argument in arguments {
            switch argument {
            case "-f", "--force":
                force = true
            case _ where argument.hasPrefix("-"):
                throw DockerCLIError.invalidArguments("unsupported volume rm option \(argument)")
            default:
                identifiers.append(argument)
            }
        }
        guard !identifiers.isEmpty else {
            throw DockerCLIError.invalidArguments("volume rm requires a volume")
        }
        for identifier in identifiers {
            let path = "/volumes/\(Self.path(identifier))"
            let target = force ? Self.target(path, query: [("force", "true")]) : path
            _ = try request("DELETE", target)
        }
        return .stdout(identifiers.joined(separator: "\n") + "\n")
    }

    private func events(
        _ arguments: [String],
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        let options = try DockerEventOptions(arguments: arguments)
        var query = try [("filters", Self.jsonString(options.filters))]
        if let since = options.since {
            query.append(("since", since))
        }
        if let until = options.until {
            query.append(("until", until))
        }
        let target = Self.target("/events", query: query)
        return try streamRequest("GET", target, maximumBodyBytes: nil, streamingOutput: streamingOutput)
    }

    func request(
        _ method: String,
        _ target: String,
        body: Data = Data()
    ) throws -> DockerHTTPResponse {
        try transport.send(
            DockerHTTPRequest(
                method: method,
                target: target,
                headers: body.isEmpty ? [:] : ["Content-Type": "application/json"],
                body: body
            )
        )
    }

    func streamRequest(
        _ method: String,
        _ target: String,
        body: Data = Data(),
        maximumBodyBytes: Int? = 64 * 1024 * 1024,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        var captured = Data()
        _ = try transport.send(
            DockerHTTPRequest(
                method: method,
                target: target,
                headers: body.isEmpty ? [:] : ["Content-Type": "application/json"],
                body: body
            ),
            maximumBodyBytes: maximumBodyBytes
        ) { chunk in
            if let streamingOutput {
                try streamingOutput(chunk, false)
            } else {
                captured.append(chunk)
            }
        }
        return DockerCLIResult(standardOutput: captured)
    }

    func streamFileRequest(
        _ method: String,
        _ target: String,
        bodyFile: URL,
        bodyLength: UInt64,
        maximumBodyBytes: Int? = 64 * 1024 * 1024,
        streamingOutput: ((Data, Bool) throws -> Void)?
    ) throws -> DockerCLIResult {
        guard let transport = transport as? any DockerEngineFileUploadTransport else {
            throw DockerCLIError.unsupported("file-backed build upload transport")
        }
        var captured = Data()
        _ = try transport.send(
            DockerHTTPRequest(
                method: method,
                target: target,
                headers: ["Content-Type": "application/x-tar"]
            ),
            bodyFile: bodyFile,
            bodyLength: bodyLength,
            maximumBodyBytes: maximumBodyBytes
        ) { chunk in
            if let streamingOutput {
                try streamingOutput(chunk, false)
            } else {
                captured.append(chunk)
            }
        }
        return DockerCLIResult(standardOutput: captured)
    }

    private static func stripGlobalOptions(_ arguments: [String]) throws -> [String] {
        var result = arguments
        let index = 0
        while index < result.count {
            let value = result[index]
            if value == "--host" || value == "-H" || value == "--context" || value == "--config" {
                guard index + 1 < result.count else {
                    throw DockerCLIError.invalidArguments("\(value) requires a value")
                }
                result.removeSubrange(index ... (index + 1))
            } else if value.hasPrefix("--host=") || value.hasPrefix("--context=")
                || value.hasPrefix("--config=")
            {
                result.remove(at: index)
            } else {
                break
            }
        }
        return result
    }

    private static func optionValue(
        _ long: String,
        short: String? = nil,
        in arguments: [String]
    ) -> String? {
        for (index, argument) in arguments.enumerated() {
            if argument == long || short.map({ argument == $0 }) == true,
               index + 1 < arguments.count
            {
                return arguments[index + 1]
            }
            if argument.hasPrefix(long + "=") {
                return String(argument.dropFirst(long.count + 1))
            }
        }
        return nil
    }

    private static func removeOption(
        _ name: String,
        short: String? = nil,
        from arguments: inout [String]
    ) -> String? {
        for (index, argument) in arguments.enumerated() {
            if argument == name || short.map({ argument == $0 }) == true {
                guard index + 1 < arguments.count else { return nil }
                let value = arguments[index + 1]
                arguments.removeSubrange(index ... (index + 1))
                return value
            }
            if argument.hasPrefix(name + "=") {
                arguments.remove(at: index)
                return String(argument.dropFirst(name.count + 1))
            }
        }
        return nil
    }

    static func object(_ data: Data) throws -> [String: Any] {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw DockerCLIError.malformedResponse("expected JSON object")
        }
        return object
    }

    static func array(_ data: Data) throws -> [Any] {
        guard let array = try JSONSerialization.jsonObject(with: data) as? [Any] else {
            throw DockerCLIError.malformedResponse("expected JSON array")
        }
        return array
    }

    static func json(_ value: Any) throws -> Data {
        try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    }

    private static func jsonString(_ value: Any) throws -> String {
        guard let result = try String(data: json(value), encoding: .utf8) else {
            throw DockerCLIError.malformedResponse("could not encode JSON")
        }
        return result
    }

    static func target(_ path: String, query: [(String, String)]) -> String {
        guard !query.isEmpty else { return path }
        let encoded = query.map { "\(percentEncode($0.0))=\(percentEncode($0.1))" }
            .joined(separator: "&")
        return "\(path)?\(encoded)"
    }

    static func path(_ value: String) -> String {
        percentEncode(value)
    }

    private static func percentEncode(_ value: String) -> String {
        value.addingPercentEncoding(
            withAllowedCharacters: .urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&+=?#/"))
        )
            ?? value
    }

    private static func imageReference(_ value: String) -> (name: String, tag: String?) {
        guard
            !value.contains("@"),
            let colon = value.lastIndex(of: ":"),
            value[value.index(after: colon)...].allSatisfy({ $0 != "/" }),
            !value[value.index(after: colon)...].isEmpty
        else {
            return (value, nil)
        }
        return (String(value[..<colon]), String(value[value.index(after: colon)...]))
    }
}

private struct DockerEventOptions {
    var filters: [String: [String]] = [:]
    var since: String?
    var until: String?

    init(arguments: [String]) throws {
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            switch argument {
            case "--format":
                index = try Self.valueIndex(after: index, in: arguments, option: argument)
            case "--filter":
                index = try Self.valueIndex(after: index, in: arguments, option: argument)
                try addFilter(arguments[index])
            case "--since":
                index = try Self.valueIndex(after: index, in: arguments, option: argument)
                since = try Self.nonempty(arguments[index], option: argument)
            case "--until":
                index = try Self.valueIndex(after: index, in: arguments, option: argument)
                until = try Self.nonempty(arguments[index], option: argument)
            case let option where option.hasPrefix("--since="):
                since = try Self.nonempty(
                    String(option.dropFirst("--since=".count)),
                    option: "--since"
                )
            case let option where option.hasPrefix("--until="):
                until = try Self.nonempty(
                    String(option.dropFirst("--until=".count)),
                    option: "--until"
                )
            default:
                throw DockerCLIError.invalidArguments("unsupported events option \(argument)")
            }
            index += 1
        }
    }

    private mutating func addFilter(_ value: String) throws {
        let parts = value.split(separator: "=", maxSplits: 1).map(String.init)
        guard parts.count == 2 else {
            throw DockerCLIError.invalidArguments("invalid event filter")
        }
        filters[parts[0], default: []].append(parts[1])
    }

    private static func valueIndex(
        after index: Int,
        in arguments: [String],
        option: String
    ) throws -> Int {
        let valueIndex = index + 1
        guard valueIndex < arguments.count else {
            throw DockerCLIError.invalidArguments("\(option) requires a value")
        }
        return valueIndex
    }

    private static func nonempty(_ value: String, option: String) throws -> String {
        guard !value.isEmpty else {
            throw DockerCLIError.invalidArguments("\(option) requires a value")
        }
        return value
    }
}

private struct DockerContainerListOptions {
    var all = false
    var quiet = false
    var noTrunc = false
    var labels: [String] = []
    var format: String?

    init(arguments: [String]) throws {
        var index = 0
        while index < arguments.count {
            if applyBooleanOption(arguments[index]) {
                index += 1
                continue
            }
            switch arguments[index] {
            case "--filter":
                index += 1
                guard index < arguments.count else {
                    throw DockerCLIError.invalidArguments("--filter requires a value")
                }
                guard arguments[index].hasPrefix("label=") else {
                    throw DockerCLIError.invalidArguments(
                        "unsupported ps filter \(arguments[index])"
                    )
                }
                labels.append(String(arguments[index].dropFirst("label=".count)))
            case "--format":
                index += 1
                guard index < arguments.count else {
                    throw DockerCLIError.invalidArguments("--format requires a value")
                }
                format = arguments[index]
            default:
                throw DockerCLIError.invalidArguments(
                    "unsupported ps option \(arguments[index])"
                )
            }
            index += 1
        }
    }

    private mutating func applyBooleanOption(_ option: String) -> Bool {
        switch option {
        case "-a", "--all": all = true
        case "-q", "--quiet": quiet = true
        case "--no-trunc": noTrunc = true
        case "-aq", "-qa":
            all = true
            quiet = true
        default: return false
        }
        return true
    }
}

extension DockerCLIResult {
    static func stdout(_ value: String) -> DockerCLIResult {
        DockerCLIResult(standardOutput: Data(value.utf8))
    }
}
