"""Native microphone policy logic; does not claim a device/GUI recording proof."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_mic_prerequisites_and_actual_native_permission_handlers():
    native = ROOT/'desktop/src-tauri'
    entitlement = plistlib.loads((native/'entitlements.plist').read_bytes())
    info = plistlib.loads((native/'Info.plist').read_bytes())
    assert entitlement['com.apple.security.device.audio-input'] is True
    assert 'start dictation' in info['NSMicrophoneUsageDescription']
    policy = (native/'src/media_permission.rs').read_text()
    assert 'connect_permission_request' in policy and 'request.allow()' in policy
    assert 'ResponseType::Yes' in policy and 'ResponseType::No' in policy
    assert 'is_for_video_device()' in policy and 'workspace_url(&app, url)' in policy
    for source in ['src/ui.rs','src/workspace.rs']:
        text = (native/source).read_text()
        assert 'media_permission::install' in text
    assert 'COREWEBVIEW2_PERMISSION_STATE_ALLOW' not in policy
    assert 'COREWEBVIEW2_PERMISSION_KIND_MICROPHONE' in policy
    assert 'COREWEBVIEW2_PERMISSION_STATE_DENY' in policy
    assert 'uri.is_null()' in policy
    assert 'args.PermissionKind(&mut kind).is_err()' in policy
    assert 'args.Uri(&mut uri).is_err()' in policy
    assert 'permission_owner.destroy()' in policy
    assert 'completed.replace(true)' in policy
    bridge = (native/'macos/media_permission.m').read_text()
    assert 'WKPermissionDecisionGrant' not in bridge
    assert 'WKPermissionDecisionPrompt' in bridge and 'WKPermissionDecisionDeny' in bridge
    assert 'frame.mainFrame' in bridge and 'WKMediaCaptureTypeMicrophone' in bridge


@pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('clang'),reason='actual Objective-C/WebKit policy execution needs macOS')
def test_native_webkit_policy_prompt_scope_and_delegate_forwarding(tmp_path):
    # Controlled NSObject collaborators exercise the compiled WKUIDelegate
    # implementation without touching a microphone, desktop or TCC database.
    source = tmp_path/'policy.m'
    source.write_text('''
#import <Foundation/Foundation.h>
#import <WebKit/WebKit.h>
#import "media_permission.m"
@interface FixtureView:NSObject
@property NSURL *URL;
@end
@implementation FixtureView @end
@interface FixtureOrigin:NSObject
@property NSString *protocol; @property NSString *host; @property NSInteger port;
@end
@implementation FixtureOrigin @end
@interface FixtureFrame:NSObject
@property(getter=isMainFrame) BOOL mainFrame;
@end
@implementation FixtureFrame @end
@interface FixtureOriginal:NSObject<WKUIDelegate>
@property BOOL forwarded;
@end
@implementation FixtureOriginal
- (void)webViewDidClose:(WKWebView*)view { self.forwarded=YES; }
@end
int main() { @autoreleasepool {
 TXMediaPermissionDelegate *delegate=[TXMediaPermissionDelegate new]; delegate.port=9911;
 FixtureOriginal *original=[FixtureOriginal new]; delegate.original=original;
 [delegate webViewDidClose:nil]; if(!original.forwarded)return 1;
 FixtureView *view=[FixtureView new]; view.URL=[NSURL URLWithString:@"http://127.0.0.1:9911/"];
 FixtureOrigin *origin=[FixtureOrigin new];origin.protocol=@"http";origin.host=@"127.0.0.1";origin.port=9911;
 FixtureFrame *frame=[FixtureFrame new];frame.mainFrame=YES;
 __block WKPermissionDecision decision;
 void (^probe)(WKMediaCaptureType)=^(WKMediaCaptureType type){
  [delegate webView:(WKWebView*)view requestMediaCapturePermissionForOrigin:(WKSecurityOrigin*)origin
   initiatedByFrame:(WKFrameInfo*)frame type:type decisionHandler:^(WKPermissionDecision value){decision=value;}];
 };
 probe(WKMediaCaptureTypeMicrophone);if(decision!=WKPermissionDecisionPrompt)return 2;
 probe(WKMediaCaptureTypeCamera);if(decision!=WKPermissionDecisionDeny)return 3;
 probe(WKMediaCaptureTypeCameraAndMicrophone);if(decision!=WKPermissionDecisionDeny)return 4;
 frame.mainFrame=NO;probe(WKMediaCaptureTypeMicrophone);if(decision!=WKPermissionDecisionDeny)return 5;
 frame.mainFrame=YES;origin.port=9912;probe(WKMediaCaptureTypeMicrophone);if(decision!=WKPermissionDecisionDeny)return 6;
 origin.port=9911;origin.host=@"example.invalid";probe(WKMediaCaptureTypeMicrophone);if(decision!=WKPermissionDecisionDeny)return 7;
 origin.host=@"127.0.0.1";view.URL=[NSURL URLWithString:@"http://127.0.0.1:9912/"];
 probe(WKMediaCaptureTypeMicrophone);if(decision!=WKPermissionDecisionDeny)return 8;
 view.URL=[NSURL URLWithString:@"http://127.0.0.1:9911/"];delegate.port=0;
 probe(WKMediaCaptureTypeMicrophone);if(decision!=WKPermissionDecisionDeny)return 9;
 puts("native microphone policy: scoped Prompt, foreign/video Deny, original delegate forwarded");return 0;
}}
''')
    binary = tmp_path/'policy-test'
    compiled = subprocess.run(['clang','-fobjc-arc','-mmacosx-version-min=13.0',
        '-I',str(ROOT/'desktop/src-tauri/macos'),str(source),'-framework','Foundation',
        '-framework','WebKit','-o',str(binary)],capture_output=True,text=True,timeout=40)
    assert compiled.returncode==0,compiled.stderr
    result = subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'scoped Prompt' in result.stdout
