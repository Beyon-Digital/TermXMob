#import "capture_bridge.h"

#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <QuartzCore/QuartzCore.h>
#import <ImageIO/ImageIO.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <ApplicationServices/ApplicationServices.h>

#import <ScreenCaptureKit/ScreenCaptureKit.h>

#import <os/lock.h>
#import <string.h>

static char g_error[512];
static os_unfair_lock g_error_lock = OS_UNFAIR_LOCK_INIT;

static void termx_set_error(NSString *message) {
    os_unfair_lock_lock(&g_error_lock);
    const char *utf8 = message.UTF8String ?: "unknown error";
    strlcpy(g_error, utf8, sizeof(g_error));
    os_unfair_lock_unlock(&g_error_lock);
}

const char *termx_bridge_last_error(void) {
    os_unfair_lock_lock(&g_error_lock);
    const char *result = g_error;
    os_unfair_lock_unlock(&g_error_lock);
    return result;
}

void termx_bridge_free(void *ptr) {
    if (ptr) {
        free(ptr);
    }
}

int32_t termx_bridge_os_version(void) {
    NSOperatingSystemVersion version = [[NSProcessInfo processInfo] operatingSystemVersion];
    return (int32_t)(version.majorVersion * 10000 + version.minorVersion * 100 + version.patchVersion);
}

int32_t termx_permission_preflight(void) {
    return CGPreflightScreenCaptureAccess() ? 1 : 0;
}

int32_t termx_permission_request(void) {
    return CGRequestScreenCaptureAccess() ? 1 : 0;
}

int32_t termx_accessibility_preflight(void) {
    return AXIsProcessTrusted() ? 1 : 0;
}

int32_t termx_accessibility_request(void) {
    NSDictionary *options = @{ (__bridge NSString *)kAXTrustedCheckOptionPrompt : @YES };
    return AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options) ? 1 : 0;
}

#pragma mark - Capture

typedef struct {
    uint64_t generation;
    CVPixelBufferRef buffer;
} termx_frame_slot;

@interface TermxCaptureSink : NSObject <SCStreamOutput, SCStreamDelegate>
@property (nonatomic, assign) os_unfair_lock *lock;
@property (nonatomic, assign) termx_frame_slot *slot;
@end

@implementation TermxCaptureSink

- (void)stream:(SCStream *)stream
    didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer
                   ofType:(SCStreamOutputType)type {
    if (type != SCStreamOutputTypeScreen || !CMSampleBufferIsValid(sampleBuffer)) {
        return;
    }
    // Only forward complete frames; SCK also delivers idle/blank placeholders.
    CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, false);
    if (attachments != NULL && CFArrayGetCount(attachments) > 0) {
        CFDictionaryRef attachment = CFArrayGetValueAtIndex(attachments, 0);
        CFNumberRef statusValue = CFDictionaryGetValue(attachment, (__bridge CFStringRef)SCStreamFrameInfoStatus);
        int status = 0;
        if (statusValue != NULL && CFNumberGetValue(statusValue, kCFNumberIntType, &status)) {
            if (status != SCFrameStatusComplete) {
                return;
            }
        }
    }
    CVPixelBufferRef pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer);
    if (pixelBuffer == NULL) {
        return;
    }
    CVPixelBufferRetain(pixelBuffer);
    os_unfair_lock_lock(self.lock);
    CVPixelBufferRef previous = self.slot->buffer;
    self.slot->buffer = pixelBuffer;
    self.slot->generation += 1;
    os_unfair_lock_unlock(self.lock);
    if (previous) {
        CVPixelBufferRelease(previous);
    }
}

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error {
    NSString *message = [NSString stringWithFormat:@"stream stopped: %@ (%ld)", error.localizedDescription,
                                                   (long)error.code];
    termx_set_error(message);
}

@end

static SCStream *g_stream = nil;
static TermxCaptureSink *g_sink = nil;
static dispatch_queue_t g_queue = nil;
static termx_frame_slot g_slot = {0, NULL};
static os_unfair_lock g_slot_lock = OS_UNFAIR_LOCK_INIT;
static uint32_t g_display_id = 0;
static int32_t g_width = 0;
static int32_t g_height = 0;
static int32_t g_fps = 12;
static int32_t g_started = 0;

static SCDisplay *termx_find_display(SCShareableContent *content, uint32_t display_id) {
    if (display_id == 0) {
        return content.displays.firstObject;
    }
    for (SCDisplay *display in content.displays) {
        if (display.displayID == display_id) {
            return display;
        }
    }
    return nil;
}

static void termx_teardown_stream(void) {
    SCStream *stream = g_stream;
    g_stream = nil;
    g_started = 0;
    if (stream != nil) {
        dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
        [stream stopCaptureWithCompletionHandler:^(NSError *error) {
            (void)error;
            dispatch_semaphore_signal(semaphore);
        }];
        dispatch_semaphore_wait(semaphore, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
    }
    os_unfair_lock_lock(&g_slot_lock);
    CVPixelBufferRef buffer = g_slot.buffer;
    g_slot.buffer = NULL;
    g_slot.generation += 1;
    os_unfair_lock_unlock(&g_slot_lock);
    if (buffer) {
        CVPixelBufferRelease(buffer);
    }
}

int32_t termx_capture_start(uint32_t display_id, int32_t max_width, int32_t max_height, int32_t fps) {
    if (@available(macOS 12.3, *)) {
        if (g_started && g_stream != nil && display_id == g_display_id && max_width == g_width &&
            max_height == g_height && fps == g_fps) {
            return 0;
        }
        termx_teardown_stream();

        __block SCShareableContent *content = nil;
        __block NSError *contentError = nil;
        dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
        [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                                  onScreenWindowsOnly:YES
                                                    completionHandler:^(SCShareableContent *result,
                                                                        NSError *error) {
            content = result;
            contentError = error;
            dispatch_semaphore_signal(semaphore);
        }];
        if (dispatch_semaphore_wait(semaphore, dispatch_time(DISPATCH_TIME_NOW, 6 * NSEC_PER_SEC)) != 0) {
            termx_set_error(@"ScreenCaptureKit did not respond while listing displays");
            return -4;
        }
        if (contentError != nil || content == nil) {
            termx_set_error([NSString stringWithFormat:@"screen capture unavailable: %@ (%ld)",
                                                       contentError.localizedDescription ?: @"no content",
                                                       (long)contentError.code]);
            return contentError.code == -3801 ? -3801 : -5;
        }
        SCDisplay *display = termx_find_display(content, display_id);
        if (display == nil) {
            termx_set_error([NSString stringWithFormat:@"display %u not found", display_id]);
            return -6;
        }

        SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:display
                                                     excludingApplications:@[]
                                                          exceptingWindows:@[]];
        SCStreamConfiguration *config = [[SCStreamConfiguration alloc] init];
        size_t width = max_width > 0 ? (size_t)max_width : (size_t)display.width;
        size_t height = max_height > 0 ? (size_t)max_height : (size_t)display.height;
        if (width < 16 || height < 16) {
            // A brand-new virtual display can report 0x0 until the window
            // server finishes registering its mode; fall back to a usable size
            // rather than streaming a 1x1 frame.
            width = max_width > 0 ? (size_t)max_width : 1920;
            height = max_height > 0 ? (size_t)max_height : 1080;
        }
        config.width = width;
        config.height = height;
        config.pixelFormat = kCVPixelFormatType_32BGRA;
        config.showsCursor = YES;
        config.queueDepth = 5;
        int32_t rate = fps > 0 ? fps : 12;
        if (rate > 30) {
            rate = 30;
        }
        config.minimumFrameInterval = CMTimeMake(1, rate);

        if (g_queue == nil) {
            g_queue = dispatch_queue_create("com.jaexxxy.termx.capture", DISPATCH_QUEUE_SERIAL);
        }
        TermxCaptureSink *sink = [[TermxCaptureSink alloc] init];
        sink.lock = &g_slot_lock;
        sink.slot = &g_slot;
        SCStream *stream = [[SCStream alloc] initWithFilter:filter configuration:config delegate:sink];
        NSError *addError = nil;
        if (![stream addStreamOutput:sink type:SCStreamOutputTypeScreen sampleHandlerQueue:g_queue error:&addError]) {
            termx_set_error([NSString stringWithFormat:@"cannot attach stream output: %@",
                                                       addError.localizedDescription ?: @"unknown"]);
            return -7;
        }

        __block NSError *startError = nil;
        dispatch_semaphore_t startSemaphore = dispatch_semaphore_create(0);
        [stream startCaptureWithCompletionHandler:^(NSError *error) {
            startError = error;
            dispatch_semaphore_signal(startSemaphore);
        }];
        if (dispatch_semaphore_wait(startSemaphore, dispatch_time(DISPATCH_TIME_NOW, 6 * NSEC_PER_SEC)) != 0) {
            termx_set_error(@"timed out starting the screen stream");
            return -8;
        }
        if (startError != nil) {
            termx_set_error([NSString stringWithFormat:@"cannot start screen stream: %@ (%ld)",
                                                       startError.localizedDescription ?: @"unknown",
                                                       (long)startError.code]);
            return startError.code == -3801 ? -3801 : -9;
        }

        g_stream = stream;
        g_sink = sink;
        g_display_id = display_id;
        g_width = max_width;
        g_height = max_height;
        g_fps = fps;
        g_started = 1;
        return 0;
    }
    termx_set_error(@"ScreenCaptureKit requires macOS 12.3 or later");
    return -2;
}

static bool termx_encode_jpeg(CVPixelBufferRef pixelBuffer, int32_t quality, uint8_t **out, size_t *out_len) {
    CIImage *image = [CIImage imageWithCVPixelBuffer:pixelBuffer];
    static CIContext *context = nil;
    if (context == nil) {
        context = [CIContext contextWithOptions:@{ kCIContextUseSoftwareRenderer : @NO }];
    }
    CGImageRef cgImage = [context createCGImage:image fromRect:image.extent];
    if (cgImage == NULL) {
        termx_set_error(@"cannot convert the frame to an image");
        return false;
    }
    NSMutableData *data = [NSMutableData data];
    CGImageDestinationRef destination =
        CGImageDestinationCreateWithData((__bridge CFMutableDataRef)data, (__bridge CFStringRef)UTTypeJPEG.identifier, 1, NULL);
    if (destination == NULL) {
        CGImageRelease(cgImage);
        termx_set_error(@"cannot create a JPEG encoder");
        return false;
    }
    int clamped = quality < 10 ? 10 : (quality > 95 ? 95 : quality);
    NSDictionary *properties = @{ (__bridge NSString *)kCGImageDestinationLossyCompressionQuality : @(clamped / 100.0) };
    CGImageDestinationAddImage(destination, cgImage, (__bridge CFDictionaryRef)properties);
    bool ok = CGImageDestinationFinalize(destination);
    CFRelease(destination);
    CGImageRelease(cgImage);
    if (!ok || data.length == 0) {
        termx_set_error(@"cannot encode the frame as JPEG");
        return false;
    }
    uint8_t *buffer = malloc(data.length);
    if (buffer == NULL) {
        termx_set_error(@"out of memory while copying the frame");
        return false;
    }
    memcpy(buffer, data.bytes, data.length);
    *out = buffer;
    *out_len = data.length;
    return true;
}

int32_t termx_capture_frame(uint8_t **out, size_t *out_len, int32_t timeout_ms, int32_t quality) {
    if (@available(macOS 12.3, *)) {
        if (g_stream == nil) {
            termx_set_error(@"the screen stream is not running");
            return -10;
        }
        CFTimeInterval deadline = CACurrentMediaTime() + (timeout_ms > 0 ? timeout_ms / 1000.0 : 1.0);
        CVPixelBufferRef frame = NULL;
        while (frame == NULL) {
            os_unfair_lock_lock(&g_slot_lock);
            if (g_slot.buffer != NULL) {
                frame = CVPixelBufferRetain(g_slot.buffer);
            }
            os_unfair_lock_unlock(&g_slot_lock);
            if (frame != NULL) {
                break;
            }
            if (CACurrentMediaTime() >= deadline) {
                return -3;
            }
            [NSThread sleepForTimeInterval:0.02];
        }
        bool ok = termx_encode_jpeg(frame, quality, out, out_len);
        CVPixelBufferRelease(frame);
        return ok ? 0 : -11;
    }
    termx_set_error(@"ScreenCaptureKit requires macOS 12.3 or later");
    return -2;
}

void termx_capture_stop(void) {
    if (@available(macOS 12.3, *)) {
        termx_teardown_stream();
    }
}
