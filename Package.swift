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
        .package(url: "https://github.com/apple/container.git", exact: "1.1.0"),
        .package(url: "https://github.com/apple/containerization.git", exact: "0.35.0"),
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.5.0"),
        .package(url: "https://github.com/apple/swift-collections.git", from: "1.1.0"),
        .package(url: "https://github.com/apple/swift-log.git", from: "1.6.4"),
        .package(url: "https://github.com/apple/swift-nio.git", from: "2.80.0"),
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
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOHTTP1", package: "swift-nio")
            ]
        ),
        .target(
            name: "DevContainerAppleRuntime",
            dependencies: [
                "DevContainerModel",
                "DevContainerRuntimeSPI",
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
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
                .product(name: "DequeModule", package: "swift-collections"),
                .product(name: "Logging", package: "swift-log"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOFoundationCompat", package: "swift-nio"),
                .product(name: "NIOHTTP1", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio")
            ]
        ),
        .executableTarget(
            name: "DevContainerCLI",
            dependencies: [
                "DevContainerComposeProvider",
                "DevContainerCore",
                "DevContainerModel",
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
            name: "DevContainerStateTests",
            dependencies: [
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
                "DevContainerDockerAPI",
                "DevContainerModel",
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
                .product(name: "ContainerAPIClient", package: "container"),
                .product(name: "ContainerResource", package: "container"),
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
                .product(name: "Logging", package: "swift-log")
            ]
        )
    ],
    swiftLanguageModes: [.v6]
)
