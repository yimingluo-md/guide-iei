# Changelog

Each entry describes what changed for the people using GUIDE-IEI. The
version here, in the `VERSION` file, and in the release tag always agree.

## 0.6.2 — 2026-09-11

Apple Silicon beta release candidate. Publication remains gated on the
[release checklist](docs/RELEASE_CHECKLIST.md); a build is not a public release.

- The self-contained macOS app bundles Python, a prebuilt interface and the
  architecture-matched VEP container image. Users do not need to install Python,
  Node, Homebrew or Docker Desktop themselves. First launch still needs internet
  for container tools and a Linux VM; it prepares the engine before opening the
  workbench, even for annotated-VCF review. Large databases remain separate.
- Distribution builds require a clean source commit, Developer ID signing and
  Apple notarization. The release assembler verifies the signed app and DMG,
  source identity and checksums. Release automation creates drafts only; it no
  longer publishes the legacy unsigned source-payload Mac installer.
- A native control window and browser Quit button make the service lifecycle
  explicit. Closing the browser leaves work running; confirmed Quit stops
  GUIDE-IEI without stopping Docker. Busy imports, storage moves and engine
  preparation must finish first. Old source-launcher Dock shortcuts should be
  replaced with the installed self-contained app.
- The native startup window automatically checks/prepares container tools and
  VEP before opening the browser, with named stages, logs, Retry and Quit.
  Compatible installations are reused; there is no skip option and old remembered
  deferrals are ignored. Import & QC retains engine repair controls and all
  large-dataset selection/downloads.
- A ready annotation engine is shown only in the existing Ensembl VEP status
  row, without a redundant setup card. Active preparation and unavailable-engine
  repair remain visible; previous setup logs are collapsed under diagnostics.
- Quitting from the native window now notifies open browser tabs without
  discarding their loaded reviews, filters or stars. Temporary connection failures
  show a reconnecting notice rather than claiming the app quit; tabs reconnect
  automatically when the service returns, without refreshing the page.
- Existing managed Colima installations recover from an unused default Docker
  connection without changing the global Docker context. Setup and later jobs
  use the same recovered connection. Missing working-folder sharing is repaired
  with a configuration backup; busy containers are not interrupted.
- Fixed first launch from `/Applications` on Docker Desktop without sharing that
  directory. Containers use user-owned working/data folders, not the signed app
  tree; setup verifies real read/write access and annotation still checks all
  required input/reference/output locations. LOFTEE and the VEP engine are unchanged.
- The macOS application, browser tab and workbench header now share the
  DNA/immune-receptor icon.
- The bundled engine omits unused legacy Kent/jkOwnLib and Carol/Math::CDF
  material without changing the BigWig reader. A matching third-party source
  companion (separate from the installer) covers OS, Perl, Python and native
  dependencies. Release assembly requires the matching companion and verifies
  its checksum; original notices and modification notices are retained.
- Cohort rsID searches resolve exact identifiers through indexes before joining
  annotations, avoiding a full annotation-index scan on affected SQLite versions.
- Fixed immediate reopening after webpage Quit: recently closed TCP connections
  no longer produce a false “port already in use” error. Genuine running services
  and unrelated applications remain protected; nothing is killed to free a port.
- Shutdown records interruption before signalling annotation children and stops
  new child launches once shutdown begins, preventing a timing-dependent false
  failure status or a child starting after the shutdown snapshot.
- Post-processing now indexes uniquely named temporary VCFs before replacing
  final files. This avoids indexing a stale container view of a replaced file;
  tabix truncation warnings are failures even when tabix returns zero. The real
  release regression checks every indexed record against the full output.
- Initial standalone distribution targets Apple Silicon on macOS 13 or newer.
  Intel packaging CI is a smoke test, not full Intel annotation certification.
  Windows/WSL and Linux source workflows remain available with their documented
  prerequisites; they are not standalone installers in this beta.
- Research-use-only scope is unchanged. Notarization is not clinical validation.

## 0.6.1 — 2026-08-25

The Mac release now starts reliably on a factory-fresh Intel or Apple-silicon
Mac running macOS 13 or newer after the documented one-time manual approval
in Privacy & Security.

**Mac installation and launch**

- The downloadable app is now a self-contained standalone release rather than
  a shortcut that depends on finding the source repository beside it. Its
  checksum-verified application payload is installed in the user's Application
  Support folder, so macOS App Translocation cannot break its paths.
- A tiny native universal launcher contains both arm64 and x86_64 code. Release
  builds receive a free ad-hoc signature and bundle seal; no paid Apple
  Developer membership or signing secret is required to build or publish them.
- A clean Mac receives pinned, checksum-verified native Python and Node.js
  runtimes in the user's own tools folder. System Python, Xcode Command Line
  Tools, Git, Homebrew, Docker Desktop, and administrator rights are not
  prerequisites.
- The interface runs as an optimized production build. Software updates mark
  it for a one-time rebuild whenever web source files change.
- First-launch errors now appear in a visible macOS alert instead of causing a
  silent exit.

**Release engineering**

- Tagged releases are built on macOS and publish both the source/update archive
  and the standalone Mac application. CI verifies the universal executable,
  ad-hoc bundle seal, payload install, architecture metadata, and translocated
  launch behavior.

**Windows installation and launch**

- The double-click launcher now opens with immediate visible status, remains
  open after errors, and records timestamped diagnostic logs under the user's
  local application-data folder.
- It requires a genuine WSL2 distribution with Bash, ignores application-owned
  internal distributions, and offers or explains the supported Ubuntu install
  when WSL is absent.
- First launch copies only portable source state into WSL, safely handles
  Windows folder names containing shell metacharacters, and installs missing
  Ubuntu Python/core prerequisites. Windows `node_modules` and Next.js build
  output can no longer contaminate the Linux setup.
- Docker Desktop availability is checked inside the selected distribution.
  The graphical launcher never installs a second native Docker engine behind
  the user's back; annotated-VCF review remains available without Docker.
- Windows CI parses the launcher and tests its clean-machine failure guidance.

**Faster, safer initial dataset setup**

- dbNSFP no longer asks nontechnical users to install `aria2c`, download three
  files, or choose a source folder. After academic registration, users paste
  the main GRCh38 link (including an Outlook Safe Link) into the dataset card;
  GUIDE-IEI privately derives the `.tbi` and `.md5` companions, downloads with
  eight resumable connections, verifies the MD5 and tabix index, and installs
  the published file without rebuilding or making a second 52 GB copy.
- The dbNSFP link field accepts future official release filenames ending in
  `_grch38.gz`, rejects the similarly named `_grch37.gz` file, and records the
  installed filename/version for subsequent jobs. Slow academic-server range
  probes now allow five minutes per attempt and retry instead of failing at the
  former 60-second limit with a Python traceback.
- One-click setup now validates the indexing tools before large transfers. If
  a clean machine lacks native bioinformatics tools, GUIDE-IEI builds its
  configured local annotation-tool image first, so a missing backend cannot
  surface after hours of downloading.
- Independent mirror-backed and canonical-source reference groups download in
  two bounded parallel lanes. SpliceAI's source, contents, verification, and
  per-file connection count are unchanged.
- Docker fallback is explicitly local-only and never attempts to pull the
  project image from a registry. Deterministic container startup failures are
  no longer retried after a delay.
- VEP cache installation no longer decompresses the verified multi-gigabyte
  archive once to list it and again to extract it. ClinVar release detection
  reads only its header after checksum verification, and publishing the
  `latest` copy uses a hard link when the filesystem supports one.
- Clean-clone CI now exercises tool-image preflight, the no-pull guarantee, and
  concurrent reference scheduling.
- Resuming on macOS now handles the common one-lane case where the large core
  downloads are complete and only small indexed tracks remain. A failed
  one-click setup stays visible with its error, retained-data explanation,
  log, and an explicit Retry action.
- Clean source checkouts now restore any absent release reference payloads
  (the frameshift gene model, SCREEN cCRE map, gene-TSS table, and GRCh37
  liftover files) through the same recommended setup. The required frameshift
  dataset card also exposes a working repair action instead of an unactionable
  **Needs repair** state.
- Dataset progress now shows the live dataset and operation (for example,
  cCRE download, gene-TSS construction, or hg19 liftover preparation) in the
  visible progress row instead of a generic **Preparing recommended datasets**
  label. The same live detail is exposed to screen readers.

**Known limitation**

- Until the project adopts Apple Developer ID signing and notarization, macOS
  requires **System Settings → Privacy & Security → Open Anyway** once for each
  newly downloaded release. Organization-managed Macs may disable that option.

## 0.6.0 — 2026-08-23

The first release installable through the built-in updater.

**New**

- **Software updates from inside the workbench.** The new About page
  shows the installed version, checks GitHub for a newer release when you
  ask it to (one request, nothing sent), and installs it in place. Your
  datasets, your sample library, and your edited configuration are never
  touched; when a release introduces new configuration options, the new
  file is placed beside yours for review instead of overwriting it. The
  previous version is kept for one-click rollback.
- **One-click launchers** for Mac (GUIDE-IEI.app) and Windows
  (GUIDE-IEI.bat via WSL2), with first-launch environment preparation and
  no administrator rights required on the Mac.
- **Bulk intake queue** for importing many annotated VCFs into the
  cohort in one sitting.
- **Sex-chromosome-aware trio analysis**: X, Y, and mitochondrial
  inheritance models, with hemizygous calls excluded from compound-het
  pairing.
- **PS1- and PM5-style ClinVar protein matching as two separate,
  transcript-aware flags**, each per ALT allele, with the reasoning
  documented in the manual.

**Reliability and integrity**

- A long series of hardening fixes across import identity (sample names
  are now bound to the exact bytes a checksum vouches for), cohort and
  WGS content fingerprints (full hashing for exome-scale files),
  download verification (upstream MD5 and remote-change detection),
  liftover cache validation, projection-cache validation, and the
  review interface's QC honesty.
- The Windows launcher now always targets the Linux distribution it
  validated, never the WSL default.

**Notes**

- Cached GRCh37 liftover conversions from earlier versions re-run once
  after this update (the conversion pipeline changed).
- The ClinVar amino-acid catalog rebuilds once on the next annotation
  run (its format gained reference-residue and transcript columns).
