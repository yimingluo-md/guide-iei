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

## Storage management

The Storage page reports:

- `cohort.sqlite3`;
- managed Sample Library files;
- browser upload staging;
- cohort preparation cache;
- WGS review cache;
- job and resource logs;
- SQLite freelist space that can be reclaimed.

Cleanup is explicit. It removes rebuildable uploads/caches and incomplete
`.partial` files, but never an external original VCF or a managed library VCF.
SQLite compaction is a separate confirmed action; it runs `VACUUM`, can take
several minutes, and does not remove logical records.

Expected managed compact-candidate storage is approximately 3–12 GB for 100
WGS samples or 15–60 GB for 500. External annotated WGS VCFs are not included
in those estimates. Full-WGS cohort indexing can reach hundreds of gigabytes
for 100 genomes and terabyte scale for several hundred.
