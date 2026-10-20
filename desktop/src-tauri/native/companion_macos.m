#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#import <CoreGraphics/CoreGraphics.h>
#import "companion_bridge.h"

// This translation unit is deliberately main-thread-only, including callbacks.
static TXCompanionEvent txEvents[64];
static size_t txRead=0,txWrite=0;
static bool txSleeping=false,txInactive=false,txLocked=false;
static bool txIsAwake(void){return !txSleeping&&!txInactive&&!txLocked;}
static NSMutableArray *txObservers;
static void txEvent(uint32_t slot,uint32_t kind,double x,double y) {
    size_t next=(txWrite+1)%64;
    if(next==txRead) txRead=(txRead+1)%64;
    txEvents[txWrite]=(TXCompanionEvent){slot,kind,x,y};txWrite=next;
}
static double txTop(void) { return NSScreen.screens.count ? NSMaxY(NSScreen.screens.firstObject.frame) : 0; }
@interface TXCompanionPanel : NSPanel
@end
@implementation TXCompanionPanel
- (BOOL)canBecomeKeyWindow {return NO;}
- (BOOL)canBecomeMainWindow {return NO;}
@end
@interface TXCompanionView : NSView
@property(nonatomic) uint32_t slot;
@property(nonatomic) BOOL dragging,moved,flipSprite;
@property(nonatomic) NSPoint dragStart,windowStart;
@property(nonatomic,strong) NSData *pixels;
@property(nonatomic,strong) NSMenu *activeMenu;
@property(nonatomic) NSUInteger pixelWidth,pixelHeight;
@property(nonatomic) double footX,footY;
@property(nonatomic) NSRect interaction;
@property(nonatomic) CGImageRef sprite;
@end
@implementation TXCompanionView
- (BOOL)isFlipped {return YES;}
- (BOOL)acceptsFirstResponder {return NO;}
- (void)dealloc {if(_sprite)CGImageRelease(_sprite);}
- (void)drawRect:(NSRect)dirty {
    [[NSColor clearColor]set];NSRectFillUsingOperation(self.bounds,NSCompositingOperationCopy);
    if(!self.sprite)return;
    CGContextRef ctx=NSGraphicsContext.currentContext.CGContext;
    CGContextSaveGState(ctx);
    CGContextSetInterpolationQuality(ctx,kCGInterpolationNone);
    CGContextTranslateCTM(ctx,self.flipSprite?self.bounds.size.width:0,self.bounds.size.height);
    CGContextScaleCTM(ctx,self.flipSprite?-1:1,-1);
    CGContextDrawImage(ctx,NSRectToCGRect(self.bounds),self.sprite);
    CGContextRestoreGState(ctx);
}
- (BOOL)opaqueAtMouse {
    NSPoint local=[self convertPoint:[self.window mouseLocationOutsideOfEventStream] fromView:nil];
    if(!NSPointInRect(local,self.interaction)||!NSPointInRect(local,self.bounds)||!self.pixels||self.bounds.size.width<=0||self.bounds.size.height<=0)return NO;
    NSUInteger x=MIN(self.pixelWidth-1,(NSUInteger)(local.x/self.bounds.size.width*self.pixelWidth));
    NSUInteger y=MIN(self.pixelHeight-1,(NSUInteger)(local.y/self.bounds.size.height*self.pixelHeight));
    if(self.flipSprite)x=self.pixelWidth-1-x;
    return ((const uint8_t*)self.pixels.bytes)[(y*self.pixelWidth+x)*4+3]>20;
}
- (void)mouseDown:(NSEvent*)event {
    self.dragStart=NSEvent.mouseLocation;self.windowStart=self.window.frame.origin;self.moved=NO;self.dragging=YES;
    txEvent(self.slot,2,0,0);
}
- (void)mouseDragged:(NSEvent*)event {
    NSPoint mouse=NSEvent.mouseLocation;NSPoint origin=NSMakePoint(self.windowStart.x+mouse.x-self.dragStart.x,self.windowStart.y+mouse.y-self.dragStart.y);
    if(hypot(mouse.x-self.dragStart.x,mouse.y-self.dragStart.y)>3)self.moved=YES;
    [self.window setFrameOrigin:origin];
    txEvent(self.slot,3,origin.x+self.footX,txTop()-origin.y-self.window.frame.size.height+self.footY);
}
- (void)mouseUp:(NSEvent*)event {
    if(!self.dragging)return;
    self.dragging=NO;NSRect frame=self.window.frame;
    txEvent(self.slot,4,frame.origin.x+self.footX,txTop()-NSMaxY(frame)+self.footY);
    if(!self.moved)txEvent(self.slot,1,0,0);
}
- (void)menuAction:(NSMenuItem*)item {txEvent(self.slot,(uint32_t)item.tag,0,0);}
- (void)rightMouseDown:(NSEvent*)event {
    NSMenu *menu=[[NSMenu alloc]initWithTitle:@"Companion"];
    NSArray *titles=@[@"Open agent",@"Pause / resume",@"Bring home",@"Hide companion"];
    int tags[]={1,5,6,7};
    for(NSUInteger i=0;i<titles.count;i++){
        NSMenuItem *item=[[NSMenuItem alloc]initWithTitle:titles[i] action:@selector(menuAction:) keyEquivalent:@""];
        item.target=self;item.tag=tags[i];[menu addItem:item];
    }
    self.activeMenu=menu;[NSMenu popUpContextMenu:menu withEvent:event forView:self];self.activeMenu=nil;
}
@end
static TXCompanionPanel *txPanels[4];
static void txInitialize(void) {
    if(txObservers)return;
    txObservers=[NSMutableArray new];
    NSNotificationCenter *workspace=NSWorkspace.sharedWorkspace.notificationCenter;
    id token=[workspace addObserverForName:NSWorkspaceWillSleepNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *n){(void)n;txSleeping=true;for(int i=0;i<4;i++)termx_companion_hide(i);}];[txObservers addObject:token];
    token=[workspace addObserverForName:NSWorkspaceSessionDidResignActiveNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *n){(void)n;txInactive=true;for(int i=0;i<4;i++)termx_companion_hide(i);}];[txObservers addObject:token];
    token=[workspace addObserverForName:NSWorkspaceDidWakeNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *n){(void)n;txSleeping=false;}];[txObservers addObject:token];
    token=[workspace addObserverForName:NSWorkspaceSessionDidBecomeActiveNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *n){(void)n;txInactive=false;}];[txObservers addObject:token];
    CFDictionaryRef session=CGSessionCopyCurrentDictionary();
    if(session){NSDictionary*info=(__bridge NSDictionary*)session;txInactive=![info[(__bridge NSString*)kCGSessionOnConsoleKey]boolValue];txLocked=[info[@"CGSSessionScreenIsLocked"]boolValue];CFRelease(session);}
    NSDistributedNotificationCenter *distributed=NSDistributedNotificationCenter.defaultCenter;
    token=[distributed addObserverForName:@"com.apple.screenIsLocked" object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification*n){(void)n;txLocked=true;for(int i=0;i<4;i++)termx_companion_hide(i);}];[txObservers addObject:token];
    token=[distributed addObserverForName:@"com.apple.screenIsUnlocked" object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification*n){(void)n;txLocked=false;}];[txObservers addObject:token];
}
bool termx_companion_available(void) {return NSThread.isMainThread;}
bool termx_companion_reduced_motion(void){return NSWorkspace.sharedWorkspace.accessibilityDisplayShouldReduceMotion;}
bool termx_companion_awake(void) {txInitialize();return txIsAwake();}
size_t termx_companion_geometry(TXCompanionGeometry *out,size_t capacity) {
    if(!out||!NSThread.isMainThread)return 0;
    size_t count=0;double top=txTop();
    for(NSScreen *screen in NSScreen.screens){
        if(count>=capacity)break;NSRect r=screen.visibleFrame;
        out[count++]=(TXCompanionGeometry){r.origin.x,top-NSMaxY(r),r.size.width,r.size.height,1,0};
    }
    // Public CoreGraphics metadata only; never capture titles or screenshots.
    CFArrayRef infos=CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly|kCGWindowListExcludeDesktopElements,kCGNullWindowID);
    if(infos){
        for(NSDictionary *info in (__bridge NSArray*)infos){
            if(count>=capacity)break;
            if([info[(__bridge NSString*)kCGWindowLayer]intValue]!=0 || [info[(__bridge NSString*)kCGWindowAlpha]doubleValue]<0.1)continue;
            CGRect r;if(!CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)info[(__bridge NSString*)kCGWindowBounds],&r))continue;
            if(r.size.width<60||r.size.height<40)continue;
            out[count++]=(TXCompanionGeometry){r.origin.x,r.origin.y,r.size.width,r.size.height,1,1};
        }
        CFRelease(infos);
    }
    return count;
}
bool termx_companion_render(uint32_t slot,double x,double y,double width,double height,double anchor_x,double anchor_y,double hit_x,double hit_y,double hit_width,double hit_height,const uint8_t*rgba,uint32_t pw,uint32_t ph,bool flip) {
    if(slot>=4||!rgba||!pw||!ph||pw>512||ph>512||!NSThread.isMainThread||!txIsAwake())return false;
    TXCompanionPanel *panel=txPanels[slot];
    if(!panel){
        panel=[[TXCompanionPanel alloc]initWithContentRect:NSMakeRect(0,0,width,height) styleMask:NSWindowStyleMaskBorderless|NSWindowStyleMaskNonactivatingPanel backing:NSBackingStoreBuffered defer:NO];
        if(!panel)return false;
        panel.releasedWhenClosed=NO;panel.opaque=NO;panel.backgroundColor=NSColor.clearColor;panel.hasShadow=NO;panel.level=NSFloatingWindowLevel;panel.hidesOnDeactivate=NO;
        panel.collectionBehavior=NSWindowCollectionBehaviorCanJoinAllSpaces|NSWindowCollectionBehaviorFullScreenAuxiliary|NSWindowCollectionBehaviorIgnoresCycle;
        panel.excludedFromWindowsMenu=YES;panel.accessibilityLabel=@"TermX companion";
        TXCompanionView *view=[[TXCompanionView alloc]initWithFrame:NSMakeRect(0,0,width,height)];view.slot=slot;panel.contentView=view;txPanels[slot]=panel;
    }
    TXCompanionView *view=(TXCompanionView*)panel.contentView;view.footX=anchor_x;view.footY=anchor_y;view.interaction=NSMakeRect(hit_x,hit_y,hit_width,hit_height);
    if(!view.dragging)[panel setFrame:NSMakeRect(x,txTop()-y-height,width,height) display:NO];
    NSData *pixels=[NSData dataWithBytes:rgba length:(NSUInteger)pw*ph*4];
    if(view.pixelWidth!=pw||view.pixelHeight!=ph||![view.pixels isEqualToData:pixels]){
        view.pixels=pixels;view.pixelWidth=pw;view.pixelHeight=ph;
        CGDataProviderRef provider=CGDataProviderCreateWithCFData((__bridge CFDataRef)pixels);CGColorSpaceRef color=CGColorSpaceCreateDeviceRGB();
        CGImageRef image=CGImageCreate(pw,ph,8,32,pw*4,color,kCGImageAlphaLast|kCGBitmapByteOrder32Big,provider,NULL,false,kCGRenderingIntentDefault);
        CGColorSpaceRelease(color);CGDataProviderRelease(provider);if(!image)return false;if(view.sprite)CGImageRelease(view.sprite);view.sprite=image;view.needsDisplay=YES;
    }
    if(view.flipSprite!=flip){view.flipSprite=flip;view.needsDisplay=YES;}
    panel.ignoresMouseEvents=!view.dragging&&![view opaqueAtMouse];
    [panel orderFrontRegardless];
return true;
}
void termx_companion_hide(uint32_t slot){
    if(slot>=4)return;TXCompanionView*view=(TXCompanionView*)txPanels[slot].contentView;view.dragging=NO;[view.activeMenu cancelTracking];
    [txPanels[slot]orderOut:nil];
    for(size_t i=txRead;i!=txWrite;i=(i+1)%64)if(txEvents[i].slot==slot)txEvents[i].kind=0;
}
bool termx_companion_event(TXCompanionEvent*out){if(!out||txRead==txWrite)return false;*out=txEvents[txRead];txRead=(txRead+1)%64;return true;}
void termx_companion_shutdown(void){for(int i=0;i<4;i++){[txPanels[i]close];txPanels[i]=nil;}txRead=txWrite=0;}
