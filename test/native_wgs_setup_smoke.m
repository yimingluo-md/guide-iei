// Exercise the actual native controls with synthetic API responses. No downloads.
#define main guide_launcher_main
#include "../desktop/macos/GUIDE-IEI-App.m"
#undef main

@interface SmokeWorker : NSObject
- (BOOL)isRunning;
@end
@implementation SmokeWorker
- (BOOL)isRunning { return YES; }
@end

@interface SmokeGuide : GuideApp
@property(nonatomic, copy) NSDictionary *snapshot;
@property(nonatomic, copy) NSDictionary *plan;
@property(nonatomic, copy) NSString *postError;
@property(nonatomic, copy) NSString *lastAction;
@property NSUInteger posts;
@end
@implementation SmokeGuide
- (NSDictionary *)startupStatus { return self.snapshot; }
- (void)startupAction:(NSString *)action { self.lastAction = action; }
- (void)request:(NSString *)route body:(NSDictionary *)body done:(void (^)(NSDictionary *, NSString *))done {
    if ([route isEqual:@"/api/service/status"]) {
        done(@{@"instance_id": @"smoke", @"blockers": @[], @"annotations": @0, @"downloads": @0}, nil);
    } else if ([route isEqual:@"/api/recommended-wgs/status"]) {
        NSCAssert(!body, @"Status polls must be read-only"); done(self.plan, nil);
    } else if ([route isEqual:@"/api/recommended-wgs/setup"]) {
        NSCAssert([body[@"confirm"] boolValue], @"Explicit setup authorization required");
        self.posts++;
        if (self.postError) { done(nil, self.postError); return; }
        NSDictionary *job = @{@"id": @"wgs", @"status": @"running", @"message": @"Full SpliceAI MANE 1.5", @"log_path": @"/synthetic/wgs.log"};
        NSMutableDictionary *plan = [self.plan mutableCopy]; plan[@"job"] = job; self.plan = plan;
        done(job, nil);
    } else NSCAssert(NO, @"Unexpected request: %@", route);
}
@end

static NSDictionary *planWithJob(NSDictionary *job) {
    return @{@"known": @YES, @"installed": @NO, @"download_bytes": @112000000000,
        @"setup_bytes": @(117.0 * 1024 * 1024 * 1024), @"free_bytes": @(200.0 * 1024 * 1024 * 1024),
        @"job": job ?: NSNull.null};
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [NSApp finishLaunching];
        SmokeGuide *guide = [SmokeGuide new];
        guide.worker = (id)[SmokeWorker new]; guide.instanceID = @"smoke";
        guide.snapshot = @{@"phase": @"waiting", @"can_prepare": @YES,
            @"annotation_path": @"/Synthetic/Annotation datasets", @"free_bytes": @(200.0*1024*1024*1024),
            @"estimated_required_bytes": @(64.0*1024*1024*1024)};
        guide.plan = planWithJob(nil); guide.preparing = YES;
        [guide buildControls]; [guide refreshStatus:nil];
        NSCAssert(guide.wgsChoice.state == NSControlStateValueOff, @"WGS must default off");
        NSCAssert(guide.posts == 0, @"Polling must never start a download");
        NSCAssert([guide.wgsSummary.stringValue containsString:@"112.0 GB"], @"Show download size");
        [guide.window.contentView layoutSubtreeIfNeeded];
        NSScrollView *scroll = (id)guide.tabs.tabViewItems[0].view;
        NSCAssert([scroll isKindOfClass:NSScrollView.class] && scroll.hasVerticalScroller, @"Setup is scrollable");
        NSCAssert(scroll.documentView.frame.size.height > scroll.contentView.bounds.size.height, @"Long copy must scroll");
        NSCAssert(scroll.documentView.frame.size.width > 500 && guide.retryButton.frame.size.height > 10, @"Layout must not collapse");
        if (argc > 1) {
            [guide.window display];
            [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.2]];
            // Optional local visual QA; capture only this synthetic window.
            NSTask *capture = [NSTask new];
            capture.executableURL = [NSURL fileURLWithPath:@"/usr/sbin/screencapture"];
            capture.arguments = @[@"-x", @"-o", @"-l", [NSString stringWithFormat:@"%ld", (long)guide.window.windowNumber], [NSString stringWithUTF8String:argv[1]]];
            NSError *captureError = nil;
            NSCAssert([capture launchAndReturnError:&captureError], @"%@", captureError);
            [capture waitUntilExit];
            NSCAssert(capture.terminationStatus == 0, @"Window capture failed (screen-recording permission may be needed)");
        }
        guide.wgsChoice.state = NSControlStateValueOn;
        [guide prepareSetup:nil];
        NSCAssert(guide.wgsQueued && [guide.lastAction isEqual:@"prepare"] && guide.posts == 0, @"Essentials before WGS");
        guide.snapshot = @{@"phase": @"preparing"}; [guide refreshStatus:nil];
        NSCAssert(guide.posts == 0 && !guide.openButton.enabled, @"Do not bypass essentials");
        guide.snapshot = @{@"phase": @"ready"}; [guide refreshStatus:nil];
        NSCAssert(guide.posts == 1 && !guide.wgsQueued && guide.openButton.enabled, @"Start WGS once; review remains available");
        [guide refreshStatus:nil];
        NSCAssert(guide.posts == 1 && !guide.retryButton.enabled, @"No duplicate starts");
        NSCAssert([guide.wgsJobLabel.stringValue containsString:@"SpliceAI"], @"Named dataset progress");
        guide.plan = planWithJob(@{@"id": @"wgs", @"status": @"failed", @"error": @"Network interrupted"});
        [guide refreshStatus:nil];
        NSCAssert(guide.openButton.enabled && guide.retryButton.enabled, @"Optional failure cannot block review; allow retry");
        guide.postError = @"Not enough space"; [guide prepareSetup:nil]; [guide refreshStatus:nil];
        NSCAssert([guide.wgsJobLabel.stringValue containsString:@"Not enough space"], @"Polls must preserve action errors");
        guide.postError = nil; [guide prepareSetup:nil];
        NSCAssert(guide.posts == 3 && [guide wgsActive], @"Explicit retry uses same installer");
        // An existing ready installation must not download anything by default.
        guide.plan = planWithJob(nil); guide.wgsChoice.state = NSControlStateValueOff;
        guide.wgsError = nil; [guide refreshStatus:nil];
        NSCAssert(guide.posts == 3 && !guide.retryButton.enabled, @"Later setup remains opt-in");
        guide.wgsChoice.state = NSControlStateValueOn; [guide wgsChanged:nil];
        NSCAssert(guide.retryButton.enabled, @"Ready installations can opt in later");
        guide.plan = @{@"known": @NO, @"error": @"Cannot inspect datasets"}; [guide refreshStatus:nil];
        NSCAssert(!guide.retryButton.enabled && guide.openButton.enabled, @"Unknown WGS state does not block essentials");
        guide.plan = @{@"known": @YES, @"installed": @YES}; [guide refreshStatus:nil];
        NSCAssert(!guide.retryButton.enabled && !guide.wgsChoice.enabled, @"Installed datasets are reused");
        [guide.window orderOut:nil];
        puts("NATIVE WGS SETUP PASSED: default off, scroll layout, essential handoff, later opt-in, progress, failure, retry, installed reuse");
    }
    return 0;
}
