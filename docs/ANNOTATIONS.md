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
| **auto** | VEP cache, reference FASTA, LOFTEE GRCh38 data, SpliceAI masked MANE SNVs, RepeatMasker, SegDup | `scripts/download_references.sh` (SpliceAI is fetched from Ensembl; RepeatMasker/SegDup are fetched from UCSC and cleaned for VEP automatically) |
| **auto, per-run** | ClinVar | fetched fresh from NCBI on every run by `scripts/fetch_clinvar.sh` |
| **bring-your-own (large)** | dbNSFP | large download — place the file and point the config at it. dbNSFP needs a one-time rebuild (`scripts/prepare_dbnsfp.sh`). |
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

- `--flag_pick_allele_gene` retains every transcript consequence and marks one
  preferred consequence per ALT allele and gene with `PICK=1`. The explicit
  order is MANE Select, MANE Plus Clinical, canonical, APPRIS, TSL, biotype,
  CCDS, consequence rank, then transcript length. `--mane`, `--canonical`,
  `--appris`, `--tsl`, `--ccds`, and `--numbers` expose the selection evidence.
- `--symbol` adds the gene symbol, `--hgvs` adds HGVS notations, followed by
  `--biotype`, `--sift p`, `--polyphen p` (prediction + score),
  `--af_gnomade` / `--af_gnomadg` (gnomAD exome/genome allele frequencies)
  plus `--max_af` (`MAX_AF` / `MAX_AF_POPS`, used as UI popfreqmax).

The review default shows every MANE Select or MANE Plus Clinical consequence.
When an allele-gene has no MANE transcript, its `PICK=1` consequence is used as
the clinical fallback. “All transcripts” remains available without rerunning
VEP.

## Plugins

| Plugin | Config key | File | Notes |
|--------|-----------|------|-------|
| dbNSFP | `plugins.dbNSFP` | `path` + `columns` | One file, dozens of scores (CADD, REVEL, AlphaMissense, SIFT, PolyPhen, PrimateAI, MetaRNN, GERP++, phyloP/phastCons…). Core fields are always included; each annotation job can add curated predictors from the local UI. The diagnostic profile pins **v5.3.1a**. Download from dbnsfp.org, then run `scripts/prepare_dbnsfp.sh`. |
| LoF (LOFTEE) | `plugins.LoF` | `human_ancestor_fa`, `conservation_file`, `gerp_bigwig` | Required by the diagnostic profile. **Must use the LOFTEE `grch38` branch** (baked into the image). `loftee_path: auto` resolves to `$LOFTEE_DIR` (`/opt/vep/src/loftee`) inside the container. |
| SpliceAI | `plugins.SpliceAI` | `snv` (optional `indel`) | Required by default. `scripts/download_references.sh` fetches Ensembl's GRCh38 masked SNV scores for MANE v1.4. A lab may additionally configure a compatible indexed indel VCF. |

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
3. **Download and unzip** academic release **v5.3.1a**. It is based on GENCODE
   49 / Ensembl 115. The ZIP contains per-chromosome variant tables
   (`dbNSFP5.3.1a_variant.chr<#>.gz`), the gene table, and
   the `search_dbNSFP` program.
4. **Build the GRCh38 file for VEP.** The per-chromosome files are sorted on
   the **hg19** columns; for GRCh38 they must be merged and re-sorted on the
   GRCh38 position columns, then bgzipped and tabix-indexed:

```bash
scripts/prepare_dbnsfp.sh /path/to/dbNSFP5.3.1a_unzipped_dir
```

This writes the configured `plugins.dbNSFP.path` (+ `.tbi`). The default
`plugins.dbNSFP.columns` list is the always-on core: CADD, REVEL,
AlphaMissense, SIFT, PolyPhen, MetaRNN, PrimateAI and common conservation
scores. On the VEP settings screen, **Additional dbNSFP predictors** appends
validated fields to that job's generated config. The service checks every
choice against the installed file header. Avoid `ALL`: it adds hundreds of
columns and produces unnecessarily large VCFs.

When an annotated VCF is imported, the review application reads the VEP CSQ
schema and automatically lists only the additional predictors actually present
under **Display settings**. They remain hidden by default and can be enabled
individually. Detected fields are also appended to TSV exports.

The pinned VEP image/cache is currently release 113, whereas dbNSFP 5.3.x was
rebuilt on Ensembl 115. The dbNSFP plugin performs GRCh38 allele lookups, but
transcript-specific values can differ when transcript sets change. The
pipeline reports this compatibility difference rather than silently claiming
the releases are identical. Before upgrading VEP or dbNSFP, run:

```bash
python3 pipeline/check_dbnsfp_version.py \
  --config config/annotation.config.yaml
```

The command reads the configured version and filename, checks the official
dbNSFP releases page, recommends a newer academic release when one exists, and
warns about known Ensembl/VEP release differences. It never downloads or
replaces dbNSFP. Use `--offline` when network access is unavailable.

### SpliceAI
SpliceAI is **required by default** and remains separate from dbNSFP.
`scripts/download_references.sh` downloads the public Ensembl GRCh38 masked SNV
scores for MANE v1.4 plus its tabix index to `references/spliceai/`. Preflight
stops before annotation if either file is absent. The source is Ensembl's
[`variation_plugins` directory](https://ftp.ensembl.org/pub/data_files/homo_sapiens/GRCh38/variation_plugins/);
the VCF is about 28.6 GB and the downloader uses validated, resumable HTTP
ranges.

The bundled reference covers all possible SNVs overlapping MANE Select
transcripts and uses SpliceAI's default 50-base distance. Ensembl does not
generate indel scores. If a lab has a compatible bgzipped, tabix-indexed indel
score VCF, add it as `plugins.SpliceAI.indel`; the command builder will pass
both files to the plugin. This distinction should remain visible when
interpreting an unannotated indel.

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
2. Extracts `CDS` and `stop_codon` features plus windows at the actual exon
   boundaries of protein-coding transcripts. This retains splice sites even
   when a UTR separates an exon boundary from the CDS. Coordinates are
   converted from GTF (1-based inclusive) to BED (0-based half-open), padded by
   `region.padding_bp` (default **8 bp**), and clamped at 0.
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

The review UI also exposes ClinVar conflicts separately. “ClinVar conflict
with ≥1 P / LP” requires both an aggregate conflicting classification in
`CLNSIG` and an explicit `Pathogenic` or `Likely_pathogenic` submission in
`CLNSIGCONF`; a generic conflict label alone is not sufficient.

---

## Frameshift PTC-based LOFTEE 50-bp rule

LOFTEE's original `LoF_info/50_BP_RULE` is evaluated at the genomic variant
coordinate. That is an unsuitable anchor for a frameshift because the
premature termination codon is downstream in the new reading frame. The
enabled-by-default `loftee_ptc_50bp.py` postprocessor:

1. Reads the exact transcript and consequence from each VEP CSQ entry.
2. Builds that transcript from the release-matched local Ensembl GTF and
   indexed GRCh38 FASTA—no REST call and no separate R installation.
3. Validates transcript version, CDS structure, terminal stop, REF allele, and
   exon mapping.
4. Applies the indel to CDS plus 3′ UTR, locates the first in-frame stop, and
   measures from that PTC to the true final exon-exon junction.
5. Uses `distance <= 50` as `FAIL`; otherwise `PASS`.
6. Replaces `50_BP_RULE` inside `CSQ/LoF_info` only when calculation succeeds.

Appended CSQ fields retain the audit trail:

| Field | Meaning |
|-------|---------|
| `LoF_50_BP_RULE_original` | Original variant-position LOFTEE verdict |
| `LoF_50_BP_RULE_PTC` | Primary PTC-position verdict |
| `LoF_50_BP_RULE_changed` | `1` when the effective verdict changed |
| `PTC_cds_pos`, `PTC_aa_pos` | Simulated PTC position |
| `PTC_dist_from_last_exon` | Distance to the true final exon junction |
| `PTC_dist_from_last_coding_exon` | LOFTEE-anchor comparison |
| `LoF_50_BP_RULE_LOFTEE_anchor` | Verdict at the comparison anchor |
| `PTC_calc_status` | Success or explicit refusal reason |

Start-loss frameshifts, transcript-version mismatches, REF mismatches, invalid
models, exon-spanning edits, and cases without a downstream stop are refused
rather than assigned a confident verdict. Their original `LoF_info` remains
unchanged. The postprocessor does not rewrite `LoF=HC/LC`, because that value
also summarizes other LOFTEE filters.

The GTF release must equal the configured VEP release; preflight treats a
mismatch as an error when this required postprocessor is enabled. Each run
writes `<vep.vcf.gz>.loftee_ptc50.audit.json`.

---

## Sample-specific haplotype consequences

The required `haplotype_consequences` stage runs the release-matched
Haplosaurus executable from the pinned VEP container on frameshift-indel
candidates. Haplosaurus proposes transcript and protein haplotypes; the local
postprocessor then independently checks each sample's `GT`, `PS`, and `PID`
before attaching `INFO/IEI_HAPLOTYPE_FRAME`.

The three statuses are intentionally conservative:

| Status | Review behavior |
|--------|-----------------|
| `FRAME_RESTORED_CONFIRMED` | All copies carrying the component variants form a non-frameshift, non-stop-changing protein haplotype; excluded from isolated-LoF review by default |
| `FRAME_RESTORATION_PARTIAL_CONFIRMED` | A restoring haplotype is confirmed, but another allele copy remains disrupted (for example homozygous frameshift plus heterozygous partner); remains visible |
| `FRAME_RESTORING_POSSIBLE_UNPHASED` | Restoration is possible but heterozygous phase/phase-set evidence is insufficient; remains visible with a warning |

Explicitly phased trans variants are not called restoring. Haplosaurus
haplotypes retaining a frameshift or changing a stop are also not treated as
restoration. The original VEP/LOFTEE consequence is never deleted, and a
compact `<vep.vcf.gz>.haplotype.audit.json` records all decisions.

---

## Annotation completeness certificate and regression panel

Each completed annotation writes JSON and HTML certificates beside the final
VCF. Configure warning thresholds under `annotation_qc:` in
`config/annotation.config.yaml`. These are coverage checks, not pathogenicity
rules:

| Check | Eligible denominator |
|-------|----------------------|
| AlphaMissense / CADD | records with a missense consequence |
| LOFTEE | records with stop-gained, frameshift, essential splice, or start-lost consequences |
| PTC 50-bp correction | records with a frameshift consequence |
| SpliceAI | transcript-overlapping SNVs on a MANE transcript |

A field absent from the required VEP schema is a failure. Coverage below its
configured threshold is a warning. No eligible records is
`NOT_APPLICABLE`—not a failure. promoterAI is `SKIPPED_NOT_INSTALLED` when its
licensed source is unavailable.

The complementary regression panel verifies known public examples against the
actual installed VEP image and data:

```bash
bash scripts/run_annotation_regression.sh
```

Expected assertions live in `test/regression/expected.yaml`, and the resulting
machine-readable report is
`test/out/regression/annotation_regression.report.json`. The assertions are
version-aware but avoid brittle exact score values: they check annotation
presence, expected consequence/gene, a meaningful SpliceAI score, ClinVar
pathogenicity, LOFTEE classification, and amino-acid matching. The TERT control
automatically becomes a required promoterAI check when that field is present.
