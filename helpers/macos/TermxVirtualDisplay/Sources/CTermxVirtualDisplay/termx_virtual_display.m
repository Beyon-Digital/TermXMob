#import "termx_virtual_display.h"

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>

// Private CoreGraphics classes. They are exported by CoreGraphics at runtime;
// we only declare the surface we use. Availability is checked dynamically so
// the helper fails cleanly on systems without them.
@interface CGVirtualDisplayDescriptor : NSObject
@property (nonatomic) unsigned int maxPixelsWide;
@property (nonatomic) unsigned int maxPixelsHigh;
@property (nonatomic) CGSize sizeInMillimeters;
@property (nonatomic) unsigned int serialNum;
@property (nonatomic) unsigned int productID;
@property (nonatomic) unsigned int vendorID;
@property (nonatomic, copy) NSString *name;
@property (nonatomic, strong) dispatch_queue_t queue;
@end

@interface CGVirtualDisplayMode : NSObject
- (instancetype)initWithWidth:(unsigned int)width
                       height:(unsigned int)height
                  refreshRate:(double)refreshRate;
@end

@interface CGVirtualDisplaySettings : NSObject
@property (nonatomic) unsigned int hiDPI;
@property (nonatomic, copy) NSArray *modes;
@end

@interface CGVirtualDisplay : NSObject
@property (nonatomic, readonly) unsigned int displayID;
- (instancetype)initWithDescriptor:(CGVirtualDisplayDescriptor *)descriptor;
- (BOOL)applySettings:(CGVirtualDisplaySettings *)settings;
@end

static NSMutableArray<CGVirtualDisplay *> *gDisplays = nil;

int32_t termx_virtual_display_available(void) {
    return NSClassFromString(@"CGVirtualDisplay") != nil ? 1 : 0;
}

int32_t termx_virtual_display_create(
    uint32_t width,
    uint32_t height,
    double refresh,
    const char *name,
    uint32_t *out_id
) {
    @autoreleasepool {
        if (termx_virtual_display_available() == 0) {
            return TERMX_VD_UNAVAILABLE;
        }
        if (width == 0 || height == 0) {
            return TERMX_VD_DESCRIPTOR_FAILED;
        }

        CGVirtualDisplayDescriptor *descriptor = [[CGVirtualDisplayDescriptor alloc] init];
        descriptor.name = [NSString stringWithUTF8String:name ? name : "Termx Virtual Display"];
        descriptor.maxPixelsWide = width;
        descriptor.maxPixelsHigh = height;
        descriptor.sizeInMillimeters = CGSizeMake(width / 10.0, height / 10.0);
        descriptor.serialNum = 1;
        descriptor.productID = 0x544D;
        descriptor.vendorID = 0x544D;
        descriptor.queue = dispatch_get_main_queue();

        CGVirtualDisplay *display = [[CGVirtualDisplay alloc] initWithDescriptor:descriptor];
        if (display == nil) {
            return TERMX_VD_DESCRIPTOR_FAILED;
        }

        CGVirtualDisplayMode *mode =
            [[CGVirtualDisplayMode alloc] initWithWidth:width height:height refreshRate:refresh];
        CGVirtualDisplaySettings *settings = [[CGVirtualDisplaySettings alloc] init];
        settings.hiDPI = 0;
        settings.modes = @[ mode ];
        if (![display applySettings:settings]) {
            return TERMX_VD_APPLY_FAILED;
        }

        if (gDisplays == nil) {
            gDisplays = [NSMutableArray array];
        }
        [gDisplays addObject:display];
        if (out_id != NULL) {
            *out_id = display.displayID;
        }
        return TERMX_VD_OK;
    }
}

void termx_virtual_display_release_all(void) {
    @autoreleasepool {
        gDisplays = nil;
    }
}
