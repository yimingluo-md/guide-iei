// Native lifecycle for the self-contained edition. The review UI stays in the browser.
#import <Cocoa/Cocoa.h>

@interface GuideApp : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSTask *worker;
@property(nonatomic, strong) NSTask *setup;
@property(nonatomic, copy) NSString *root;
@property(nonatomic, copy) NSString *python;
@property(nonatomic, copy) NSString *logPath;
@property BOOL quitting;
@end

@implementation GuideApp
- (void)alert:(NSString *)message {
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"GUIDE-IEI";
    alert.informativeText = message;
    [alert runModal];
}
- (void)openReview:(id)sender {
    NSString *port = NSProcessInfo.processInfo.environment[@"IEI_UI_PORT"] ?: @"3000";
    [NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:[@"http://127.0.0.1:" stringByAppendingString:port]]];
}
- (void)openLog:(id)sender {
    [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:self.logPath]];
}
- (NSTask *)task:(NSString *)program arguments:(NSArray *)arguments {
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:program];
    task.arguments = arguments;
    task.currentDirectoryURL = [NSURL fileURLWithPath:self.root];
    NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy];
    env[@"PYTHONDONTWRITEBYTECODE"] = @"1";
    env[@"PYTHONNOUSERSITE"] = @"1";
    [env removeObjectForKey:@"PYTHONPATH"];
    [env removeObjectForKey:@"PYTHONHOME"];
    env[@"IEI_DESKTOP_APP"] = @"1";
    env[@"IEI_PYTHON_BIN"] = self.python;
    env[@"PATH"] = [NSString stringWithFormat:@"%@:%@/.iei-variant-review/tools/bin:/usr/bin:/bin:/usr/sbin:/sbin", self.python.stringByDeletingLastPathComponent, NSHomeDirectory()];
    task.environment = env;
    NSFileHandle *log = [NSFileHandle fileHandleForWritingAtPath:self.logPath];
    [log seekToEndOfFile];
    task.standardOutput = log;
    task.standardError = log;
    return task;
}
- (void)prepareAnnotation:(id)sender {
    if (self.setup.running) { [self openLog:nil]; return; }
    NSAlert *confirm = [NSAlert new];
    confirm.messageText = @"Prepare annotation environment?";
    confirm.informativeText = @"This downloads the container tools and prepares the annotation engine. Large annotation databases are selected separately in the workbench. Review of an annotated VCF does not need this step.";
    [confirm addButtonWithTitle:@"Prepare"];
    [confirm addButtonWithTitle:@"Cancel"];
    if ([confirm runModal] != NSAlertFirstButtonReturn) return;
    self.setup = [self task:@"/bin/bash" arguments:@[[self.root stringByAppendingPathComponent:@"scripts/setup_environment.sh"], @"--install", @"--yes"]];
    __weak GuideApp *weak = self;
    self.setup.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!weak.quitting) [weak alert:task.terminationStatus == 0 ? @"Annotation environment prepared. Select your datasets in the workbench." : @"Annotation setup needs attention. Choose Open Log for details, then retry Prepare Annotation Environment."];
        });
    };
    NSError *error = nil;
    if (![self.setup launchAndReturnError:&error]) [self alert:error.localizedDescription];
    else [self openLog:nil];
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSString *resources = NSBundle.mainBundle.resourcePath;
    self.root = [resources stringByAppendingPathComponent:@"application"];
    self.python = [NSBundle.mainBundle.bundlePath stringByAppendingPathComponent:@"Contents/Frameworks/Python.framework/Versions/Current/bin/python3"];
    NSString *support = NSProcessInfo.processInfo.environment[@"IEI_APP_SUPPORT_DIR"] ?: [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/GUIDE-IEI"];
    NSString *logs = [support stringByAppendingPathComponent:@"logs"];
    NSError *error = nil;
    if (![NSFileManager.defaultManager createDirectoryAtPath:logs withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:&error]) {
        [self alert:error.localizedDescription]; [NSApp terminate:nil]; return;
    }
    self.logPath = [logs stringByAppendingPathComponent:[NSString stringWithFormat:@"desktop-%@.log", NSUUID.UUID.UUIDString]];
    [NSFileManager.defaultManager createFileAtPath:self.logPath contents:nil attributes:@{NSFilePosixPermissions:@0600}];
    NSMenu *bar = [NSMenu new];
    NSMenuItem *top = [NSMenuItem new];
    NSMenu *menu = [NSMenu new];
    [menu addItemWithTitle:@"Open Workbench" action:@selector(openReview:) keyEquivalent:@"o"].target = self;
    [menu addItemWithTitle:@"Prepare Annotation Environment…" action:@selector(prepareAnnotation:) keyEquivalent:@""].target = self;
    [menu addItemWithTitle:@"Open Log" action:@selector(openLog:) keyEquivalent:@"l"].target = self;
    [menu addItem:NSMenuItem.separatorItem];
    [menu addItemWithTitle:@"Quit GUIDE-IEI" action:@selector(terminate:) keyEquivalent:@"q"];
    top.submenu = menu; [bar addItem:top]; NSApp.mainMenu = bar;
    self.worker = [self task:self.python arguments:@[@"-s", @"-B", @"-m", @"local_service.desktop_app"]];
    __weak GuideApp *weak = self;
    self.worker.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!weak.quitting) {
                if (task.terminationStatus != 0) [weak alert:[@"The workbench stopped. Open this log for details: " stringByAppendingString:weak.logPath]];
                [NSApp terminate:nil];
            } else [NSApp replyToApplicationShouldTerminate:YES];
        });
    };
    if (![self.worker launchAndReturnError:&error]) {
        [self alert:error.localizedDescription]; [NSApp terminate:nil];
    }
}
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {
    [self openReview:nil]; return NO;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    if (self.setup.running) {
        [self alert:@"Annotation environment preparation is still running. Wait for it to finish before quitting; progress is in Open Log."];
        return NSTerminateCancel;
    }
    if (self.worker.running) {
        if (!self.quitting) {
            NSAlert *confirm = [NSAlert new];
            confirm.messageText = @"Quit GUIDE-IEI?";
            confirm.informativeText = @"Active annotation jobs and downloads will be interrupted. Save any Review once work before quitting.";
            [confirm addButtonWithTitle:@"Quit"]; [confirm addButtonWithTitle:@"Cancel"];
            if ([confirm runModal] != NSAlertFirstButtonReturn) return NSTerminateCancel;
        }
        self.quitting = YES; [self.worker terminate]; return NSTerminateLater;
    }
    self.quitting = YES; return NSTerminateNow;
}
@end

int main(int argc, const char *argv[]) {
    (void)argc; (void)argv;
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        GuideApp *delegate = [GuideApp new]; app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular]; [app run];
    }
    return 0;
}
