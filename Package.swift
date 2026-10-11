// swift-tools-version: 6.2
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

import Foundation
import PackageDescription

let runtimeProfile = ProcessInfo.processInfo.environment[
    "DEVCONTAINER_RUNTIME_PROFILE"
] ?? "enhanced"
let enhancedRuntime: Bool = {
    switch runtimeProfile {
    case "enhanced":
        true
    case "stock":
        false
    default:
        fatalError(
            "DEVCONTAINER_RUNTIME_PROFILE must be 'stock' or 'enhanced'"
        )
    }
}()

let runtimeSwiftSettings: [SwiftSetting] = enhancedRuntime
    ? [.define("DEVCONTAINER_ENHANCED_RUNTIME")]
    : []

private func dependency(
    name: String,
    environmentVariable: String,
    url: String,
    revision: String
) -> Package.Dependency {
    if let path = ProcessInfo.processInfo.environment[environmentVariable],
       !path.isEmpty
    {
        return .package(name: name, path: path)
    }
    return .package(url: url, revision: revision)
}

private func runtimeDependency(
    name: String,
    environmentVariable: String,
    stockURL: String,
    stockVersion: Version,
    enhancedURL: String,
    enhancedRevision: String
) -> Package.Dependency {
    if let path = ProcessInfo.processInfo.environment[environmentVariable],
       !path.isEmpty
    {
        return .package(name: name, path: path)
    }
    if enhancedRuntime {
        return .package(url: enhancedURL, revision: enhancedRevision)
    }
    return .package(url: stockURL, exact: stockVersion)
}

private func runtimeTLSDependency(
    stockURL: String,
    stockVersion: Version,
    enhancedURL: String,
    enhancedRevision: String
) -> Package.Dependency {
    if enhancedRuntime {
        return .package(url: enhancedURL, revision: enhancedRevision)
    }
    return .package(url: stockURL, exact: stockVersion)
}

let package = Package(
    name: "devcontainer",
    platforms: [
        .macOS(.v15)
    ],
    products: [
        .library(name: "DevContainerModel", targets: ["DevContainerModel"]),
        .library(name: "DevContainerProcess", targets: ["DevContainerProcess"]),
        .library(name: "DevContainerRuntimeSPI", targets: ["DevContainerRuntimeSPI"]),
        .library(name: "DevContainerState", targets: ["DevContainerState"]),
        .library(name: "DevContainerCore", targets: ["DevContainerCore"]),
        .library(name: "DevContainerDockerAPI", targets: ["DevContainerDockerAPI"]),
        .library(name: "DevContainerAppleRuntime", targets: ["DevContainerAppleRuntime"]),
        .library(name: "DevContainerComposeProvider", targets: ["DevContainerComposeProvider"]),
        .library(name: "DevContainerTestSupport", targets: ["DevContainerTestSupport"]),
        .executable(name: "devcontainer", targets: ["DevContainerCLI"]),
        .executable(name: "devcontainer-engine", targets: ["DevContainerService"]),
        .executable(name: "devcontainer-compose", targets: ["DevContainerComposeCLI"]),
        .executable(name: "devcontainer-docker", targets: ["DevContainerDockerCLI"])
    ],
    dependencies: [
        dependency(
            name: "container-engine-api",
            environmentVariable: "CONTAINER_ENGINE_API_PACKAGE_PATH",
            url: "https://github.com/stephenlclarke/container-engine-api.git",
            revision: enhancedRuntime
                ? "6e8c932fc8755a4b922fd239426e9029be0554e0"
                : "36de2d66d4a1f7eb48c08d94cf1444f93d5f9c77"
        ),
        dependency(
            name: "container",
            environmentVariable: "CONTAINER_PACKAGE_PATH",
            url: "https://github.com/stephenlclarke/container.git",
            revision: enhancedRuntime
                ? "38a53cb6ba8f48413534c3c4112f72489ecbecc6"
                : "aad0c75555d8ccce45aea01d7e1558eb7dee408e"
        ),
        runtimeDependency(
            name: "containerization",
            environmentVariable: "CONTAINERIZATION_PACKAGE_PATH",
            stockURL: "https://github.com/apple/containerization.git",
            stockVersion: "0.45.0",
            enhancedURL: "https://github.com/stephenlclarke/containerization.git",
            enhancedRevision: "c0607ac9aa5b759141506fbd8fc01f423d433f1e"
        ),
        runtimeTLSDependency(
            stockURL: "https://github.com/apple/swift-nio-ssl.git",
            stockVersion: "2.37.4",
            enhancedURL: "https://github.com/stephenlclarke/swift-nio-ssl.git",
            enhancedRevision: "aee34db2144717ddce7bd145e45cf4fb9dab73fb"
        ),
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.5.0"),
        .package(url: "https://github.com/apple/swift-collections.git", from: "1.1.0"),
        .package(url: "https://github.com/apple/swift-log.git", from: "1.6.4"),
        .package(url: "https://github.com/apple/swift-nio.git", from: "2.80.0"),
        .package(url: "https://github.com/swiftlang/swift-docc-plugin.git", from: "1.1.0")
    ],
    targets: [
        .target(
            name: "DevContainerDockerClient",
            dependencies: [
                "DevContainerProcess",
                .product(name: "ContainerEngineWire", package: "container-engine-api"),
                .product(name: "ContainerUnixHTTPClient", package: "container-engine-api")
            ]
        ),
        .executableTarget(
            name: "DevContainerDockerCLI",
            dependencies: ["DevContainerDockerClient", "DevContainerCore", "DevContainerModel"]
        ),
        .testTarget(
            name: "DevContainerDockerClientTests",
            dependencies: [
                "DevContainerDockerClient", "DevContainerDockerCLI", "DevContainerTestStorage",
                "DevContainerProcess", "DevContainerModel",
                .product(name: "ContainerEngineWire", package: "container-engine-api"),
                .product(name: "ContainerUnixHTTPServer", package: "container-engine-api"),
                .product(name: "Logging", package: "swift-log")
            ]
        ),
        .target(name: "DevContainerTestStorage"),
        .executableTarget(
            name: "DevContainerVersionGenerator",
            path: "Tools/version-generator"
        ),
        .plugin(
            name: "GenerateDevContainerVersion",
            capability: .buildTool(),
            dependencies: ["DevContainerVersionGenerator"]
        ),
        .systemLibrary(name: "CSQLite"),
        .target(
            name: "DevContainerModel",
            plugins: ["GenerateDevContainerVersion"]
        ),
        .target(
            name: "DevContainerRuntimeSPI",
            dependencies: ["DevContainerModel"]
        ),
        .target(
            name: "DevContainerProcess",
            dependencies: ["DevContainerModel"]
        ),
        .target(
            name: "DevContainerState",
            dependencies: [
                "CSQLite",
                "DevContainerModel",
                "DevContainerRuntimeSPI"
            ]
        ),
        .target(
            name: "DevContainerCore",
            dependencies: [
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerState"
            ]
        ),
        .target(
            name: "DevContainerDockerAPI",
            dependencies: [
                "DevContainerCore",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                .product(name: "ContainerEngineRouter", package: "container-engine-api"),
                .product(name: "ContainerEngineWire", package: "container-engine-api"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOHTTP1", package: "swift-nio")
            ]
        ),
        .target(
            name: "DevContainerAppleRuntime",
            dependencies: [
                "DevContainerModel",
                "DevContainerProcess",
                "DevContainerRuntimeSPI",
                .product(name: "ContainerEngineRuntimeSPI", package: "container-engine-api"),
                .product(name: "ContainerAPIClient", package: "container"),
                .product(name: "ContainerBuild", package: "container"),
                .product(name: "ContainerNetworkClient", package: "container"),
                .product(name: "ContainerPersistence", package: "container"),
                .product(name: "ContainerResource", package: "container"),
                .product(name: "ContainerXPC", package: "container"),
                .product(name: "Containerization", package: "containerization"),
                .product(name: "ContainerizationOCI", package: "containerization"),
                .product(name: "ContainerizationOS", package: "containerization"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio"),
                .product(name: "SocketForwarder", package: "container")
            ],
            swiftSettings: runtimeSwiftSettings
        ),
        .target(
            name: "DevContainerComposeProvider",
            dependencies: [
                "DevContainerModel",
                "DevContainerProcess",
                "DevContainerRuntimeSPI"
            ]
        ),
        .target(
            name: "DevContainerTestSupport",
            dependencies: [
                "DevContainerModel",
                "DevContainerRuntimeSPI"
            ]
        ),
        .executableTarget(
            name: "DevContainerService",
            dependencies: [
                "DevContainerAppleRuntime",
                "DevContainerCore",
                "DevContainerDockerAPI",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerState",
                .product(name: "ContainerEngineGateway", package: "container-engine-api"),
                .product(name: "ContainerEngineRuntimeSPI", package: "container-engine-api"),
                .product(name: "ContainerEngineProviderSession", package: "container-engine-api"),
                .product(name: "ContainerUnixHTTPServer", package: "container-engine-api"),
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
                .product(name: "Logging", package: "swift-log")
            ],
            swiftSettings: runtimeSwiftSettings
        ),
        .executableTarget(
            name: "DevContainerCLI",
            dependencies: [
                "DevContainerComposeProvider",
                "DevContainerCore",
                "DevContainerModel",
                "DevContainerProcess",
                "DevContainerState",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
                .product(name: "Logging", package: "swift-log")
            ]
        ),
        .executableTarget(
            name: "DevContainerComposeCLI",
            dependencies: [
                "DevContainerComposeProvider",
                "DevContainerCore",
                "DevContainerModel",
                "DevContainerProcess",
                "DevContainerState"
            ]
        ),
        .testTarget(
            name: "DevContainerCLITests",
            dependencies: [
                "DevContainerTestStorage",
                "DevContainerCLI",
                "DevContainerCore",
                "DevContainerModel",
                "DevContainerProcess",
                "DevContainerState"
            ]
        ),
        .testTarget(
            name: "DevContainerModelTests",
            dependencies: ["DevContainerModel", "DevContainerTestStorage"]
        ),
        .testTarget(
            name: "DevContainerProcessTests",
            dependencies: ["DevContainerProcess", "DevContainerModel", "DevContainerTestStorage", "DevContainerProcessProbe"]
        ),
        .executableTarget(
            name: "DevContainerProcessProbe",
            dependencies: ["DevContainerProcess"],
            path: "Tools/process-test-probe"
        ),
        .testTarget(
            name: "DevContainerStateTests",
            dependencies: [
                "CSQLite",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerState"
            ]
        ),
        .testTarget(
            name: "DevContainerCoreTests",
            dependencies: [
                "DevContainerTestStorage",
                "DevContainerCore",
                "DevContainerModel",
                "DevContainerState",
                "DevContainerTestSupport"
            ]
        ),
        .testTarget(
            name: "DevContainerDockerAPITests",
            dependencies: [
                "DevContainerTestStorage",
                "DevContainerCore",
                "DevContainerDockerAPI",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerState",
                "DevContainerTestSupport"
            ]
        ),
        .testTarget(
            name: "DevContainerComposeProviderTests",
            dependencies: [
                "DevContainerTestStorage",
                "DevContainerComposeProvider",
                "DevContainerModel",
                "DevContainerRuntimeSPI"
            ]
        ),
        .testTarget(
            name: "DevContainerComposeCLITests",
            dependencies: [
                "DevContainerTestStorage",
                "DevContainerComposeCLI",
                "DevContainerModel",
                "DevContainerState"
            ]
        ),
        .testTarget(
            name: "DevContainerAppleRuntimeTests",
            dependencies: [
                "DevContainerTestStorage",
                "DevContainerAppleRuntime",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerState",
                .product(name: "ContainerEngineRuntimeSPI", package: "container-engine-api"),
                .product(name: "ContainerAPIClient", package: "container"),
                .product(name: "ContainerResource", package: "container"),
                .product(name: "ContainerXPC", package: "container"),
                .product(name: "Containerization", package: "containerization"),
                .product(name: "ContainerPersistence", package: "container"),
                .product(name: "ContainerizationOCI", package: "containerization"),
                .product(name: "ContainerizationOS", package: "containerization"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio")
            ],
            exclude: enhancedRuntime
                ? []
                : ["AppleContainerRuntimeLoggingHandoffTests.swift"],
            swiftSettings: runtimeSwiftSettings
        ),
        .testTarget(
            name: "DevContainerServiceTests",
            dependencies: [
                "DevContainerTestStorage",
                "DevContainerProcess",
                "DevContainerState",
                "DevContainerDockerAPI",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerService",
                "DevContainerTestSupport",
                .product(name: "ContainerEngineGateway", package: "container-engine-api"),
                .product(name: "ContainerEngineProviderSession", package: "container-engine-api"),
                .product(name: "ContainerEngineRuntimeSPI", package: "container-engine-api"),
                .product(name: "ContainerEngineWire", package: "container-engine-api"),
                .product(name: "Logging", package: "swift-log")
            ],
            swiftSettings: runtimeSwiftSettings
        )
    ] + (ProcessInfo.processInfo.environment["DEVCONTAINER_HOST_INTEGRATION"] == "1" && enhancedRuntime ? [
        .testTarget(
            name: "DevContainerHostIntegrationTests",
            dependencies: [
                "DevContainerAppleRuntime",
                .product(name: "ContainerAPIClient", package: "container")
            ],
            swiftSettings: runtimeSwiftSettings
        )
    ] : []),
    swiftLanguageModes: [.v6]
)
