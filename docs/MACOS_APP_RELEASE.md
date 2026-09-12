# Self-contained macOS app builds

The self-contained edition is currently a **release candidate packaging path**,
for the initial Apple Silicon beta. Release automation creates drafts only and
no longer builds the legacy source-payload Mac installer. Describe an artifact
as Developer ID signed or notarized only after verifying that actual artifact;
the preview label alone does not establish its signing status.

## Contents and operation

- Native Apple Silicon or Intel application launcher with Open Workbench,
  Open Log and Quit menu items, plus a visible
  control window that can be recalled from the Dock. The browser also has a
  Quit button; both paths require confirmation and share the service's
  busy-operation guards. Closing the native window requests Quit rather than
  hiding a running service.
- Pinned relocatable Python and PyYAML; no user-installed Python or Node needed.
- Prebuilt static workbench served by the local Python service on loopback.
- Prebuilt architecture-specific Linux VEP engine archive, integrity manifest
  and collected image license notices. No reference databases or volumes are
  included in the engine archive.
- A separate third-party source companion accompanies public binary releases.
  It is not needed for installation and is not bundled into the app. See the
  [engine redistribution review](BUNDLED_ENGINE_REDISTRIBUTION.md).
- Small public reference tables and public gene knowledge, documentation and
  third-party notices. No developer reference folder, private exports, patient
  libraries, credentials or results are copied into the application.
- The app opens the existing browser interface; it is not a native variant-table
  rewrite. Closing the browser does not stop the app; use Quit GUIDE-IEI.
- A bounded, patient-data-free lifecycle watch notifies open browser tabs when
  Quit is confirmed from either window. The shutdown notice keeps the loaded
  review mounted for viewing/export. Unconfirmed connection failures show a
  reconnecting notice instead; reopening reconnects without automatic reload.

Each package has a unique build ID in `desktop-build.json`. A second launch
reopens an identified instance of the same package; different/older builds
receive an explanatory dialog. An unrecognized occupied port never causes
process termination. Lifecycle status exposes job counts, not patient names.
The instance ID on a Quit request prevents accidentally stopping a replacement
instance; it is not authentication (see the single-user security model).

The self-contained app checks and prepares the annotation engine before opening
the browser, even when the intended task is already-annotated VCF review.
The native window shows named stages, Open Log, Retry preparation and Quit;
there is no skip option and old remembered deferrals are ignored. Existing
compatible images are reused. A missing/stale image is loaded from the app's
compressed archive after SHA-256, source identity and architecture checks.
The loaded tools/plugins are tested before the configured image tag is updated.
A missing or corrupt bundle fails with repair guidance, never a local build.

Existing selected Docker Desktop/Colima engines are started without changing
their configuration. On a clean Mac, setup installs pinned user-space
Lima/Colima/Docker CLI tools and creates a VM; Docker Desktop is not required.
Those runtime/VM downloads still require internet. Existing managed Colima
installations are recoverable after an interrupted first start: if the managed Docker
client still points at its unused built-in default connection and exactly one
Colima profile exists, GUIDE-IEI uses that profile's socket for its service and
all child jobs. This is a process-local selection, not a change to the user's
global Docker context. Explicit host/context overrides, working default engines,
Docker Desktop, unrecognized endpoints and ambiguous profiles are left alone.
Colima startup can take up to ten minutes while resuming VM preparation. Failed
startup details appear in the setup log and the native Retry window.

Application code runs on the Mac; containers do not need the app bundle or
`/Applications` shared. Fresh managed Colima VMs retain the home/temp mounts
and add a custom working folder only when needed. Setup verifies actual
bidirectional read/write access to `container-work` in the workbench state
directory, normally `~/.iei-variant-review/container-work`. When workspace access
fails, setup can repair the selected local Colima profile: it checks
for active containers, backs up the exact YAML, preserves settings and existing
mounts, adds missing workspace sharing, restarts that same profile without
switching contexts, and verifies workspace access. It does not make an existing
read-only parent mount writable automatically. Failed restart restores
the prior sharing configuration. A profile running other containers or
Kubernetes is not restarted; the startup window asks the user to finish those
workloads and choose Retry preparation. Configuration backups are private files
beside `colima.yaml`, named `colima.yaml.guide-iei-backup-*`.

Docker Desktop sharing permissions are not edited behind its UI; if necessary,
the startup window directs users to Settings → Resources → File sharing and
then Retry preparation. No Terminal command is required. Remote or unknown
runtime profiles are not reconfigured. An installed image alone is not considered
ready until workspace read/write preparation succeeds. Later readiness polls
only read a verified workspace marker and do not write or change sharing.
Quitting GUIDE-IEI does not
stop the Docker daemon or VM, which may be used by other applications.

For annotation, open **Import & QC → Set up annotation datasets**. If the
engine is missing or outdated, **Set up annotation engine** installs missing
Mac user-space container tools and loads the bundled VEP image after confirmation.
Progress, logs, and retry are on the same page; quit and conflicting data
changes are blocked until setup finishes. It does not install Node or change
an existing conda/Homebrew environment. Choose data locations in Storage and
then install datasets through the workbench. Large databases and registration-restricted exports remain
separate from the app. Container file-sharing checks still apply to actual
inputs, references, outputs and working directories, including external drives.
The HTS wrapper does not implicitly mount the app as its working directory.
Plugin verification checks the baked image plugins against host file hashes;
the optional AVI ZIP compiler stages and verifies its small C++ helper beside
the data before compiling/running it in the image. The Python coordinator runs
on the Mac; Python 3 inside the VEP image is not required.

The self-contained app uses bundle identifier `org.guide-iei.desktop`, distinct
from the source-tree launcher's `org.guide-iei.workbench`. This keeps macOS
Launch Services from treating the development shim as another copy of the
standalone app. Old Dock shortcuts may still point at the old source launcher;
remove that shortcut and add the installed standalone app instead. Do not
delete reference or patient-library folders when replacing the app.

Existing Storage selections and Sample Library paths are retained. On a fresh
machine the annotation default is
`~/Library/Application Support/GUIDE-IEI/references`; existing patient-library
defaults are unchanged. Logs are under that Application Support folder's
`logs` directory. Output defaults to a writable results folder in the workspace,
never inside the app. The source-tree installation is not copied or removed.

## Build a local preview

Build natively on the target architecture with Python 3.9+, Node/npm 22+ and
Xcode Command Line Tools. The user receiving the app needs none of these tools.
The build machine also needs a running Docker engine and a matching native
`vep-annotate:latest` image, built with `bash docker/build.sh` and validated with
the container regression suite. The app builder refuses a stale or wrong-
architecture image, smoke-tests it, and exports it into the build cache. It
never copies Docker volumes or commits a running container.

```bash
python3 scripts/build_macos_app.py --preview
```

This reads current working-tree application code (including UI edits) and the
allowlisted tracked pipeline/configuration/docs files. It builds the UI in a
temporary folder, leaving the running developer UI build unchanged. Exact
Python and wheel downloads are SHA-256 verified and cached in
`dist/macos-build-cache`. The output is architecture-labelled `.app`, `.zip`,
`.dmg` and checksum files under `dist/macos-app`. Existing output files are not
overwritten; choose another `--output` directory for a subsequent build.
The engine archive is cached by architecture and source fingerprint; a cache
with a different immutable image ID is rejected instead of silently reused.
For UI-only CI, `--preview --without-engine` deliberately omits the archive;
these builds cannot prepare annotation and are not distributable. Intel CI's
UI-only run therefore does not validate the Intel annotation engine.

Before public distribution, review licenses/source-offer obligations for the
complete Linux image (not just VEP), retain required notices and provide any
required corresponding source. `bundled-engine/IMAGE-NOTICES.txt` collects
available package/vendor notices, but is not a substitute for that review.
See the [version-specific review](BUNDLED_ENGINE_REDISTRIBUTION.md), including
its open source-delivery and legacy Kent licensing findings.
The sealed manifest and image archive live inside the app's signed resources;
changing either requires rebuilding, signing and notarizing the app. The app
retains the archive after Docker loads its copy, so allow disk space for both.

A preview has only an ad-hoc signature unless an explicit Developer ID identity
is supplied. It must not be published as the supported end-user installer.
This does not prohibit public beta releases: build the beta from clean committed
source without `--preview`, then mark the GitHub release **prerelease**. The
build's `preview` flag describes development provenance, not product maturity.

Validate the actual artifact without touching the workstation's active library:

```bash
python3 test/test_self_contained_macos.py /path/to/the-built.app
# Optional: also exercise the interface in an isolated headless Chrome session
IEI_TEST_PACKAGED_BROWSER=1 python3 test/test_self_contained_macos.py /path/to/the-built.app
```

The smoke test assigns a free loopback port and temporary empty storage. It
checks the bundled Python, interface/API, reference data and update guard,
then checks that the app signature remains valid after shutdown.

### Intel GitHub Actions smoke test

The `macos-app-intel` workflow builds natively on `macos-15-intel`, verifies the
launcher and bundled Python architecture, and runs the packaged-app and browser
smokes above. It runs on pushes to `main` and `codex/intel-app-smoke`, and supports
manual dispatch. Its 14-day artifacts are ad-hoc
signed test previews, not notarized end-user releases. No signing credentials
are uploaded to the runner.

This checks packaging and isolated startup on a hosted Intel runner, not a
pristine end-user installation. It does not test Finder installation, Gatekeeper
approval, native app menu interaction, or full container-based annotation.
Those checks still require a suitable Intel Mac.

## Signed distribution build

Create/install a **Developer ID Application** certificate and its private key
in Keychain. Store notarization credentials with `xcrun notarytool
store-credentials` under a named Keychain profile. Do not put certificates,
passwords or API private keys in this repository or a chat.

Commit reviewed changes first, then:

```bash
python3 scripts/build_macos_app.py \
  --identity 'Developer ID Application: YOUR NAME (TEAMID)' \
  --notary-profile guide-iei-notary
```

The builder signs each Mach-O component inside-out with hardened runtime,
signs the app, submits it for notarization, staples and validates the app,
then builds/signs/notarizes/staples the DMG. A failed signing/notarization step
stops the build. Developer ID builds use secure timestamps. The build script
does not publish or replace an installed app.

For an isolated release-engine tag, add `--engine-image YOUR_TESTED_IMAGE`;
the builder checks its source fingerprint without retagging the workstation's
installed engine. The companion sources must match that exact image identity.

## Updates and release gates

The standalone edition blocks source-tree install and rollback operations.
An update links to the official Mac release download: quit GUIDE-IEI and replace
the app. Data and settings are not part of that replacement. Keep an older
app if needed, but do not assume downgrading its code is compatible with a
newer patient-library schema.

Before publishing this package, complete [the release checklist](RELEASE_CHECKLIST.md).
The initial supported standalone scope is Apple Silicon; Intel's equivalent
checks are required before expanding that scope:

1. Test native arm64 and x86_64 builds on supported clean macOS installations.
2. Test actual quarantined DMG downloads and online first-launch preparation
   without preinstalled Python/Node/Homebrew/Docker Desktop. Then verify offline
   reopening and annotated-VCF review with the prepared engine. Offline first
   launch on a clean machine must request networking, not bypass setup.
3. Test full annotation environment setup, dataset installation, external-drive
   paths, file sharing, retry and repair.
4. Test existing-user migration, replacing the app, restart after Storage
   changes and orderly quit during active work, using synthetic data.
5. Verify Developer ID/notarization on the final artifacts and ensure the draft
   contains the self-contained installer, not the legacy source-payload ZIP.

After a clean committed distribution build, assemble source/update and Mac
archives with matching source identity and a combined checksum file:

```bash
bash scripts/make_release.sh --macos-artifacts dist/macos-app \
  --engine-sources dist/engine-source-companion-0.6.2
```

The assembler verifies the actual ZIP and mounted DMG (signature, notarization
ticket, build ID, architecture, clean-source flag and commit). `--draft` also
creates a GitHub draft prerelease; it never publishes. A bare version tag runs
CI and creates only a source-archive draft. Attach the verified signed assets
and replace its checksum file with the combined file before publication.
Do not overwrite assets on an already published release; use a new version.
`--source-only` is for the CI/source archive, not an end-user Mac installer.

Mac assembly requires `--engine-sources`: it verifies the companion archive's
SHA-256, image identity and source fingerprint against the packaged app, then
includes the archive and `engine-sources.json` in release assets/checksums.
Publish both with the matching installer. The user's app download remains
small; the approximately 1.38 GB source companion is an optional download.

Apple notarization checks software security; it is not clinical validation.
