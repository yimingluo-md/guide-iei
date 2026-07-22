# Annotation sources

Every annotation source is toggled in `config/annotation.config.yaml` with the
same three keys:

```yaml
SomeSource:
  enabled: true      # include this annotation
  required: false    # if true, a missing file is a hard error; if false, skip with a warning
  path: ...          # (or file:) location of the data file, relative to the repo root
```

If `enabled: true` but the file is missing and `required: false`, the source is
**skipped with a warning** and the run continues. This is what lets one config
work whether or not you have the large/custom datasets on hand.

## Availability tiers

| Tier | Sources | How you get them |
|------|---------|------------------|
| **auto** | VEP cache, reference FASTA, LOFTEE GRCh38 data, RepeatMasker, SegDup | `scripts/download_references.sh` (RepeatMasker/SegDup are fetched from UCSC and cleaned for VEP automatically) |
| **auto, per-run** | ClinVar | fetched fresh from NCBI on every run by `scripts/fetch_clinvar.sh` |
| **bring-your-own (large)** | dbNSFP, SpliceAI | large downloads — you place the files and point the config at them. dbNSFP needs a one-time rebuild (`scripts/prepare_dbnsfp.sh`). |
| **bring-your-own (custom)** | promoterAI, LoGoFunc | license-gated / lab-generated tracks — not scriptable; auto-skipped if absent |
| **auto (region)** | coding+splice BED | built once from the release-matched Ensembl GTF by `scripts/build_coding_bed.sh`; used to pre-filter the input VCF |

**Design note.** Most precomputed pathogenicity/conservation scores that used
to require a separate plugin and download (CADD, REVEL, AlphaMissense, SIFT,
PolyPhen, PrimateAI, MetaRNN, GERP, phyloP/phastCons, …) are now pulled from a
**single dbNSFP file** via one VEP plugin. This collapses the heaviest part of
the old setup into one download + one prep step. dbNSFP does **not** provide
SpliceAI to VEP — SpliceAI stays a separate plugin with its own precomputed
score files.

---

## Core VEP annotations

Set under `core:` — these need only the VEP cache + FASTA:

- `--pick` one consequence per variant, `--symbol` gene symbol, `--hgvs` HGVS
  notations, `--biotype`, `--sift p`, `--polyphen p` (prediction + score),
  `--af_gnomade` / `--af_gnomadg` (gnomAD exome/genome allele frequencies).

## Plugins

| Plugin | Config key | File | Notes |
|--------|-----------|------|-------|
| dbNSFP | `plugins.dbNSFP` | `path` + `columns` | One file, dozens of scores (CADD, REVEL, AlphaMissense, SIFT, PolyPhen, PrimateAI, MetaRNN, GERP++, phyloP/phastCons…). Pick columns in `columns:` (or `ALL`). **v5.1a** matches GENCODE 47 / Ensembl 113 / VEP 113. Download from dbnsfp.org, then `scripts/prepare_dbnsfp.sh`. |
| LoF (LOFTEE) | `plugins.LoF` | `human_ancestor_fa`, `conservation_file`, `gerp_bigwig` | **must use the LOFTEE `grch38` branch** (baked into the image). `loftee_path: auto` resolves to `$LOFTEE_DIR` (`/opt/vep/src/loftee`) inside the container. |
| SpliceAI | `plugins.SpliceAI` | `snv`, `indel` | Precomputed delta scores; **not** in dbNSFP and **not** auto-downloadable (BaseSpace login). Supply your own precomputed VCFs (e.g. scores you recompute yourself). |

## Custom tracks (`--custom`)

| Track | Config key | Format | Fields |
|-------|-----------|--------|--------|
| RepeatMasker | `custom_tracks.RepeatMasker` | bed | overlap flag — **auto** from UCSC hg38 `rmsk`, cleaned (see below) |
| SegmentalDups | `custom_tracks.SegDup` | bed | overlap flag — **auto** from UCSC hg38 `genomicSuperDups`, cleaned |
| promoterAI | `custom_tracks.promoterAI` | vcf (type=exact) | `promoterAI` — **license-gated** (see below) |
| ClinVar | `custom_tracks.ClinVar` | vcf (type=exact, coords=0) | `CLNSIG`, `CLNSIGCONF`, `CLNREVSTAT`, `CLNDN` |
| LoGoFunc | `custom_tracks.LoGoFunc` | vcf (type=exact, coords=0) | `LoGoFunc_GOF`, `LoGoFunc_LOF` — **lab custom track** |

Custom-track fields are joined with `%` in the VEP `--custom` string (VEP's
multi-field separator), reproducing the original `vep_hg38.sh` behaviour.

---

## Preparing the bring-your-own sources

### dbNSFP (one-time, ~50 GB — manual registration)
dbNSFP **cannot be auto-downloaded** — it is behind an academic registration.
`scripts/download_references.sh` therefore does not fetch it; it prints these
steps when dbNSFP is enabled but missing:

1. **Register** with your institutional email at
   <https://www.dbnsfp.org/download> — you receive an academic access code
   after verification (free for academic / non-commercial use under
   CC BY-NC-ND 4.0).
2. **Request the download links** using that email + access code.
3. **Download and unzip** the release. Use **v5.1a** for a VEP 113 / Ensembl
   113 / GRCh38 pipeline (its variant set is built on GENCODE 47 / Ensembl 113;
   v5.3 is Ensembl 115). The ZIP contains per-chromosome variant tables
   (`dbNSFP5.1a_variant.chr<#>.gz`), the gene table (`dbNSFP5.1_gene.gz`), and
   the `search_dbNSFP` program.
4. **Build the GRCh38 file for VEP.** The per-chromosome files are sorted on
   the **hg19** columns; for GRCh38 they must be merged and re-sorted on the
   GRCh38 position columns, then bgzipped and tabix-indexed:

```bash
scripts/prepare_dbnsfp.sh /path/to/dbNSFP5.1a_unzipped_dir
```

This writes `references/dbnsfp/dbNSFP5.1a_grch38.gz` (+ `.tbi`) — the default
`plugins.dbNSFP.path`. Choose which scores land in the VCF with
`plugins.dbNSFP.columns` (a list of dbNSFP column names, or `ALL`). The default
list reproduces the old CADD / REVEL / AlphaMissense / SIFT / PolyPhen stack
plus common conservation and ensemble scores.

### SpliceAI
Precomputed SpliceAI delta scores are **not** in dbNSFP and are **not**
scriptable to download (Illumina BaseSpace login). Supply your own precomputed
`snv`/`indel` VCFs — recomputing scores yourself is recommended for diagnostics
(the 2019 precomputed tables report only the max delta within 50 nt of each
variant). Place them and point `plugins.SpliceAI.snv` / `.indel` at them; the
source is auto-skipped until the files exist.

### promoterAI
promoterAI scores are distributed by Illumina under a **non-commercial license
form** (not a direct download). Request access at Illumina's PromoterAI page;
they email a link to the precomputed scores. Convert to a bgzipped,
tabix-indexed VCF exposing a `promoterAI` INFO field and point
`custom_tracks.promoterAI.file` at it. Auto-skipped until present.

## Region restriction — coding + splice BED (default)

The `region:` block controls the scope of annotation. **By default
(`coding_only: true`) the input VCF is pre-filtered to coding exons + splice
sites before VEP** — the single biggest lever for running on a workstation
instead of a cluster (see the README *Scope* section).

`scripts/build_coding_bed.sh` builds the BED once and caches it at
`references/regions/coding_splice.padded.bed.gz`:

1. Downloads the **release-matched Ensembl GTF**
   (`Homo_sapiens.GRCh38.<release>.gtf.gz`, same release as your VEP cache).
   Because it is Ensembl, contigs are already `1`/`MT` — no `chr` stripping is
   needed (unlike the UCSC tracks above).
2. Extracts `CDS` and `stop_codon` features, converts GTF (1-based inclusive)
   to BED (0-based half-open), pads each by `region.padding_bp` (default **8 bp**),
   clamps the low end at 0.
3. Sorts and merges overlapping/adjacent intervals (pure awk — no bedtools),
   then bgzips + tabix-indexes.

The run script then applies it with `bcftools view -R <bed>`. **Padding
rationale:** 8 bp captures the essential/consensus splice sites — it matches
the splice-consensus window dbNSFP annotates (−3 to +8) and the Sequence
Ontology `splice_region_variant` definition (3 exonic / 8 intronic); the
essential GT-AG dinucleotides (±1–2) are a subset. Change it with
`region.padding_bp` (rebuild with `--force`).

**Overrides.** Set `region.custom_bed` to a gene-panel / capture BED to use it
verbatim. Set `region.coding_only: false` (or pass `--all-variants`) to annotate
every variant (WGS / non-coding; HPC-scale).

---

## RepeatMasker + SegDup — automatic, with contig cleaning

`scripts/download_references.sh` fetches these from the UCSC Genome Browser
(`goldenPath/hg38/database/rmsk.txt.gz` and `genomicSuperDups.txt.gz`) and
converts each to a sorted, bgzipped, tabix-indexed BED. **The cleaning matters:**
UCSC names contigs `chr1` / `chrM`, but the Ensembl VEP cache uses `1` / `MT`.
The script strips the `chr` prefix, renames `chrM`→`MT`, and keeps primary
contigs only (1–22, X, Y, MT) — without this, `--custom` overlap queries
silently never match. RepeatMasker's name column is `repClass|repFamily|repName`;
SegDup's is the duplication `fracMatch`.

---

## ClinVar (automatic, per run)

`clinvar.auto_fetch: true` (default) makes every run download the current
`clinvar.vcf.gz` for the configured assembly from
`https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/`. The release date is
read from the `##fileDate=` header and stamped into the filename
(`clinvar_<YYYYMMDD>.GRCh38.vcf.gz`) and into the amino-acid-match INFO
description, so every annotated VCF records exactly which ClinVar it used.

## ClinVar amino-acid-match post-processing

Reproduces the awk step from `vep_hg38.sh`. A residue-level catalog of
pathogenic / likely-pathogenic **missense** ClinVar variants
(`SYMBOL` + `Protein_position`) is built by `build_clinvar_aa_reference.sh`
(filter ClinVar → VEP-annotate the subset → reduce to 2 columns). Each variant
in your annotated VCF then gets `INFO/ClinVar_path_aa_match = 1` if any of its
missense transcript consequences sits on a residue in that catalog, else `0`.
Because both sides read `Protein_position` from the same VEP field, the match
is an exact string comparison — identical semantics to the original tab-based
step, but applied to VCF (so sample genotype / zygosity is preserved).
