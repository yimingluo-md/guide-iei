---
title: "Sample Library & storage"
parent: Reference
nav_order: 13
---

# Sample Library, cohort profiles, and workstation storage

## Ownership model

The persistent Sample Library is the source of truth. Cohort Search is a
derived SQLite carrier index that can be removed and rebuilt without deleting a
library dataset.

The identity hierarchy is:

```text
Individual (phenotype and demographics)
└── Sample/specimen
    └── Genomic dataset/import version (WES, WGS, rerun, etc.)
        └── Genotype observations
```

VCF sample names are retained as assay identifiers, but are not used as the
stable individual primary key. Legacy phenotype links by VCF sample name remain
readable.

The library groups every multi-sample VCF as one **callset** instead of showing
one top-level card per sample. Expanding the callset shows its samples. The
managed VCF is stored once and shared by those sample records.

## Duplicate imports and updated versions

Before saving a VCF, GUIDE-IEI compares both its complete SHA-256 and an
annotation-insensitive callset fingerprint made from alleles, filters, FORMAT,
and genotypes:

- importing the same bytes again reuses the existing library records and
  managed file, even when the currently selected import settings differ;
- the same callset with changed annotations becomes a new current annotation
  version automatically;
- a file with overlapping sample names but changed variants or genotypes asks
  whether it should replace the current version or remain a separate dataset;
- a file with no matching samples is stored as a new callset.

Replacing a callset does not delete its history. The prior managed review VCF
is shown under **Previous versions**, can still be opened, and can be restored
as current. Only the current version is eligible for Cohort Search, so rerunning
annotation cannot count the same samples twice. A deliberately separate dataset
may use the same VCF sample name; its library UUID keeps it distinct.

## Import choices

- **Keep in Sample Library** is the default. Persistence happens before the
  browser review opens: GUIDE-IEI creates a managed VCF and index under
  `~/.iei-variant-review/sample-library/files/`, then builds Cohort Search when
  selected. An exome VCF keeps its complete transcript annotations in that
  managed copy. If those annotations would exceed the browser row limit, the
  list uses MANE, then VEP PICK, then one fallback per allele/gene; opening a
  variant restores its complete transcript table from the indexed VCF.
- **Include qualifying variants in Cohort Search** is enabled when the sample
  is retained. A healthy entry is shown only as **Included in Cohort Search**.
  Samples not currently indexed show **Add to Cohort Search**, while a missing
  derived entry shows **Repair Cohort Search**.
- **Review once** opens the VCF without creating library or cohort records. A
  file whose transcript expansion exceeds the browser limit must instead be
  retained or reduced before it can be opened safely.
  This is not a no-trace mode: uploads, preparation caches, logs, and annotation
  outputs can remain on disk.

Routine review does not require index maintenance. Rebuild, removal from
Cohort Search, and the advanced full-WGS index are under **More actions**; none
of these actions reruns VEP or changes the managed review VCF.

After a retained import, every VCF sample must either be linked to an existing
individual, used to create a new individual ID, or explicitly left as phenotype
unavailable. Mapping may be finished later.

## Dataset provenance

Each library dataset records:

- stable library sample and dataset UUIDs;
- VCF sample name and optional individual link;
- original name, path, size, mtime, and SHA-256 when the path is accessible;
- content-addressed managed review VCF and index;
- WES/exome or WGS scope and Compact or Full cohort-index scope;
- QC settings, WGS prefilter settings, and retention routes;
- workstation annotation bundle and resource versions at import time;
- source and retained record counts when available;
- complete canonical settings JSON, a 16-character SHA-256 settings hash, and
  a readable profile label.

The installed workstation resource versions are not asserted to be the source
of an externally annotated VCF; that caveat is stored with the bundle record.

## Cohort comparability

Carrier results show profile labels and can be filtered by WES/WGS assay or
exact profile hash. Positive carrier findings remain useful across profiles.
Absence from a candidate index is not a negative genotype result, because
capture, callability, QC, and retention routes may differ.

The software intentionally does **not** calculate cohort allele frequencies.

## Compact versus Full WGS

Compact WGS is the default. It retains the gentle diagnostic/research candidate
union documented in the main README and typically produces tens of thousands
of records per genome.

Full WGS is an advanced action in the Sample Library. It indexes every PASS/unfiltered
carrier call from the accessible original VCF. A confirmation warning is shown
because one genome can require approximately 10 GB in the initial SQLite
representation. The operation is intended for users who specifically need
exhaustive exact-carrier lookup and have sufficient storage.

## Configurable workstation locations

The **Storage** page separates three kinds of local storage:

| Location | Contains | Typical placement |
| --- | --- | --- |
| **Annotation datasets** | VEP cache, FASTA, dbNSFP, CADD, SpliceAI, ClinVar, ClinGen, SCREEN, PromoterAI, LoGoFunc, FuncVEP, and private GenIA index | Fast internal or continuously attached external SSD |
| **Sample Library & Cohort** | Managed review VCFs, SQLite cohort/phenotype records, import provenance, and logs | Internal or continuously attached external SSD |
| **Temporary workspace** | Browser uploads, prepared VCFs, cohort staging, and rebuildable WGS-review caches | Fastest available disk; follows Sample Library by default |

Each card reports its configured path, active connection/write status, workbench
space used, available space, and relevant cloud/network warnings. It provides
**Change location**, **Test location**, and **Open folder** actions. On WSL,
paths mounted below `/mnt/<drive>/` also show their Windows spelling where it
can be determined.

The small bootstrap registry at `~/.iei-variant-review-bootstrap.json` and its
atomic `.backup` remain on the local user account. They contain roots and
migration history, not VCF contents, cohort records, or phenotypes. Because
folder names are retained, use generic storage-root names rather than patient
or study identifiers. The bootstrap allows the application to find a selected
external drive after restarting. If a configured Sample Library or temporary
drive is unavailable, startup fails with a reconnect message instead of
silently creating a fresh database at the default path.

Managed data and temporary roots carry a unique identity marker. Startup checks
both the storage kind and identity, so a blank or different drive mounted at the
same path is reported as unavailable rather than initialized as a new library.
Read-only annotation roots are supported; a marker is optional when the root
was supplied by an institution and cannot be modified.

### Annotation-root behavior

The selected annotation root is the single ordinary-user location for the
bundle. Stock configuration values beginning `references/` are resolved from
that root whenever the UI creates a VEP or resource-download configuration.
Thus changing the root moves the VEP cache and bundled tracks as one coherent
set. A deliberately absolute per-dataset value in the advanced configuration
(for example, a pre-existing dbNSFP or CADD file) remains an override and is
not moved implicitly.

### Change versus move

**Use for future data** records a new location and takes effect after a
restart. For Sample Library storage, it is intentionally limited to a new or
empty dedicated folder, or a previously managed folder carrying a valid
identity marker. This permits a deliberate rollback to a preserved source
without attaching an unrelated SQLite database.

**Copy existing data** is the safe migration path. It requires a new destination
folder, refuses to run while annotation, resource, cohort, or WGS-prefilter
work is active, checks free space with a safety margin, writes and deletes a
small probe file, then:

1. copies ordinary files and verifies each copy by SHA-256;
2. copies SQLite databases with SQLite's backup API and runs an integrity
   check on the snapshot;
3. rewrites managed Sample Library paths under the selected data root to be
   relative, so a changed external-drive mount path remains portable;
4. writes the new root only after the verified copy is complete, using an
   atomic directory rename.

While a copy is running, and after a location switch until restart, mutating
operations are disabled. Read-only library and migration-status views remain
available. Failed or interrupted copies are recorded and their uniquely named
staging directory is removed automatically; startup also recovers staging from
an interrupted prior process.

The old source is always retained and shown in the migration history after
restart. It is never deleted automatically; remove it manually only after
opening the copied library and confirming that the expected samples, cohort
search, and phenotypes are present. Original external VCFs remain external
provenance paths and are not copied or deleted by this migration.

Large UI-started downloads check that their configured destination has
sufficient free space before beginning (the central annotation root unless an
advanced absolute per-dataset override is in use). Completed ranges and `.part`
files count toward the requirement, so a nearly completed download can resume
without needing space for a second complete payload. ClinVar downloads use the
same resumable range downloader.

### External-disk guidance

Use an SSD where possible. APFS is preferred on macOS, NTFS on Windows, and
ext4 on Linux/within WSL. Avoid cloud-synchronised folders and network shares
for the active SQLite cohort database. exFAT is acceptable for a read-only
transfer disk but is not recommended for the active library because locking
and crash-safety semantics are weaker. Read-only annotation files can live on
slower storage, but tabix-heavy VEP runs can become materially slower.

## Bulk import and collection scale

The Sample Library's **Bulk import** panel queues a folder or list of
annotated VCFs for serial intake: each file runs the same candidate
prefilter as a single import (exome default gnomAD popmax ≤ 0.01; genomes
use the Compact WGS prefilter), is stored as a managed dataset, and — by
default — is added to Cohort Search as it completes. The queue is
persisted (`bulk-intake.sqlite3` in the service state directory), so an
interrupted batch resumes automatically when the service restarts;
per-file failures are recorded with their errors and never stop the run.
One bulk job runs at a time, up to 5,000 files per job.

The design target is a 1,000-genome collection on one workstation. At that
size the library page paginates (25 callsets per page; search and bulk actions
apply to current samples), library additions and
removals run as a single batched request with a per-item outcome report,
and every Cohort Search query form — variant, gene, gene list, region —
answers from covering indexes rather than table scans (the test suite
asserts the query plans). Reviewing, by contrast, stays a per-case or
per-subset activity: large collections are meant to be queried through
Cohort Search, not opened wholesale in the browser.

## Storage management

### Backup and restore

There is not yet a one-click portable backup/restore package. **Export TSV
is not a backup.** For a recoverable workstation copy:

1. Record the application version and the three actual roots shown in
   **Storage**, including any absolute per-dataset overrides. Do not assume
   the defaults if an external location was selected.
2. Finish or stop active jobs and bulk intake, then stop both the workbench
   UI and local service. Closing the browser tab alone does not stop the
   service. Copying a live SQLite database with an ordinary file copy can
   produce an inconsistent backup.
3. Back up the **entire Sample Library & Cohort root**, including hidden
   identity markers, managed VCFs/indexes, databases, phenotype/individual
   records, private OMIM data, and provenance. Keep any SQLite `-wal`/`-shm`
   files with their database if present; do not delete them manually.
4. Back up the bootstrap registry
   `~/.iei-variant-review-bootstrap.json` and its `.backup` sibling. Also
   preserve your configuration and the annotation root's private/user-supplied
   resources (including GenIA), manifests, and any local overrides. A copy of
   large public references avoids downloading again but is not a substitute
   for the library backup. Follow each source's redistribution restrictions.
5. Keep external original VCFs, complete annotation outputs, and their audit
   files separately: a Storage migration does not copy these provenance paths.
   Browser-local saved candidates, custom gene lists, and display preferences
   are separate from the server folder; record/export what you need or include
   the browser profile in an institution-approved backup. A library copy alone
   will not restore them.

Restore with the same GUIDE-IEI version first, while the service is stopped.
Keep the original backup untouched. Restore the complete folder structure and
markers to the same paths, or use **Storage → Change location → Use for future
data** to select an existing managed copy when the old location is still
accessible. If a missing external drive prevents startup, reconnect it first;
do not delete the bootstrap registry to silence the error. For a new machine
with different mount paths, have an administrator map the backed-up roots
before startup; there is no automatic cross-machine path-repair wizard.

Verify sample/callset counts, a reopened managed VCF, individual/phenotype
links, and a known Cohort Search result. Check private dataset availability.
Cohort Search can be rebuilt from retained library data, but a compact copy
cannot recreate variants it never retained. Only after this check should you
upgrade the restored installation or retire an old storage copy. Encrypt and
restrict access to backups containing patient or licensed data.

The Storage page reports:

- `cohort.sqlite3`;
- managed Sample Library files;
- browser upload staging;
- cohort preparation cache;
- WGS review cache;
- job and resource logs;
- SQLite freelist space that can be reclaimed.

Cleanup is explicit. It removes rebuildable uploads/caches, generated job and
resource configuration snapshots, and incomplete `.partial` files, but never
an external original VCF or a managed library VCF.
SQLite compaction is a separate confirmed action; it runs `VACUUM`, can take
several minutes, and does not remove logical records.

Expected managed compact-candidate storage is approximately 3–12 GB for 100
WGS samples or 15–60 GB for 500. External annotated WGS VCFs are not included
in those estimates. Full-WGS cohort indexing can reach hundreds of gigabytes
for 100 genomes and terabyte scale for several hundred.
