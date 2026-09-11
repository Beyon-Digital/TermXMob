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
        .executableTarget(name: "TermxVirtualDisplay")
    ]
)
