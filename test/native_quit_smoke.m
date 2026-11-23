// Exercise AppKit termination against an isolated real supervisor and service.
#define main guide_launcher_main
#include "../desktop/macos/GUIDE-IEI-App.m"
#undef main
#import <objc/runtime.h>
#include <unistd.h>
#include <signal.h>

static BOOL inDelegate = NO;
static BOOL sawLater = NO;
static BOOL sawRaceFailure = NO;
static BOOL supervisorStopped = NO;
static NSModalResponse confirmQuit(id alert, SEL selector) { (void)alert; (void)selector; return NSAlertFirstButtonReturn; }
@interface QuitApplication : NSApplication
@end
@implementation QuitApplication
- (void)replyToApplicationShouldTerminate:(BOOL)answer {
    fprintf(stderr, "AppKit termination reply: %d\n", answer);
    NSCAssert(!inDelegate && sawLater, @"AppKit reply must follow NSTerminateLater");
    if (!answer) sawRaceFailure = YES;
    [super replyToApplicationShouldTerminate:answer];
}
@end
@interface QuitGuide : GuideApp
@property(nonatomic, copy) NSString *mode;
@property BOOL simulateRace;
@property NSUInteger baseReads;
@end
@implementation QuitGuide
- (void)alert:(NSString *)message {
    if ([self.mode isEqual:@"race"] && sawRaceFailure) {
        NSCAssert([message containsString:@"Starting"], @"Expected missing endpoint error");
        self.simulateRace = NO; self.ownsService = YES;
        [NSTimer scheduledTimerWithTimeInterval:0.05 target:NSApp selector:@selector(terminate:) userInfo:nil repeats:NO];
        return;
    }
    fprintf(stderr, "Unexpected alert: %s\n", message.UTF8String); _exit(3);
}
- (NSURL *)baseURL {
    NSURL *base = [super baseURL];
    if (self.simulateRace && ++self.baseReads == 1)
        [NSFileManager.defaultManager removeItemAtPath:self.endpointPath error:nil];
    return base;
}
- (void)applicationDidFinishLaunching:(NSNotification *)note {
    (void)note;
    NSDictionary *env = NSProcessInfo.processInfo.environment;
    NSString *tmp = env[@"QUIT_TEST_DIR"];
    self.root = env[@"QUIT_TEST_ROOT"]; self.instanceID = @"native-quit-test";
    self.startupPath = [tmp stringByAppendingPathComponent:@"status.json"];
    self.controlPath = [tmp stringByAppendingPathComponent:@"control.json"];
    self.endpointPath = [tmp stringByAppendingPathComponent:@"endpoint.json"];
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 200, 100) styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
    [self.window makeKeyAndOrderFront:nil];
    NSCAssert(![self windowShouldClose:self.window] && !self.window.visible && !self.quitting, @"Closing controls must keep service running");
    self.worker = [NSTask new]; self.worker.executableURL = [NSURL fileURLWithPath:env[@"QUIT_TEST_PYTHON"]];
    self.worker.arguments = @[@"-B", @"-m", @"local_service.desktop_app"];
    self.worker.currentDirectoryURL = [NSURL fileURLWithPath:self.root];
    NSMutableDictionary *workerEnv = [env mutableCopy];
    [workerEnv addEntriesFromDictionary:@{@"HOME":tmp, @"IEI_APP_SUPPORT_DIR":tmp,
        @"IEI_WORKBENCH_STATE_DIR":[tmp stringByAppendingPathComponent:@"data"],
        @"IEI_WORKBENCH_STORAGE_REGISTRY":[tmp stringByAppendingPathComponent:@"storage.json"],
        @"IEI_DEFAULT_ANNOTATION_ROOT":[tmp stringByAppendingPathComponent:@"refs"],
        @"IEI_UI_PORT":env[@"QUIT_TEST_PORT"], @"IEI_DESKTOP_INSTANCE_ID":self.instanceID,
        @"IEI_DESKTOP_STARTUP_STATUS":self.startupPath, @"IEI_DESKTOP_STARTUP_CONTROL":self.controlPath,
        @"IEI_DESKTOP_ENDPOINT_FILE":self.endpointPath, @"IEI_DESKTOP_SETUP":@"0",
        @"IEI_DESKTOP_NO_BROWSER":@"1", @"IEI_AUTO_START_DOCKER":@"0", @"IEI_MAC_TOOL_DISCOVERY":@"0"}];
    self.worker.environment = workerEnv;
    __weak QuitGuide *weak = self;
    self.worker.terminationHandler = ^(NSTask *task) { dispatch_async(dispatch_get_main_queue(), ^{
        fprintf(stderr, "Supervisor exited: %d, quitting=%d\n", task.terminationStatus, weak.quitting);
        [NSFileManager.defaultManager removeItemAtPath:[tmp stringByAppendingPathComponent:@"worker.pid"] error:nil];
        NSCAssert(task.terminationStatus == 0 && weak.quitting, @"Supervisor should stop after confirmed Quit");
        supervisorStopped = YES;
        [NSApp replyToApplicationShouldTerminate:YES];
    }); };
    NSError *error = nil; NSCAssert([self.worker launchAndReturnError:&error], @"%@", error);
    [[NSString stringWithFormat:@"%d", self.worker.processIdentifier] writeToFile:[tmp stringByAppendingPathComponent:@"worker.pid"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
    [NSTimer scheduledTimerWithTimeInterval:0.1 target:self selector:@selector(waitReady:) userInfo:nil repeats:YES];
}
- (void)waitReady:(NSTimer *)timer {
    NSCAssert(self.worker.running, @"Supervisor exited before ready");
    if (![[self startupStatus][@"phase"] isEqual:@"ready"] || !self.baseURL) return;
    [timer invalidate]; self.ownsService = YES; self.preparing = NO;
    if ([self.mode isEqual:@"race"]) { self.ownsService = NO; self.simulateRace = YES; }
    if ([self.mode hasPrefix:@"restart"]) {
        [self request:@"/api/service/restart" body:@{} done:^(NSDictionary *value, NSString *failure) {
            NSCAssert(!failure && [value[@"restarting"] boolValue], @"Restart should be accepted: %@", failure);
            if ([self.mode isEqual:@"restart-gap"]) {
                // Hold only our supervisor while the old service shuts down,
                // then queue Quit with the endpoint absent before resuming it.
                NSCAssert(kill(self.worker.processIdentifier, SIGSTOP) == 0, @"Pause test supervisor");
                [NSTimer scheduledTimerWithTimeInterval:1.5 repeats:NO block:^(NSTimer *ignored) {
                    (void)ignored;
                    [NSFileManager.defaultManager removeItemAtPath:self.endpointPath error:nil];
                    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 5), dispatch_get_main_queue(), ^{
                        kill(self.worker.processIdentifier, SIGCONT);
                    });
                    [NSApp terminate:nil];
                }];
            } else {
                // The old endpoint may still exist while its service is shutting down.
                [NSTimer scheduledTimerWithTimeInterval:0.05 target:NSApp selector:@selector(terminate:) userInfo:nil repeats:NO];
            }
        }];
    } else {
        if ([self.mode isEqual:@"gap"]) [NSFileManager.defaultManager removeItemAtPath:self.endpointPath error:nil];
        [NSApp terminate:nil];
    }
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    inDelegate = YES;
    NSApplicationTerminateReply result = [super applicationShouldTerminate:app];
    fprintf(stderr, "Termination delegate returned: %ld\n", (long)result);
    inDelegate = NO; sawLater = result == NSTerminateLater;
    NSCAssert(sawLater, @"Quit must wait for the supervisor");
    return result;
}
- (void)applicationWillTerminate:(NSNotification *)note {
    (void)note;
    if (!supervisorStopped) { fprintf(stderr, "Application terminated before its supervisor\n"); _exit(5); }
    NSCAssert(![self.mode isEqual:@"race"] || sawRaceFailure, @"Missing endpoint must cancel asynchronously");
    puts("NATIVE QUIT PASSED");
}
@end
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSCAssert(argc == 2, @"mode required"); alarm(45);
        method_setImplementation(class_getInstanceMethod(NSAlert.class, @selector(runModal)), (IMP)confirmQuit);
        NSApplication *app = [QuitApplication sharedApplication]; [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        __attribute__((objc_precise_lifetime)) QuitGuide *guide = [QuitGuide new];
        guide.mode = [NSString stringWithUTF8String:argv[1]]; app.delegate = guide; [app run];
    }
    return 0;
}
