import CoreImage
import CoreMedia
import CoreVideo
import Darwin
import Foundation
import ImageIO
import ScreenCaptureKit

let runner = CaptureRunner()
DispatchQueue.main.async {
    runner.start()
}
dispatchMain()

final class CaptureRunner: NSObject, SCStreamOutput, SCStreamDelegate {
    private let outputQueue = DispatchQueue(label: "termx.capture.frames")
    private let ciContext = CIContext()
    private var stream: SCStream?
    private var busy = false

    func start() {
        SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true) { content, error in
            if let error {
                fputs("termx-capture: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
            guard let content, let display = content.displays.first else {
                fputs("termx-capture: no display available\n", stderr)
                exit(1)
            }
            self.begin(display: display)
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
        stream.startCapture { error in
            if let error {
                fputs("termx-capture: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        self.stream = stream
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
