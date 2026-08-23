# Changelog

Each entry describes what changed for the people using GUIDE-IEI. The
version here, in the `VERSION` file, and in the release tag always agree.

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
