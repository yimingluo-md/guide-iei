# Self-contained macOS app builds

The self-contained edition is currently a **release candidate packaging path**,
separate from the existing source-payload release workflow. Describe an artifact
as Developer ID signed or notarized only after verifying that actual artifact;
the preview label alone does not establish its signing status.

## Contents and operation

- Native Apple Silicon or Intel application launcher with Open Workbench,
  Prepare Annotation Environment, Open Log and Quit menu items.
- Pinned relocatable Python and PyYAML; no user-installed Python or Node needed.
- Prebuilt static workbench served by the local Python service on loopback.
- Small public reference tables and public gene knowledge, documentation and
  third-party notices. No developer reference folder, private exports, patient
  libraries, credentials or results are copied into the application.
- The app opens the existing browser interface; it is not a native variant-table
  rewrite. Closing the browser does not stop the app; use Quit GUIDE-IEI.

Review of an annotated VCF needs neither a container nor a dataset download.
For annotation, choose **Prepare Annotation Environment** in the Mac app menu;
this explicitly downloads/prepares the container environment. Progress is in
Open Log. Choose data locations in Storage and then install datasets through
the workbench. Large databases and registration-restricted exports remain
separate from the app. Container file-sharing checks still apply, including
access to the app under Applications and to selected data directories.

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
smokes above. It runs on pushes to `codex/intel-app-smoke` and supports manual
dispatch once available on the default branch. Its 14-day artifacts are ad-hoc
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

Before switching the public release workflow to this package:

1. Test native arm64 and x86_64 builds on supported clean macOS installations.
2. Test actual quarantined DMG downloads, offline first launch and annotated-VCF
   review without Python/Node/Homebrew/Docker installed.
3. Test full annotation environment setup, dataset installation, external-drive
   paths, file sharing, retry and repair.
4. Test existing-user migration, replacing the app, restart after Storage
   changes and orderly quit during active work, using synthetic data.
5. Verify Developer ID/notarization on the final artifacts, then deliberately
   replace the legacy release workflow. Until then it remains unchanged.

Apple notarization checks software security; it is not clinical validation.
