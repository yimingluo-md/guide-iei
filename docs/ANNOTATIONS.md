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
| **local updateable snapshot** | ClinGen Evidence Repository variant curations | installed or updated from the annotation-dataset UI; prepared by `scripts/update_clingen_erepo.sh` |
| **large local** | dbNSFP; CADD v1.7 whole genome (WGS only) | dbNSFP requires academic registration and one-time rebuilding (`scripts/prepare_dbnsfp.sh`). CADD's required score-only files are downloadable/resumable from the UI or `scripts/download_cadd_wgs.sh`. |
| **bring-your-own (licensed)** | PromoterAI | obtain the two files from Illumina, then prepare them from the local UI or `scripts/prepare_promoterai.sh`; auto-skipped if absent |
| **optional public** | LoGoFunc | resumable direct download from Zenodo in the UI or `scripts/download_logofunc.sh`; an existing download can be validated and moved into managed storage with `scripts/prepare_logofunc.sh`; auto-skipped if absent |
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
| CADD v1.7 whole genome | `plugins.CADD_WGS` | `snv`, `indels` | Optional, WGS-only standard CADD plugin. The UI downloads only the official score tables and indexes and emits `CADD_RAW`/`CADD_PHRED`; CADD is licensed for non-commercial use. |
| PromoterAI | `plugins.PromoterAI` | `file`, `transcript_map`, `manifest` | Optional, WGS-only transcript/TSS-aware plugin. The plugin code is bundled; licensed scores are prepared locally and never shipped. |
| LoGoFunc | `plugins.LoGoFunc` | `file`, `manifest` | Optional GRCh38 missense-mechanism prediction (Neutral/GOF/LOF probabilities) from [Zenodo 13835271](https://zenodo.org/records/13835271). The plugin requires allele, source transcript, residue, and amino-acid substitution agreement. |

## Custom tracks (`--custom`)

| Track | Config key | Format | Fields |
|-------|-----------|--------|--------|
| RepeatMasker | `custom_tracks.RepeatMasker` | bed | overlap flag — **auto** from UCSC hg38 `rmsk`, cleaned (see below) |
| SegmentalDups | `custom_tracks.SegDup` | bed | overlap flag — **auto** from UCSC hg38 `genomicSuperDups`, cleaned |
| ClinVar | `custom_tracks.ClinVar` | vcf (type=exact, coords=0) | `CLNSIG`, `CLNSIGCONF`, `CLNREVSTAT`, `CLNDN` |

Custom-track fields are joined with `%` in the VEP `--custom` string (VEP's
multi-field separator), reproducing the original `vep_hg38.sh` behaviour.

### LoGoFunc matching and interpretation

LoGoFunc's public table contains one canonical Ensembl transcript consequence
for each GRCh38 missense SNV and was generated with VEP 106. This pipeline is
pinned to VEP 113 and retains all clinical transcript consequences. A genomic
allele match alone is therefore insufficient: the plugin emits probabilities
only when the source transcript stable ID, amino-acid position, reference amino
acid, and alternate amino acid also agree. Allele-only matches are recorded as
such so transcript-version drift is visible rather than silently misassigned.

The UI can propagate a verified source-transcript prediction alongside the
preferred MANE row for the same allele and gene, while preserving the source
transcript and match status. Cohort LoGoFunc filters use only verified matches.
These are research mechanism predictions, not ClinVar classifications and not
LOFTEE calls. See [Bayrak et al., Genome Medicine (2023)](https://pubmed.ncbi.nlm.nih.gov/38037155/).

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

### PromoterAI

PromoterAI scores are distributed directly by Illumina after the user accepts
the applicable license at <https://github.com/Illumina/PromoterAI>. This
software does not download, bundle, upload, or redistribute the score files.
The supplied folder must contain exactly these two inputs:

- `tss.tsv`
- `promoterAI_tss500.tsv.gz`

On the local **Annotation datasets** screen, select **Choose folder**, pick the
download folder in the native file chooser, and select **Prepare and install**.
No path typing is required. Prepared files are written to the Annotation
datasets location configured on the Storage page; the licensed original files
are removed from the selected folder only after every managed output is
successfully created. The equivalent terminal command (which retains source
files unless the UI-only removal flag is supplied) is:

```bash
bash scripts/prepare_promoterai.sh /absolute/path/to/PromoterAI
```

The one-time preparation performs basic schema, coordinate, strand, SNV, and
numeric-score checks. It collapses transcripts that share the same genomic TSS
into an indexed table with `score_A`, `score_C`, `score_G`, and `score_T`
columns, while retaining a separate transcript/TSS map. It writes:

- `references/promoterai/promoterai_scores.tsv.gz` plus `.tbi`
- `references/promoterai/promoterai_transcripts.tsv`
- `references/promoterai/promoterai.manifest.json`

The manifest records source filenames, sizes, SHA-256 checksums, row counts,
the GRCh38 coordinate convention, and checksums for every derived file. It does
not record a license acknowledgement, agreement date, or license version.
Small TSS groups present in `tss.tsv` but absent from Illumina's score table are
left unannotated and listed under `missing_tss`; preparation fails if omissions
exceed 0.1% instead of silently accepting a materially incomplete source.

The bundled `PromoterAI.pm` VEP plugin annotates an SNV only when its GRCh38
allele and the VEP transcript's TSS both match. Output includes the signed
`PromoterAI_score`, TSS, strand-aware distance from the TSS, source transcript,
and transcript match mode. The review UI automatically detects the score,
shows it among the default predictors, and offers the optional filter
`|PromoterAI score| >= 0.8` when the annotation is present.

PromoterAI is deliberately unavailable under **Exome region only**, because
that profile removes promoter variants before VEP. Use a whole-genome job to
enable it. A missing local PromoterAI dataset is an explicit optional skip.

### CADD v1.7 whole genome

The WGS annotation profile offers separate, optional full-genome CADD v1.7
scores. Select **Download / resume** in the dataset setup screen or run
`scripts/download_cadd_wgs.sh`. The downloader retrieves only
`whole_genome_SNVs.tsv.gz`, `gnomad.genomes.r4.0.indel.tsv.gz`, their tabix
indexes, and official MD5 files (about 83 GiB total). It deliberately skips the
625 GB/11 GB `inclAnno` files, the 335 GB release bundle, and dbscSNV because
the standard VEP CADD plugin reports only `CADD_RAW` and `CADD_PHRED`.

The files are queried directly with
`--plugin CADD,snv=<file>,indels=<file>`; no converted or merged VCF is made.
This is distinct from the coding-region CADD columns supplied through dbNSFP.
When both exist, the selected WGS plugin value is authoritative and dbNSFP is
the fallback. The official indel table covers gnomAD genomes r4.0 indels rather
than every possible novel indel; absent values remain missing and pass the
conservative WGS prefilter. CADD is available for non-commercial use.

### Whole-genome review prefilter

The Import page prepares annotated WGS VCFs as BGZF/tabix input and filters
chromosome shards with four concurrent readers. The work is a background job;
the page displays its current phase, percentage, scanned variants, retained
variants, and active reader count. PASS/QC and the population-frequency rule
(`gnomAD popmax <= 0.01` or a per-variant unavailable value by default) are
evaluated before the candidate routes.
The derived browser-review VCF keeps every retained site while reducing
redundant CSQ entries to MANE, then PICK, then one fallback per allele/gene.
Browser intake streams BGZF lines with bounded decompression memory.
The candidate routes are exonic/essential-splice, qualifying SpliceAI,
qualifying absolute promoterAI, and one mutually exclusive noncoding-region
choice: SCREEN Registry V4 cCRE overlap (default), all noncoding regions, or no
additional noncoding regions. CADD, other predictors, and gene lists are not
import-time filters. A missing score does not satisfy its score route; a
qualifying variant can still enter through another route. There are two narrow
indel safety routes because the configured precomputed reference tracks may be
SNV-only: an intronic sequence-resolved indel without a SpliceAI value is
retained, and a sequence-resolved indel overlapping the installed promoterAI
TSS +/- 500 map without a promoterAI value is retained. Both remain subject to
PASS/QC and population frequency. They are labeled per ALT with
`IEI_UNSCORED_INDEL=SpliceAI_intronic`, `PromoterAI_promoter`, or both joined by
`&`; `.` denotes an ALT for which the flag is not applicable. The exception is
not applied when a score is present but below threshold, and it is enabled only
when the corresponding predictor field is declared in that VCF's INFO/CSQ
schema. A completely absent annotation dataset is therefore not mistaken for
an unscored indel.

SCREEN cCREs are a native indexed BED resource, not a VEP plugin. The pinned
public Registry V4 GRCh38 BED and index ship with the native software bundle;
the dataset screen offers a repair download only if the bundled copy is
missing. The command-line repair remains
`scripts/download_references.sh config/annotation.config.yaml --only ccre`.
The preparation retains cCRE accessions and overall classes for future display,
normalizes primary contigs, and creates a BGZF/tabix BED. It also builds a
compact gene-level TSS table from the release-matched Ensembl 113 GTF.

Variant detail lookup is local and distinguishes overlap, confirmed non-overlap,
and an unavailable resource. Each overlapping cCRE reports its accession,
class, and every Ensembl gene whose gene-level TSS lies within 500 kb of the
cCRE interval, ordered by absolute strand-aware distance. Protein-coding genes
are displayed by default; an **Include non-protein-coding genes** option reveals
the complete result while retaining total and biotype counts. This table is
deliberately not called a target-gene table: proximity does not establish
regulation, and the nearest or VEP-annotated gene may not be the cCRE's target.

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

For a single-exon transcript, there is no downstream exon-exon junction and
the conventional 50–55-nt exon-junction NMD rule is not applicable. The
postprocessor may still reconstruct and record the PTC position, but emits
`PTC_calc_status=not_applicable_single_exon_transcript`, leaves the recalculated
rule empty, and does not replace the original LOFTEE value. Premature stops in
single-exon transcripts may escape exon-junction-complex-dependent NMD, but
transcript-specific RNA and protein evidence is required rather than assuming
that every such transcript escapes all forms of RNA surveillance.

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

Transcripts whose biotype is not exactly `protein_coding` are also refused
before CDS simulation, matching LOFTEE's applicability rule. In particular,
`protein_coding_LoF` describes a transcript whose ORF is disrupted on the
reference-genome haplotype but may be translated on other human haplotypes.
Such consequences remain reviewable and visibly flagged, but are placed after
consequences modeled against an intact reference ORF and are not treated as
conventional LOFTEE-supported pLoF evidence.

The review interface therefore keeps the two assessments separate:

- **LOFTEE** shows `LoF` together with the transcript-specific `LoF_filter`
  reasons and `LoF_flags`. Bundled LOFTEE codes are expanded into readable
  explanations while the original codes remain visible for auditability.
- **Frameshift PTC calculation** shows whether transcript reconstruction
  completed, the direction-aware PTC distance from the final exon junction,
  and the recalculated `LoF_50_BP_RULE_PTC` verdict. This verdict is not
  presented as the reason for the original LOFTEE `HC`/`LC` classification.
  For an `EXON=1/1` consequence, it instead displays an explicit single-exon
  caveat and labels any legacy stored PASS/FAIL value as not interpreted.

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
| LoGoFunc allele availability | missense SNVs; exact-match coverage then uses allele-available records |

A field absent from the required VEP schema is a failure. Coverage below its
configured threshold is a warning. No eligible records is
`NOT_APPLICABLE`—not a failure. Optional LoGoFunc and promoterAI checks are
reported as `SKIPPED_NOT_INSTALLED` when their local sources are unavailable.

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
pathogenicity, LOFTEE classification, and amino-acid matching. Three OR4F5
controls verify LoGoFunc class, source transcript, and strict match status when
the public table is installed. The TERT control similarly becomes a required
promoterAI check when that field is present.

## ClinGen Evidence Repository variant curations

ClinGen expert-panel classifications are applied after VEP as exact
CHROM/POS/REF/ALT matches. They are intentionally INFO-level allele evidence,
not transcript CSQ annotations. `INFO/ClinGen_ERepo` contains URL-encoded
records with ALT, assertion UUID, ClinGen Allele Registry ID, classification,
disease, MONDO ID, mode of inheritance, expert panel, and approval date.

The full local SQLite snapshot retains interpretation summaries, applied and
not-met evidence codes, PubMed IDs, guideline links, publication dates, and
mapping provenance. The review UI renders one card per disease/MOI assertion;
it never substitutes the most pathogenic assertion for the complete set. If no
row matches, the display is **No ClinGen variant classification found.**

The snapshot is required by the diagnostic profile and can be safely refreshed
from the dataset setup screen. Updating is never performed during patient
annotation, and no patient variant is sent to an external service.
