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

import PackageDescription

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
        .executable(name: "devcontainer-compose", targets: ["DevContainerComposeCLI"])
    ],
    dependencies: [
        .package(
            url: "https://github.com/stephenlclarke/container-engine-api.git",
            revision: "5e6e24d017691596783515285e1ff56d29701235"
        ),
        .package(
            url: "https://github.com/stephenlclarke/container.git",
            revision: "b62df248d324883ddd64d4de1ed013230476a235"
        ),
        .package(
            url: "https://github.com/stephenlclarke/containerization.git",
            revision: "7f62f5b940630811573a34f70cdd6f3fa11d014d"
        ),
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.5.0"),
        .package(url: "https://github.com/apple/swift-collections.git", from: "1.1.0"),
        .package(url: "https://github.com/apple/swift-log.git", from: "1.6.4"),
        .package(url: "https://github.com/apple/swift-nio.git", from: "2.80.0"),
        .package(
            url: "https://github.com/stephenlclarke/swift-nio-ssl.git",
            revision: "a9d648535c62e640d1df258a70c9117a8ddea43e"
        ),
        .package(url: "https://github.com/swiftlang/swift-docc-plugin.git", from: "1.1.0")
    ],
    targets: [
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
            dependencies: [
                "DevContainerModel",
                .product(name: "ContainerizationOS", package: "containerization")
            ]
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
                .product(name: "ContainerResource", package: "container"),
                .product(name: "ContainerizationOS", package: "containerization"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio"),
                .product(name: "SocketForwarder", package: "container")
            ]
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
                "DevContainerDockerAPI",
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
            ]
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
                "DevContainerCLI",
                "DevContainerCore",
                "DevContainerModel",
                "DevContainerState"
            ]
        ),
        .testTarget(
            name: "DevContainerModelTests",
            dependencies: ["DevContainerModel"]
        ),
        .testTarget(
            name: "DevContainerProcessTests",
            dependencies: ["DevContainerProcess"]
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
                "DevContainerCore",
                "DevContainerModel",
                "DevContainerState",
                "DevContainerTestSupport"
            ]
        ),
        .testTarget(
            name: "DevContainerDockerAPITests",
            dependencies: [
                "DevContainerCore",
                "DevContainerDockerAPI",
                "DevContainerModel",
                "DevContainerState",
                "DevContainerTestSupport"
            ]
        ),
        .testTarget(
            name: "DevContainerComposeProviderTests",
            dependencies: [
                "DevContainerComposeProvider",
                "DevContainerModel",
                "DevContainerRuntimeSPI"
            ]
        ),
        .testTarget(
            name: "DevContainerComposeCLITests",
            dependencies: [
                "DevContainerComposeCLI",
                "DevContainerModel",
                "DevContainerState"
            ]
        ),
        .testTarget(
            name: "DevContainerAppleRuntimeTests",
            dependencies: [
                "DevContainerAppleRuntime",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerState",
                .product(name: "ContainerEngineRuntimeSPI", package: "container-engine-api"),
                .product(name: "ContainerAPIClient", package: "container"),
                .product(name: "ContainerResource", package: "container"),
                .product(name: "Containerization", package: "containerization"),
                .product(name: "ContainerizationOS", package: "containerization"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio")
            ]
        ),
        .testTarget(
            name: "DevContainerServiceTests",
            dependencies: [
                "DevContainerDockerAPI",
                "DevContainerModel",
                "DevContainerRuntimeSPI",
                "DevContainerService",
                "DevContainerTestSupport",
                .product(name: "ContainerEngineProviderSession", package: "container-engine-api"),
                .product(name: "ContainerEngineRuntimeSPI", package: "container-engine-api"),
                .product(name: "ContainerEngineWire", package: "container-engine-api"),
                .product(name: "Logging", package: "swift-log")
            ]
        )
    ],
    swiftLanguageModes: [.v6]
)
