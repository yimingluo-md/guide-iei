// Native lifecycle for the self-contained edition. The review UI stays in the browser.
#import <Cocoa/Cocoa.h>

@interface GuideApp : NSObject <NSApplicationDelegate, NSWindowDelegate, NSTabViewDelegate>
@property(nonatomic, strong) NSTask *worker;
@property(nonatomic, copy) NSString *root;
@property(nonatomic, copy) NSString *python;
@property(nonatomic, copy) NSString *logPath;
@property(nonatomic, copy) NSString *instanceID;
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) NSTextField *statusLabel;
@property(nonatomic, strong) NSButton *openButton;
@property(nonatomic, strong) NSButton *retryButton;
@property(nonatomic, strong) NSTextField *dataLabel;
@property(nonatomic, strong) NSTextField *storageLabel;
@property(nonatomic, strong) NSPopUpButton *storageKind;
@property(nonatomic, strong) NSButton *changeStorageButton;
@property(nonatomic, strong) NSButton *moveStorageButton;
@property(nonatomic, strong) NSButton *restartStorageButton;
@property(nonatomic, strong) NSTabView *tabs;
@property(nonatomic, copy) NSString *migrationID;
@property(nonatomic, strong) NSProgressIndicator *progress;
@property(nonatomic, copy) NSString *startupPath;
@property(nonatomic, copy) NSString *controlPath;
@property BOOL preparing;
@property(nonatomic, strong) NSTimer *timer;
@property BOOL polling;
@property BOOL ownsService;
@property BOOL quitting;
@end

@implementation GuideApp
- (NSDictionary *)startupStatus {
    NSData *data = [NSData dataWithContentsOfFile:self.startupPath];
    id value = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    return [value isKindOfClass:NSDictionary.class] ? value : @{};
}
- (void)startupAction:(NSString *)action {
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"action": action} options:0 error:nil];
    NSError *error = nil;
    if (![data writeToFile:self.controlPath options:NSDataWritingAtomic error:&error]) [self alert:error.localizedDescription];
}
- (void)retrySetup:(id)sender { [self startupAction:@"retry"]; self.retryButton.enabled = NO; }
- (void)prepareSetup:(id)sender { [self startupAction:@"prepare"]; self.retryButton.enabled = NO; }
- (void)showStorage:(id)sender { [self.tabs selectTabViewItemAtIndex:1]; [self refreshStorage:nil]; }
- (void)tabView:(NSTabView *)tabView didSelectTabViewItem:(NSTabViewItem *)item {
    (void)tabView;
    if ([item.identifier isEqual:@"storage"]) [self refreshStorage:nil];
}
- (NSString *)storageID { return @[@"annotation", @"data", @"temporary"][self.storageKind.indexOfSelectedItem]; }
- (void)refreshStorage:(id)sender {
    [self request:@"/api/storage/locations" body:nil done:^(NSDictionary *value, NSString *error) {
        if (error) { self.storageLabel.stringValue = error; return; }
        for (NSDictionary *location in value[@"locations"]) {
            if ([location[@"id"] isEqual:[self storageID]]) {
                double free = [location[@"free_bytes"] doubleValue] / (1024 * 1024 * 1024);
                self.storageLabel.stringValue = [NSString stringWithFormat:@"%@\n%.1f GiB free", location[@"path"] ?: @"Unavailable", free];
            }
        }
    }];
}
- (void)changeStorage:(id)sender {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseDirectories = YES; panel.canChooseFiles = NO; panel.canCreateDirectories = YES;
    panel.prompt = @"Use this folder";
    panel.message = @"Select a dedicated data folder. This changes the location without moving or deleting existing files.";
    if ([panel runModal] != NSModalResponseOK) return;
    [self request:@"/api/storage/location" body:@{@"kind": [self storageID], @"path": panel.URL.path}
        done:^(NSDictionary *value, NSString *error) {
            (void)value;
            if (error) [self alert:error];
            else { [self refreshStorage:nil]; [self alert:@"Location saved. Click Restart to use this location. Existing files have not been moved or deleted."]; }
        }];
}
- (void)moveStorage:(id)sender {
    NSSavePanel *panel = [NSSavePanel savePanel];
    panel.nameFieldStringValue = [@"GUIDE-IEI-" stringByAppendingString:[self storageID]];
    panel.prompt = @"Copy and verify";
    panel.message = @"Choose a new destination folder. Existing data will be copied and verified; the original is retained. Restart after completion.";
    if ([panel runModal] != NSModalResponseOK) return;
    [self request:@"/api/storage/migrate" body:@{@"kind": [self storageID], @"path": panel.URL.path}
        done:^(NSDictionary *value, NSString *error) {
            if (error) [self alert:error];
            else { self.migrationID = value[@"id"]; self.storageLabel.stringValue = @"Copying and verifying existing data…"; }
        }];
}
- (void)restartStorage:(id)sender {
    [self request:@"/api/service/restart" body:@{} done:^(NSDictionary *value, NSString *error) {
        (void)value;
        if (error) [self alert:error];
        else { self.preparing = YES; self.openButton.enabled = NO; self.statusLabel.stringValue = @"Restarting with the selected storage locations…"; }
    }];
}
- (NSURL *)baseURL {
    NSInteger port = [NSProcessInfo.processInfo.environment[@"IEI_UI_PORT"] ?: @"3000" integerValue];
    return [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld", (long)port]];
}
- (void)showControls:(id)sender {
    [self.window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES];
}
- (void)request:(NSString *)route body:(NSDictionary *)body done:(void (^)(NSDictionary *, NSString *))done {
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:route relativeToURL:self.baseURL]];
    request.timeoutInterval = 5;
    if (body) {
        request.HTTPMethod = @"POST";
        [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
        request.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil];
    }
    [[NSURLSession.sharedSession dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        id value = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        NSDictionary *object = [value isKindOfClass:NSDictionary.class] ? value : nil;
        NSInteger status = [(NSHTTPURLResponse *)response statusCode];
        NSString *failure = error.localizedDescription;
        if (!failure && (!object || status < 200 || status >= 300))
            failure = [object[@"error"] isKindOfClass:NSString.class] ? object[@"error"] : @"The workbench did not answer. Check Open Log.";
        dispatch_async(dispatch_get_main_queue(), ^{ done(object, failure); });
    }] resume];
}
- (void)refreshStatus:(NSTimer *)timer {
    if (self.polling || self.quitting || !self.worker.running) return;
    self.polling = YES;
    [self request:@"/api/service/status" body:nil done:^(NSDictionary *value, NSString *error) {
        self.polling = NO;
        if (self.quitting || !self.worker.running) return;
        if (error || ![value[@"instance_id"] isEqual:self.instanceID]) {
            self.statusLabel.stringValue = self.ownsService ? @"Reconnecting to the workbench…" : @"Starting the workbench…";
            return;
        }
        self.ownsService = YES;
        NSDictionary *startup = [self startupStatus];
        NSString *phase = startup[@"phase"];
        BOOL idle = [phase isEqual:@"waiting"] || [phase isEqual:@"failed"] || [phase isEqual:@"ready"];
        self.changeStorageButton.enabled = idle && !self.migrationID;
        self.moveStorageButton.enabled = idle && !self.migrationID;
        self.restartStorageButton.enabled = idle && !self.migrationID;
        if (self.migrationID) {
            [self request:@"/api/storage/migrations" body:nil done:^(NSDictionary *data, NSString *failure) {
                if (failure) { self.storageLabel.stringValue = failure; return; }
                for (NSDictionary *job in data[@"jobs"]) if ([job[@"id"] isEqual:self.migrationID]) {
                    self.storageLabel.stringValue = [job[@"error"] length] ? job[@"error"] : job[@"message"] ?: @"Copying and verifying…";
                    if ([job[@"status"] isEqual:@"succeeded"] || [job[@"status"] isEqual:@"failed"]) {
                        self.storageLabel.stringValue = [job[@"status"] isEqual:@"succeeded"] ? @"Copy verified. Original retained. Click Restart to use the new location." : job[@"error"];
                        self.migrationID = nil;
                    }
                }
            }];
        }
        if (startup[@"annotation_path"]) {
            self.dataLabel.stringValue = [NSString stringWithFormat:@"Data location: %@\n%.1f GiB free · estimated setup allowance %.1f GiB",
                startup[@"annotation_path"], [startup[@"free_bytes"] doubleValue] / (1024*1024*1024),
                [startup[@"estimated_required_bytes"] doubleValue] / (1024*1024*1024)];
        }
        if (self.preparing && ![phase isEqual:@"ready"]) {
            self.statusLabel.stringValue = startup[@"message"] ?: @"Preparing GUIDE-IEI for first use. Checking installed components…";
            self.retryButton.hidden = ![phase isEqual:@"failed"] && ![phase isEqual:@"waiting"];
            self.retryButton.title = [phase isEqual:@"failed"] ? @"Retry preparation" : @"Prepare GUIDE-IEI";
            self.retryButton.enabled = [phase isEqual:@"failed"] || [startup[@"can_prepare"] boolValue];
            self.progress.hidden = [phase isEqual:@"failed"] || [phase isEqual:@"waiting"];
            if (!self.progress.hidden) [self.progress startAnimation:nil];
            return;
        }
        self.preparing = NO;
        self.retryButton.hidden = YES;
        [self.progress stopAnimation:nil]; self.progress.hidden = YES;
        self.openButton.enabled = YES;
        NSArray *blockers = [value[@"blockers"] isKindOfClass:NSArray.class] ? value[@"blockers"] : @[];
        NSInteger jobs = [value[@"annotations"] integerValue], downloads = [value[@"downloads"] integerValue];
        self.statusLabel.stringValue = blockers.count ? [@"Working: " stringByAppendingString:[blockers componentsJoinedByString:@", "]] :
            jobs + downloads ? [NSString stringWithFormat:@"Running · %ld annotation job(s) · %ld dataset job(s)", (long)jobs, (long)downloads] : @"Running · no active jobs";
    }];
}
- (void)buildControls {
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 680, 620)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"GUIDE-IEI"; self.window.delegate = self; self.window.releasedWhenClosed = NO;
    NSTextField *heading = [NSTextField labelWithString:@"GUIDE-IEI Workbench"]; heading.font = [NSFont boldSystemFontOfSize:22];
    self.statusLabel = [NSTextField wrappingLabelWithString:@"Starting the workbench…"];
    self.statusLabel.maximumNumberOfLines = 5;
    self.statusLabel.accessibilityLabel = @"Workbench status";
    NSTextField *hint = [NSTextField wrappingLabelWithString:@"Prepare the annotation environment and essential datasets in one step. Internet access is required. Compatible installed components are reused. Advanced datasets remain available inside the analysis workbench.\n\nClosing the browser leaves GUIDE-IEI running. Use Quit to stop it; Docker remains available to other applications."];
    hint.textColor = NSColor.secondaryLabelColor;
    self.openButton = [NSButton buttonWithTitle:@"Open Workbench" target:self action:@selector(openReview:)]; self.openButton.enabled = NO;
    NSButton *quit = [NSButton buttonWithTitle:@"Quit GUIDE-IEI" target:NSApp action:@selector(terminate:)];
    NSButton *log = [NSButton buttonWithTitle:@"Open Log" target:self action:@selector(openLog:)];
    NSStackView *buttons = [NSStackView stackViewWithViews:@[self.openButton, log, quit]];
    buttons.orientation = NSUserInterfaceLayoutOrientationHorizontal; buttons.spacing = 12;
    self.retryButton = [NSButton buttonWithTitle:@"Prepare GUIDE-IEI" target:self action:@selector(prepareSetup:)]; self.retryButton.hidden = YES;
    NSStackView *setupButtons = [NSStackView stackViewWithViews:@[self.retryButton]];
    setupButtons.orientation = NSUserInterfaceLayoutOrientationHorizontal; setupButtons.spacing = 12;
    self.progress = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(0, 0, 510, 12)];
    self.progress.indeterminate = YES; self.progress.style = NSProgressIndicatorStyleBar;
    [self.progress.widthAnchor constraintEqualToConstant:510].active = YES;
    self.progress.hidden = !self.preparing;
    if (self.preparing) [self.progress startAnimation:nil];
    self.dataLabel = [NSTextField wrappingLabelWithString:@"Checking the configured data location…"];
    NSButton *change = [NSButton buttonWithTitle:@"Change…" target:self action:@selector(showStorage:)];
    NSStackView *setup = [NSStackView stackViewWithViews:@[self.dataLabel, change, hint, setupButtons]];
    setup.orientation = NSUserInterfaceLayoutOrientationVertical; setup.alignment = NSLayoutAttributeLeading; setup.spacing = 16;
    self.storageKind = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
    [self.storageKind addItemsWithTitles:@[@"Annotation datasets", @"Sample Library & Cohort", @"Temporary workspace"]];
    self.storageKind.target = self; self.storageKind.action = @selector(refreshStorage:);
    self.storageLabel = [NSTextField wrappingLabelWithString:@"Choose a storage category to inspect or change its location."];
    self.changeStorageButton = [NSButton buttonWithTitle:@"Change location (without moving files)…" target:self action:@selector(changeStorage:)];
    self.moveStorageButton = [NSButton buttonWithTitle:@"Copy existing data to a new location…" target:self action:@selector(moveStorage:)];
    self.restartStorageButton = [NSButton buttonWithTitle:@"Restart to apply storage changes" target:self action:@selector(restartStorage:)];
    NSStackView *storage = [NSStackView stackViewWithViews:@[self.storageKind, self.storageLabel, self.changeStorageButton, self.moveStorageButton, self.restartStorageButton]];
    storage.orientation = NSUserInterfaceLayoutOrientationVertical; storage.alignment = NSLayoutAttributeLeading; storage.spacing = 18;
    self.tabs = [[NSTabView alloc] initWithFrame:NSMakeRect(0,0,630,335)];
    NSTabViewItem *setupTab = [[NSTabViewItem alloc] initWithIdentifier:@"setup"]; setupTab.label = @"Setup"; setupTab.view = setup;
    NSTabViewItem *storageTab = [[NSTabViewItem alloc] initWithIdentifier:@"storage"]; storageTab.label = @"Storage"; storageTab.view = storage;
    [self.tabs addTabViewItem:setupTab]; [self.tabs addTabViewItem:storageTab];
    self.tabs.delegate = self;
    [self.tabs.heightAnchor constraintEqualToConstant:335].active = YES;
    [self.tabs.widthAnchor constraintEqualToConstant:630].active = YES;
    [hint.widthAnchor constraintEqualToConstant:590].active = YES;
    [self.dataLabel.widthAnchor constraintEqualToConstant:590].active = YES;
    [self.storageLabel.widthAnchor constraintEqualToConstant:590].active = YES;
    NSStackView *stack = [NSStackView stackViewWithViews:@[heading, self.statusLabel, self.progress, self.tabs, buttons]];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical; stack.alignment = NSLayoutAttributeLeading; stack.spacing = 18;
    stack.translatesAutoresizingMaskIntoConstraints = NO; [self.window.contentView addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:self.window.contentView.leadingAnchor constant:24],
        [stack.trailingAnchor constraintEqualToAnchor:self.window.contentView.trailingAnchor constant:-24],
        [stack.topAnchor constraintEqualToAnchor:self.window.contentView.topAnchor constant:24],
        [self.statusLabel.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
        [stack.bottomAnchor constraintLessThanOrEqualToAnchor:self.window.contentView.bottomAnchor constant:-20]]];
    [self.window center]; [self showControls:nil];
}
- (void)alert:(NSString *)message {
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"GUIDE-IEI";
    alert.informativeText = message;
    [alert runModal];
}
- (void)openReview:(id)sender {
    if (self.preparing && self.worker.running) { [self showControls:nil]; return; }
    if (self.worker.running && !self.ownsService) { [self showControls:nil]; return; }
    [NSWorkspace.sharedWorkspace openURL:self.baseURL];
}
- (void)openLog:(id)sender {
    NSString *setupLog = [self startupStatus][@"log_path"];
    NSString *path = setupLog.length && [NSFileManager.defaultManager fileExistsAtPath:setupLog] ? setupLog : self.logPath;
    [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:path]];
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
    env[@"IEI_DESKTOP_INSTANCE_ID"] = self.instanceID;
    env[@"IEI_DESKTOP_STARTUP_STATUS"] = self.startupPath;
    env[@"IEI_DESKTOP_STARTUP_CONTROL"] = self.controlPath;
    env[@"PATH"] = [NSString stringWithFormat:@"%@:%@/.iei-variant-review/tools/bin:/usr/bin:/bin:/usr/sbin:/sbin:%@/.docker/bin:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin", self.python.stringByDeletingLastPathComponent, NSHomeDirectory(), NSHomeDirectory()];
    task.environment = env;
    NSFileHandle *log = [NSFileHandle fileHandleForWritingAtPath:self.logPath];
    [log seekToEndOfFile];
    task.standardOutput = log;
    task.standardError = log;
    return task;
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.instanceID = NSUUID.UUID.UUIDString;
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
    self.startupPath = [logs stringByAppendingPathComponent:[NSString stringWithFormat:@"startup-%@.json", self.instanceID]];
    self.controlPath = [logs stringByAppendingPathComponent:[NSString stringWithFormat:@"startup-%@.control.json", self.instanceID]];
    self.preparing = ![NSProcessInfo.processInfo.environment[@"IEI_DESKTOP_SETUP"] isEqual:@"0"];
    [NSFileManager.defaultManager createFileAtPath:self.logPath contents:nil attributes:@{NSFilePosixPermissions:@0600}];
    NSMenu *bar = [NSMenu new];
    NSMenuItem *top = [NSMenuItem new];
    NSMenu *menu = [NSMenu new];
    [menu addItemWithTitle:@"Show Workbench Controls" action:@selector(showControls:) keyEquivalent:@""].target = self;
    [menu addItemWithTitle:@"Open Workbench" action:@selector(openReview:) keyEquivalent:@"o"].target = self;
    [menu addItemWithTitle:@"Open Log" action:@selector(openLog:) keyEquivalent:@"l"].target = self;
    [menu addItem:NSMenuItem.separatorItem];
    [menu addItemWithTitle:@"Quit GUIDE-IEI" action:@selector(terminate:) keyEquivalent:@"q"];
    top.submenu = menu; [bar addItem:top]; NSApp.mainMenu = bar;
    [self buildControls];
    self.worker = [self task:self.python arguments:@[@"-s", @"-B", @"-m", @"local_service.desktop_app"]];
    __weak GuideApp *weak = self;
    self.worker.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [weak.timer invalidate];
            if (!weak.quitting) {
                int code = task.terminationStatus;
                if (code == 43) {
                    [NSApp activateIgnoringOtherApps:YES];
                    NSAlert *alert = [NSAlert new]; alert.messageText = @"GUIDE-IEI is already running";
                    alert.informativeText = @"A different or older build is running. You can open it now. To use this build, quit the running workbench first using its Quit button or Dock menu. For an older Terminal launcher, press Control-C in that Terminal. No running work has been stopped.";
                    [alert addButtonWithTitle:@"Open running workbench"]; [alert addButtonWithTitle:@"Close this copy"];
                    if ([alert runModal] == NSAlertFirstButtonReturn) [weak openReview:nil];
                } else if (code == 44) {
                    [weak alert:@"The workbench port is already in use by another application or an older launcher. No process was stopped. Quit any older GUIDE-IEI launcher before opening this app; if another application owns the port, leave it running and use a different GUIDE-IEI port."];
                } else if (code != 0 && code != 42) {
                    [NSApp activateIgnoringOtherApps:YES];
                    NSAlert *alert = [NSAlert new]; alert.messageText = weak.ownsService ? @"The workbench stopped" : @"The workbench could not start";
                    alert.informativeText = @"Open the startup log for the specific error. Your existing data have not been removed.";
                    [alert addButtonWithTitle:@"Open Log"]; [alert addButtonWithTitle:@"Close"];
                    if ([alert runModal] == NSAlertFirstButtonReturn) [weak openLog:nil];
                }
                [NSApp terminate:nil];
            } else [NSApp replyToApplicationShouldTerminate:YES];
        });
    };
    if (![self.worker launchAndReturnError:&error]) {
        [self alert:error.localizedDescription]; [NSApp terminate:nil]; return;
    }
    self.timer = [NSTimer scheduledTimerWithTimeInterval:2 target:self selector:@selector(refreshStatus:) userInfo:nil repeats:YES];
    [self refreshStatus:nil];
}
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {
    [self showControls:nil]; return YES;
}
- (BOOL)windowShouldClose:(NSWindow *)sender { [NSApp terminate:nil]; return NO; }
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    if (self.worker.running) {
        if (self.quitting) return NSTerminateLater;
        if (!self.quitting) {
            NSAlert *confirm = [NSAlert new];
            confirm.messageText = @"Quit GUIDE-IEI?";
            confirm.informativeText = @"Save or export any Review once work in every browser tab first. Running annotation jobs and dataset downloads will be interrupted; queued annotation jobs can resume on the next launch. Docker will remain running.";
            [confirm addButtonWithTitle:@"Quit GUIDE-IEI"]; [confirm addButtonWithTitle:@"Keep running"];
            if ([confirm runModal] != NSAlertFirstButtonReturn) return NSTerminateCancel;
        }
        self.quitting = YES; self.statusLabel.stringValue = @"Stopping GUIDE-IEI…";
        if (self.preparing) { [self startupAction:@"quit"]; return NSTerminateLater; }
        if (!self.ownsService) { [self.worker terminate]; return NSTerminateLater; }
        [self request:@"/api/service/quit" body:@{@"confirm": @YES, @"instance_id": self.instanceID} done:^(NSDictionary *value, NSString *failure) {
            (void)value;
            if (failure && self.worker.running) {
                self.quitting = NO; [NSApp replyToApplicationShouldTerminate:NO];
                [self alert:failure]; [self refreshStatus:nil];
            }
        }];
        return NSTerminateLater;
    }
    self.quitting = YES; return NSTerminateNow;
}
- (void)applicationWillTerminate:(NSNotification *)notification {
    [self.timer invalidate];
    [NSFileManager.defaultManager removeItemAtPath:self.startupPath error:nil];
    [NSFileManager.defaultManager removeItemAtPath:self.controlPath error:nil];
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
