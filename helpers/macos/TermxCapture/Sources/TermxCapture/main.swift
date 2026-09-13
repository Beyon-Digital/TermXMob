import CoreGraphics
import CoreImage
import CoreMedia
import CoreVideo
import Darwin
import Foundation
import ImageIO
import ScreenCaptureKit

func emitDisplays() {
    Task {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let main = CGMainDisplayID()
            var lines: [String] = []
            for display in content.displays {
                let frame = CGDisplayBounds(display.displayID)
                let entry: [String: Any] = [
                    "id": Int(display.displayID),
                    "width": Int(display.width),
                    "height": Int(display.height),
                    "x": Int(frame.origin.x),
                    "y": Int(frame.origin.y),
                    "main": display.displayID == main,
                ]
                if let data = try? JSONSerialization.data(withJSONObject: entry),
                   let text = String(data: data, encoding: .utf8)
                {
                    lines.append(text)
                }
            }
            if !lines.isEmpty {
                FileHandle.standardOutput.write(Data((lines.joined(separator: "\n") + "\n").utf8))
            }
            exit(0)
        } catch {
            fputs("termx-capture: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
    dispatchMain()
}

var requestedDisplay: CGDirectDisplayID?
var listOnly = false
var arguments = CommandLine.arguments
arguments.removeFirst()
var index = 0
while index < arguments.count {
    switch arguments[index] {
    case "--display":
        if index + 1 < arguments.count, let value = UInt32(arguments[index + 1]) {
            requestedDisplay = value
        }
        index += 2
    case "--list":
        listOnly = true
        index += 1
    default:
        index += 1
    }
}

if listOnly {
    emitDisplays()
}

let runner = CaptureRunner(displayID: requestedDisplay)
DispatchQueue.main.async {
    runner.start()
}
dispatchMain()

final class CaptureRunner: NSObject, SCStreamOutput, SCStreamDelegate {
    private let outputQueue = DispatchQueue(label: "termx.capture.frames")
    private let ciContext = CIContext()
    private var stream: SCStream?
    private var busy = false
    private let requestedDisplay: CGDirectDisplayID?

    init(displayID: CGDirectDisplayID?) {
        self.requestedDisplay = displayID
        super.init()
    }

    func start() {
        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                let display = content.displays.first(where: { $0.displayID == self.requestedDisplay })
                    ?? content.displays.first
                guard let display else {
                    fputs("termx-capture: no display available\n", stderr)
                    exit(1)
                }
                if let requested = self.requestedDisplay, display.displayID != requested {
                    fputs("termx-capture: display \(requested) not found, using \(display.displayID)\n", stderr)
                }
                self.begin(display: display)
            } catch {
                fputs("termx-capture: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
    }

    private func begin(display: SCDisplay) {
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.width = display.width
        config.height = display.height
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.showsCursor = true
        config.minimumFrameInterval = CMTime(value: 1, timescale: 12)
        config.queueDepth = 2
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        do {
            try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: outputQueue)
        } catch {
            fputs("termx-capture: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
        self.stream = stream
        Task {
            do {
                try await stream.startCapture()
            } catch {
                fputs("termx-capture: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("termx-capture: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, CMSampleBufferIsValid(sampleBuffer) else { return }
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
           let raw = attachments.first?[.status] as? Int,
           raw != SCFrameStatus.complete.rawValue
        {
            return
        }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        if busy { return }
        busy = true
        defer { busy = false }
        emitJPEG(pixelBuffer)
    }

    private func emitJPEG(_ pixelBuffer: CVPixelBuffer) {
        let image = CIImage(cvPixelBuffer: pixelBuffer)
        guard let cgImage = ciContext.createCGImage(image, from: image.extent) else { return }
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil) else { return }
        CGImageDestinationAddImage(dest, cgImage, [kCGImageDestinationLossyCompressionQuality: 0.55] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return }
        writeFrame(data as Data)
    }

    private func writeFrame(_ jpeg: Data) {
        var length = UInt32(jpeg.count).bigEndian
        let header = withUnsafeBytes(of: &length) { Data($0) }
        if !writeAll(header) || !writeAll(jpeg) {
            exit(0)
        }
    }

    private func writeAll(_ data: Data) -> Bool {
        data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return false }
            var sent = 0
            while sent < data.count {
                let n = Darwin.write(STDOUT_FILENO, base + sent, data.count - sent)
                if n <= 0 {
                    return false
                }
                sent += n
            }
            return true
        }
    }
}
