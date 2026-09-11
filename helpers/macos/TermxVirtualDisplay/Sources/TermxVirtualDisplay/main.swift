import Foundation

let stubNote = "ad-hoc stub; replace with signed DriverKit helper for a real extra display"

enum CLIError: Error {
    case usage
    case io(String)
}

func usage() -> String {
    return """
    usage: termx-virtual-display create --width N --height N --dpr F --refresh N --id ID
           termx-virtual-display destroy HANDLE
    """
}

func tmpRoot() -> URL {
    let tmp = ProcessInfo.processInfo.environment["TMPDIR"] ?? NSTemporaryDirectory()
    return URL(fileURLWithPath: tmp, isDirectory: true)
        .appendingPathComponent("termx-virtual-displays", isDirectory: true)
}

func ensureStore() throws -> URL {
    let dir = tmpRoot()
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
}

func fileURL(for id: String) throws -> URL {
    let safe = (id as NSString).lastPathComponent
    return try ensureStore().appendingPathComponent("\(safe).json")
}

func parseCreate(_ args: [String]) throws -> (width: Int, height: Int, dpr: Double, refresh: Int, id: String) {
    var width: Int?
    var height: Int?
    var dpr: Double = 1.0
    var refresh: Int = 60
    var id: String?
    var i = 0
    while i < args.count {
        let key = args[i]
        let value = i + 1 < args.count ? args[i + 1] : nil
        switch key {
        case "--width":
            guard let value, let parsed = Int(value) else { throw CLIError.usage }
            width = parsed
            i += 2
        case "--height":
            guard let value, let parsed = Int(value) else { throw CLIError.usage }
            height = parsed
            i += 2
        case "--dpr":
            guard let value, let parsed = Double(value) else { throw CLIError.usage }
            dpr = parsed
            i += 2
        case "--refresh":
            guard let value, let parsed = Int(value) else { throw CLIError.usage }
            refresh = parsed
            i += 2
        case "--id":
            guard let value, !value.isEmpty else { throw CLIError.usage }
            id = value
            i += 2
        default:
            throw CLIError.usage
        }
    }
    guard let width, let height, let id else { throw CLIError.usage }
    return (width, height, dpr, refresh, id)
}

func createDisplay(width: Int, height: Int, dpr: Double, refresh: Int, id: String) throws {
    FileHandle.standardError.write(Data((stubNote + "\n").utf8))
    let url = try fileURL(for: id)
    let body = "{\"id\":\"\(id)\",\"width\":\(width),\"height\":\(height),\"dpr\":\(dpr),\"refresh\":\(refresh)}\n"
    try body.write(to: url, atomically: true, encoding: .utf8)
    FileHandle.standardOutput.write(Data((id + "\n").utf8))
}

func destroyDisplay(handle: String) throws {
    FileHandle.standardError.write(Data((stubNote + "\n").utf8))
    let url = try fileURL(for: handle)
    if FileManager.default.fileExists(atPath: url.path) {
        try FileManager.default.removeItem(at: url)
    } else {
        throw CLIError.io("unknown virtual display \(handle)")
    }
}

func main() {
    var args = CommandLine.arguments
    args.removeFirst()
    guard let command = args.first else {
        FileHandle.standardError.write(Data((usage() + "\n").utf8))
        exit(1)
    }
    do {
        switch command {
        case "create":
            let parsed = try parseCreate(Array(args.dropFirst()))
            try createDisplay(
                width: parsed.width,
                height: parsed.height,
                dpr: parsed.dpr,
                refresh: parsed.refresh,
                id: parsed.id
            )
        case "destroy":
            guard args.count >= 2 else { throw CLIError.usage }
            try destroyDisplay(handle: args[1])
        default:
            throw CLIError.usage
        }
    } catch CLIError.usage {
        FileHandle.standardError.write(Data((usage() + "\n").utf8))
        exit(1)
    } catch CLIError.io(let message) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(1)
    } catch {
        FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
        exit(1)
    }
}

main()
