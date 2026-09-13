// swift-tools-version: 5.8
import PackageDescription

let package = Package(
    name: "TermxVirtualDisplay",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "termx-virtual-display", targets: ["TermxVirtualDisplay"])
    ],
    targets: [
        .target(
            name: "CTermxVirtualDisplay",
            path: "Sources/CTermxVirtualDisplay",
            publicHeadersPath: "include",
            linkerSettings: [
                .linkedFramework("CoreGraphics"),
                .linkedFramework("Foundation"),
            ]
        ),
        .executableTarget(
            name: "TermxVirtualDisplay",
            dependencies: ["CTermxVirtualDisplay"]
        ),
    ]
)
