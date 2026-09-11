// swift-tools-version:5.8
import PackageDescription

let package = Package(
    name: "TermxCapture",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "termx-capture", targets: ["TermxCapture"]),
    ],
    targets: [
        .executableTarget(
            name: "TermxCapture"
        ),
    ]
)
