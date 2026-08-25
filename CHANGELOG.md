# Changelog

Each entry describes what changed for the people using GUIDE-IEI. The
version here, in the `VERSION` file, and in the release tag always agree.

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
