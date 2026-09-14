// swift-tools-version: 6.1
import PackageDescription

let package = Package(
    name: "SecretaryApp",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .executable(name: "SecretaryApp", targets: ["SecretaryApp"]),
        .library(name: "SecretaryClient", targets: ["SecretaryClient"]),
        .library(name: "SecretaryContract", targets: ["SecretaryContract"]),
    ],
    targets: [
        .executableTarget(
            name: "SecretaryApp",
            dependencies: ["SecretaryClient", "SecretaryContract"],
            path: "SecretaryApp/App"
        ),
        .target(
            name: "SecretaryClient",
            dependencies: ["SecretaryContract"],
            path: "SecretaryApp/Client"
        ),
        .target(
            name: "SecretaryContract",
            path: "SecretaryApp/Contract"
        ),
        .testTarget(
            name: "SecretaryAppTests",
            dependencies: ["SecretaryApp", "SecretaryClient", "SecretaryContract"]
        ),
        .testTarget(
            name: "SecretaryContractTests",
            dependencies: ["SecretaryContract"]
        ),
        .testTarget(
            name: "SecretaryClientTests",
            dependencies: ["SecretaryClient", "SecretaryContract"]
        ),
    ]
)
