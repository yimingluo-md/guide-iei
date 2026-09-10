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
- Small public reference tables and public gene knowledge, documentation and
  third-party notices. No developer reference folder, private exports, patient
  libraries, credentials or results are copied into the application.
- The app opens the existing browser interface; it is not a native variant-table
  rewrite. Closing the browser does not stop the app; use Quit GUIDE-IEI.

Each package has a unique build ID in `desktop-build.json`. A second launch
reopens an identified instance of the same package; different/older builds
receive an explanatory dialog. An unrecognized occupied port never causes
process termination. Lifecycle status exposes job counts, not patient names.
The instance ID on a Quit request prevents accidentally stopping a replacement
instance; it is not authentication (see the single-user security model).

Review of an annotated VCF needs neither a container nor a dataset download.
On macOS, opening the workbench also makes one background attempt to start an
already installed, selected local Docker Desktop engine or existing Colima
profile. The interface shows **Starting Docker…** and review remains usable.
This does not install an engine, create a new VM, change Docker contexts, or
start remote engines. Startup is bounded (up to two minutes for the startup
command, followed by up to two minutes for engine readiness); failure leaves
manual instructions and a private `logs/docker-startup.log` in the workbench
state directory. Restart the workbench to retry automatically. Developers can
disable startup with `IEI_AUTO_START_DOCKER=0`. Quitting GUIDE-IEI does not stop
the Docker daemon or Colima VM, which may be used by other applications.

For annotation, open **Import & QC → Set up annotation datasets**. If the
engine is missing or outdated, **Set up annotation engine** installs missing
Mac user-space container tools and prepares the VEP image after confirmation.
Progress, logs, and retry are on the same page; quit and conflicting data
changes are blocked until setup finishes. It does not install Node or change
an existing conda/Homebrew environment. Choose data locations in Storage and
then install datasets through the workbench. Large databases and registration-restricted exports remain
separate from the app. Container file-sharing checks still apply, including
access to the app under Applications and to selected data directories.

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

A preview has only an ad-hoc signature unless an explicit Developer ID identity
is supplied. It must not be published as the supported end-user installer.

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
2. Test actual quarantined DMG downloads, offline first launch and annotated-VCF
   review without Python/Node/Homebrew/Docker installed.
3. Test full annotation environment setup, dataset installation, external-drive
   paths, file sharing, retry and repair.
4. Test existing-user migration, replacing the app, restart after Storage
   changes and orderly quit during active work, using synthetic data.
5. Verify Developer ID/notarization on the final artifacts and ensure the draft
   contains the self-contained installer, not the legacy source-payload ZIP.

After a clean committed distribution build, assemble source/update and Mac
archives with matching source identity and a combined checksum file:

```bash
bash scripts/make_release.sh --macos-artifacts dist/macos-app
```

The assembler verifies the actual ZIP and mounted DMG (signature, notarization
ticket, build ID, architecture, clean-source flag and commit). `--draft` also
creates a GitHub draft prerelease; it never publishes. A bare version tag runs
CI and creates only a source-archive draft. Attach the verified signed assets
and replace its checksum file with the combined file before publication.
Do not overwrite assets on an already published release; use a new version.
`--source-only` is for the CI/source archive, not an end-user Mac installer.

Apple notarization checks software security; it is not clinical validation.
