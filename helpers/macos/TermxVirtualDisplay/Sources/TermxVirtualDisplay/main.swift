import CTermxVirtualDisplay
import Darwin
import Foundation

enum CLIError: Error {
    case usage
    case io(String)
    case unavailable(String)
}

func usage() -> String {
    return """
    usage: termx-virtual-display create --width N --height N --dpr F --refresh N --id ID [--parent-pid PID]
           termx-virtual-display destroy DISPLAY_ID
           termx-virtual-display list
    """
}

func storeDir() -> URL {
    let tmp = ProcessInfo.processInfo.environment["TMPDIR"] ?? NSTemporaryDirectory()
    return URL(fileURLWithPath: tmp, isDirectory: true)
        .appendingPathComponent("termx-virtual-displays", isDirectory: true)
}

func ensureStore() throws -> URL {
    let dir = storeDir()
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir
}

func safe(_ value: String) -> String {
    return (value as NSString).lastPathComponent
}

func pidFile(displayID: UInt32) throws -> URL {
    try ensureStore().appendingPathComponent("\(displayID).pid")
}

func jsonFile(displayID: UInt32) throws -> URL {
    try ensureStore().appendingPathComponent("\(displayID).json")
}

func parseCreate(_ args: [String]) throws -> (width: Int, height: Int, dpr: Double, refresh: Int, id: String, parent: Int?) {
    var width: Int?
    var height: Int?
    var dpr: Double = 1.0
    var refresh: Int = 60
    var id: String?
    var parent: Int?
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
        case "--parent-pid":
            guard let value, let parsed = Int(value) else { throw CLIError.usage }
            parent = parsed
            i += 2
        default:
            throw CLIError.usage
        }
    }
    guard let width, let height, let id else { throw CLIError.usage }
    return (width, height, dpr, refresh, id, parent)
}

func createDisplay(width: Int, height: Int, refresh: Int, id: String, parentPID: Int?) throws {
    if termx_virtual_display_available() == 0 {
        throw CLIError.unavailable(
            "CGVirtualDisplay is not available on this macOS version; install a signed host helper"
        )
    }
    var displayID: UInt32 = 0
    let name = "termx-\(safe(id))"
    let result = termx_virtual_display_create(UInt32(width), UInt32(height), Double(refresh), name, &displayID)
    switch result {
    case TERMX_VD_OK:
        break
    case TERMX_VD_UNAVAILABLE:
        throw CLIError.unavailable("CGVirtualDisplay is not available on this macOS version")
    case TERMX_VD_DESCRIPTOR_FAILED:
        throw CLIError.io("could not allocate a virtual display descriptor")
    case TERMX_VD_APPLY_FAILED:
        throw CLIError.io("virtual display rejected mode \(width)x\(height)@\(refresh)")
    default:
        throw CLIError.io("virtual display creation failed (\(result))")
    }
    let pid = ProcessInfo.processInfo.processIdentifier
    let pidURL = try pidFile(displayID: displayID)
    try "\(pid)\n".write(to: pidURL, atomically: true, encoding: .utf8)
    let record = "{\"id\":\"\(safe(id))\",\"display_id\":\(displayID),\"width\":\(width),\"height\":\(height),\"refresh\":\(refresh),\"pid\":\(pid)}\n"
    try record.write(to: try jsonFile(displayID: displayID), atomically: true, encoding: .utf8)
    FileHandle.standardOutput.write(Data("\(displayID)\n".utf8))

    if let parentPID {
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global())
        timer.schedule(deadline: .now() + 2, repeating: 2)
        timer.setEventHandler {
            if kill(Int32(parentPID), 0) != 0 {
                exit(0)
            }
        }
        timer.resume()
    }

    // Keep the CGVirtualDisplay object alive for as long as this process runs.
    RunLoop.main.run()
}

func destroyDisplay(handle: String) throws {
    guard let displayID = UInt32(handle) else {
        throw CLIError.io("unknown virtual display \(handle)")
    }
    let pidURL = try pidFile(displayID: displayID)
    guard let text = try? String(contentsOf: pidURL, encoding: .utf8),
          let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines))
    else {
        throw CLIError.io("no running helper owns display \(handle)")
    }
    if kill(pid, 0) != 0 {
        try? FileManager.default.removeItem(at: pidURL)
        throw CLIError.io("display \(handle) is no longer running")
    }
    kill(pid, SIGTERM)
    let deadline = Date().addingTimeInterval(5)
    while kill(pid, 0) == 0 && Date() < deadline {
        usleep(100_000)
    }
    if kill(pid, 0) == 0 {
        kill(pid, SIGKILL)
    }
    try? FileManager.default.removeItem(at: pidURL)
    try? FileManager.default.removeItem(at: try jsonFile(displayID: displayID))
}

func listDisplays() throws {
    let dir = try ensureStore()
    let entries = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? []
    for entry in entries where entry.pathExtension == "json" {
        if let text = try? String(contentsOf: entry, encoding: .utf8) {
            FileHandle.standardOutput.write(Data(text.utf8))
        }
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
                refresh: parsed.refresh,
                id: parsed.id,
                parentPID: parsed.parent
            )
        case "destroy":
            guard args.count >= 2 else { throw CLIError.usage }
            try destroyDisplay(handle: args[1])
        case "list":
            try listDisplays()
        default:
            throw CLIError.usage
        }
    } catch CLIError.usage {
        FileHandle.standardError.write(Data((usage() + "\n").utf8))
        exit(1)
    } catch CLIError.io(let message) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(1)
    } catch CLIError.unavailable(let message) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(2)
    } catch {
        FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
        exit(1)
    }
}

main()
