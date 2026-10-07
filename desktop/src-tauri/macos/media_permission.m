#import <Foundation/Foundation.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>

// Keep Wry's file/dialog delegates. Replacing its media auto-grant with a
// forwarding proxy avoids swizzling a dependency's process-wide class.
@interface TXMediaPermissionDelegate : NSObject <WKUIDelegate>
@property(nonatomic, strong) id<WKUIDelegate> original;
@property(nonatomic) NSUInteger port;
@end
@implementation TXMediaPermissionDelegate
- (BOOL)respondsToSelector:(SEL)selector {
    return [super respondsToSelector:selector] || [self.original respondsToSelector:selector];
}
- (id)forwardingTargetForSelector:(SEL)selector {
    return [self.original respondsToSelector:selector] ? self.original : [super forwardingTargetForSelector:selector];
}
- (void)webView:(WKWebView *)webView
    requestMediaCapturePermissionForOrigin:(WKSecurityOrigin *)origin
    initiatedByFrame:(WKFrameInfo *)frame
    type:(WKMediaCaptureType)type
    decisionHandler:(void (^)(WKPermissionDecision))decisionHandler {
    NSURL *url = webView.URL;
    BOOL trusted = self.port > 0 && frame.mainFrame && type == WKMediaCaptureTypeMicrophone
        && [origin.protocol isEqualToString:@"http"] && [origin.host isEqualToString:@"127.0.0.1"]
        && origin.port == self.port && [url.scheme isEqualToString:@"http"]
        && [url.host isEqualToString:@"127.0.0.1"] && url.port.unsignedIntegerValue == self.port;
    // Prompt remains subject to OS/TCC consent and denial. Entitlement is a
    // prerequisite, never preauthorization to record or upload microphone data.
    decisionHandler(trusted ? WKPermissionDecisionPrompt : WKPermissionDecisionDeny);
}
@end

static char TXMediaDelegateKey;
bool termx_install_media_permission(void *pointer, unsigned short port) {
    if (![NSThread isMainThread] || pointer == NULL) return false;
    WKWebView *view = (__bridge WKWebView *)pointer;
    TXMediaPermissionDelegate *delegate = objc_getAssociatedObject(view, &TXMediaDelegateKey);
    if (!delegate) {
        delegate = [TXMediaPermissionDelegate new];
        delegate.original = view.UIDelegate;
        // WKWebView's delegate is weak: retain the proxy for exactly the
        // lifetime of this WebView, with no strong reference back to the view.
        objc_setAssociatedObject(view, &TXMediaDelegateKey, delegate, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        view.UIDelegate = delegate;
    }
    delegate.port = port;
    return true;
}
