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

## Import choices

- **Keep in Sample Library** is the default. It creates a managed compact VCF
  and index under `~/.iei-variant-review/sample-library/files/`.
- **Include qualifying variants in Cohort Search** is enabled when the sample
  is retained. A healthy entry is shown only as **Included in Cohort Search**.
  Samples not currently indexed show **Add to Cohort Search**, while a missing
  derived entry shows **Repair Cohort Search**.
- **Review once** opens the VCF without creating library or cohort records.

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

Full WGS is an advanced action in the Sample Library. It indexes every PASS
carrier call from the accessible original VCF. A confirmation warning is shown
because one genome can require approximately 10 GB in the initial SQLite
representation. The operation is intended for users who specifically need
exhaustive exact-carrier lookup and have sufficient storage.

## Configurable workstation locations

The **Storage** page separates three kinds of local storage:

| Location | Contains | Typical placement |
| --- | --- | --- |
| **Annotation datasets** | VEP cache, FASTA, dbNSFP, CADD, SpliceAI, ClinVar, SCREEN, PromoterAI, and LoGoFunc | Fast internal or continuously attached external SSD |
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

## Storage management

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
