# WES/WGS diagnostic analysis pipeline

Config-driven, containerized **local** variant annotation with Ensembl VEP +
LOFTEE, for single- or multi-sample VCFs. Clone, build the image once, download the
references you want, and annotate — output is an **annotated VCF** (all
annotations in `INFO/CSQ`), so sample genotype / zygosity is preserved.

## Scope and intended use

This project is a WES/WGS diagnostic-analysis pipeline, **currently developed
primarily for inborn errors of immunity (IEI)**. It is intended to support:

- **Clinical diagnostics** — as a research/interpretation aid. Any variant used
  in patient care **must be independently confirmed and validated in a
  CLIA-certified laboratory**. This pipeline is not a clinical device and its
  output is not a diagnostic result on its own.
- **Variant-of-uncertain-significance (VUS) prioritization.**
- **Novel monogenic etiology discovery.**

> **Research use only.** This software is provided for research purposes. It is
> not FDA-cleared, not CLIA-validated, and must not be used as the sole basis
> for any clinical or treatment decision.

## Project status — this is step one

**The VEP annotation described here is only the first step.** A complete
diagnostic workflow has substantial downstream stages planned — variant
filtering and tiering, inheritance/segregation and phenotype-driven
prioritization, curation against gene–disease and IEI-specific knowledge, and a
review/reporting web UI. Those are under active development and will be added on
top of the annotated-VCF output this stage produces.

This stage reproduces the annotation stack of an HPC `vep_hg38.sh` workflow as a
portable, config-toggled local setup.

## What you get

- A **modernized Dockerfile** — layers the LOFTEE `grch38` branch onto the
  official `ensemblorg/ensembl-vep:release_113.4` image (which already ships
  VEP, the full plugin stack including dbNSFP, and bgzip/tabix).
- **dbNSFP-consolidated scores** — one file + one plugin replaces the separate
  CADD / REVEL / AlphaMissense / SIFT / PolyPhen downloads.
- A **plain-language workstation UI** that reports annotation-dataset
  availability and lets users choose per-run sources and settings. YAML remains
  an internal/CLI configuration format for administrators and reproducibility.
- A **run script** that builds the exact VEP command from the config and runs
  it in the container (docker / podman / singularity).
- **Reference download helpers** for the freely-scriptable data, plus
  **automatic latest-ClinVar fetch** on every run.
- **ClinVar amino-acid-match** post-processing, adapted from the original awk
  step to work on VCF (`INFO/ClinVar_path_aa_match`).
- **ClinGen Evidence Repository variant curations** as an updateable local
  snapshot, preserving separate disease/MOI expert-panel assertions and full
  interpretation provenance without sending patient variants to an API.
- **PTC-based LOFTEE 50-bp correction** for frameshifts, calculated locally
  from the release-matched GTF and indexed FASTA.
- **Sample-specific Haplosaurus post-processing** so nearby indels that restore
  a reading frame are reviewed as a haplotype rather than two isolated LoFs.

## Layout

```
config/annotation.config.yaml   administrator defaults used by CLI and the UI
docker/Dockerfile               VEP 113 + LOFTEE grch38 + samtools + DBD::SQLite
docker/build.sh                 build the image (docker or podman)
scripts/setup_environment.sh    host-environment doctor + no-admin bootstrap (Node, container stack)
scripts/download_references.sh  fetch VEP cache / FASTA / LOFTEE / RepeatMasker / SegDup
scripts/install_recommended_datasets.sh  one-click exome/WGS public dataset setup
scripts/update_refreshable_datasets.sh  refresh ClinVar + ClinGen variant curations
scripts/build_native_reference_bundle.sh  package shipped SCREEN + hg19 resources
scripts/build_coding_bed.sh     build coding+splice BED (Ensembl GTF) for region restriction
scripts/prepare_dbnsfp.sh       rebuild a downloaded dbNSFP release for GRCh38 (one-time)
scripts/fetch_clinvar.sh        download + version-stamp the latest ClinVar
scripts/run_annotation.sh       main entry point: config -> VEP -> annotated VCF
scripts/liftover_grch37_to_grch38.sh  controlled legacy-VCF intake into GRCh38
scripts/build_clinvar_aa_reference.sh   build the aa-match catalog from ClinVar
scripts/update_workbench_references.sh  rebuild bundled gnomAD/IUIS UI resources
scripts/update_gene_knowledge.sh  rebuild public HGNC/IUIS/ClinGen gene knowledge
scripts/update_clingen_erepo.sh   safely install/update ClinGen expert variant assertions
scripts/sync_to_onedrive.sh     copy the working tree (no .git) to a cloud-synced folder
pipeline/build_vep_command.py   translate the config into VEP argv + bind-mounts
pipeline/loftee_ptc_50bp.py     replace frameshift 50_BP_RULE using the resulting PTC
pipeline/haplotype_consequences.py validate sample GT/phase for frame-restoring haplotypes
pipeline/clinvar_aa_match.py    add INFO/ClinVar_path_aa_match to the VCF
pipeline/clingen_erepo_annotate.py  add exact allele-level ClinGen assertion IDs
pipeline/reduce_vep_to_aa_reference.py  VEP-tab -> aa-match catalog
local_service/                  loopback API + persistent SQLite job queue
webui/                          local IEI variant-review workbench
docs/ANNOTATIONS.md             per-source reference: what each is, how to get it
docs/GRCH37_INPUT.md             assembly detection, liftover QC, provenance, limitations
docs/TRIO_ANALYSIS.md            pedigree input, de novo tiers, compound-het phase
docs/SAMPLE_LIBRARY_AND_STORAGE.md  persistent identity, cohort profiles, disk management
docs/BUNDLED_WORKBENCH_REFERENCES.md  gnomAD constraint + IUIS provenance
docs/GENE_KNOWLEDGE.md                HGNC/IUIS/ClinGen + private OMIM handling
test/                           tiny VCF + config + tests (no container needed)
```

## Requirements

Run the environment doctor after cloning — it checks everything below, prints
an exact fix for anything missing, and `--install` fixes the user-space items
itself (no admin rights, no Homebrew, nothing outside one managed folder):

```bash
# report what is present / missing (changes nothing)
bash scripts/setup_environment.sh

# fix what can be fixed without admin rights
bash scripts/setup_environment.sh --install
```

What it needs to find (or install):

- **A container runtime** — Docker, Podman, Singularity, or Apptainer.
  - *macOS:* `--install` sets up a no-admin, Homebrew-free stack (Lima +
    Colima + the Docker CLI, all version-pinned and SHA-256-verified) in
    `~/.iei-variant-review/tools/`. An existing Docker Desktop / Podman
    install is detected and used instead.
  - *Linux / WSL2:* a container runtime is a system component, so the script
    prints the exact install commands and runs them only after an explicit
    yes (existing docker/podman/singularity installs are always preferred —
    Docker Desktop is **not** required on WSL2).
- **Python 3.8+** with **PyYAML** (`requirements.txt`; `--install` handles it)
  — for the config parser.
- **Node.js 22.13+** with npm — for the local review UI. `--install` places
  the official nodejs.org build in the managed tools folder when no suitable
  Node is found; `scripts/start_workbench.sh` finds it there automatically.
- Disk for references: the VEP cache alone is ~25 GB; dbNSFP is a ~50 GB
  download (academic registration required — see below) and needs ~200 GB
  scratch for its one-time GRCh38 rebuild; SpliceAI (if enabled) adds tens of
  GB more.

No VEP, LOFTEE, bgtools, or Perl installation on the host — everything runs in
the container. The setup script never edits your shell profile; remove
`~/.iei-variant-review/tools/` to uninstall everything it added.

## Supported platforms

| Platform | Status | Notes |
|----------|--------|-------|
| **Linux** | ✅ native | Primary target. Run directly. |
| **macOS** (Intel or Apple Silicon) | ✅ native | Scripts are Bash-3.2-compatible (macOS ships Bash 3.2); run the Linux container with Docker Desktop, Podman, or Colima. |
| **Windows** | ✅ via **WSL2** only | Not supported from native Windows shells or Git Bash. Use WSL2 (see below). |

The runner is a set of **Bash** scripts that call standard Unix tools
(`awk`, `sed`, `sort`, `gzip`, `curl`/`wget`, `python3`, `rsync`) plus a Linux
**container** (`ensemblorg/ensembl-vep`). Anything that needs `bcftools` /
`tabix` / `bgzip` runs them natively if present, or falls back to the container.

### Windows: use WSL2

Native Windows (`cmd`, PowerShell, or bare Git Bash) **cannot** run this — the
scripts need a real Unix shell + coreutils, and the VEP image is Linux-only.
The supported route is **WSL2** (Windows Subsystem for Linux 2), which is a real
Linux kernel:

1. Install WSL2 with a Linux distro (e.g. Ubuntu): `wsl --install` in an
   elevated PowerShell, then reboot.
2. Install **Docker Desktop** and enable its **WSL2 backend** (Settings →
   Resources → WSL integration).
3. Open the WSL2 (Ubuntu) shell and clone + run the pipeline there exactly as a
   Linux user would.

> **Performance caveat.** Keep the clone **and** the large reference files on
> the **WSL2 filesystem** (`~/...` inside the distro), *not* on a Windows drive
> under `/mnt/c/...`. Cross-filesystem I/O to `/mnt/c` is very slow, which badly
> hurts the multi-GB tabix reference reads (dbNSFP, SpliceAI, CADD).

## Where to keep the clone (cloud-sync note)

Keep the git clone in a **non-synced** location (e.g. `~/repos/`) and do all
`git pull` / `git push` there. **Do not run git directly inside a OneDrive /
Dropbox / Google Drive folder** — those providers block or corrupt the `.git`
directory (OneDrive returns "Operation not permitted" on `.git`, and syncing
git's internal object files mid-write can corrupt the repo).

If you want a copy inside a synced folder to edit/run from, use the helper,
which copies the working tree **without** `.git`:

```bash
# refresh the OneDrive snapshot after a git pull (edit DEST or set ONEDRIVE_DEST)
scripts/sync_to_onedrive.sh [DEST]
```

## Quickstart

New machine? Three commands get you to a running workbench:

```bash
git clone <this repository> && cd WES-WGS_diagnostic_analysis_pipeline
bash scripts/setup_environment.sh --install   # doctor + user-space setup (see Requirements)
bash scripts/start_workbench.sh               # launch the review workbench
```

Annotation datasets can then be installed from **Run VEP first → Set up
annotation datasets** in the UI, or from the command line as below.

```bash
# 1. build the container image (once; setup_environment.sh --install offers this too)
bash docker/build.sh

# 2. download the freely-scriptable references (VEP cache, FASTA, LOFTEE,
#    required SpliceAI MANE SNVs, RepeatMasker, SegDup, hg19->hg38 chain)
bash scripts/download_references.sh config/annotation.config.yaml

# 2b. dbNSFP (~52 GB) is NOT auto-downloaded: register at dbnsfp.org/download
#     for an academic access code, then download the single GRCh38 BGZF file
#     (dbNSFP5.4a_grch38.gz + .tbi + .md5) from your instruction email and:
bash scripts/prepare_dbnsfp.sh /path/to/download_folder
#     (verify + install, no rebuild; legacy per-chromosome ZIPs still work)

# Advisory only: report whether a newer academic dbNSFP release exists.
python3 pipeline/check_dbnsfp_version.py --config config/annotation.config.yaml

# 2c. PromoterAI is licensed and is not downloaded or shipped. After obtaining
#     tss.tsv + promoterAI_tss500.tsv.gz from Illumina, prepare them locally:
bash scripts/prepare_promoterai.sh /path/to/PromoterAI
#     The same preparation is available on the Annotation datasets screen.

# 2d. Optional LoGoFunc missense-mechanism predictions can be downloaded from
#     Zenodo in the Annotation datasets screen, or from the command line:
bash scripts/download_logofunc.sh config/annotation.config.yaml
#     If the 3.66 GB source is already present, validate/link it without copying:
bash scripts/prepare_logofunc.sh /path/to/LoGoFunc

# 3. annotate a single- or multi-sample VCF
bash scripts/run_annotation.sh \
    -i /path/to/sample.vcf.gz \
    -o results/sample.vep.vcf.gz \
    -c config/annotation.config.yaml

# Legacy GRCh37/hg19 VCF (conversion occurs before GRCh38 region filtering):
bash scripts/run_annotation.sh \
    -i /path/to/legacy.grch37.vcf.gz \
    -o results/legacy.vep.vcf.gz \
    -c config/annotation.config.yaml \
    --input-assembly GRCh37
```

To use the integrated workstation application instead, run:

```bash
bash scripts/start_workbench.sh
```

This starts the loopback-only annotation queue at `127.0.0.1:43117` and the
review UI at `127.0.0.1:3000`. The application lands on **Import VCF**. Choose
**Run VEP first** (the default) or **Review annotated VCF**. The VEP route
accepts dropped `.vcf` / `.vcf.gz` files or a selected folder, then opens a
second screen for parameters and dataset readiness. See `webui/README.md` for
the workflow and WSL2 instructions.

The persistent **Sample Library** is the source of truth for retained workbench
imports. **Keep in Sample Library** is enabled by default after review intake;
**Include qualifying variants in Cohort Search** is also enabled by default.
Choose **Review once** when no managed copy or cohort entry should remain.
The library uses the hierarchy Individual → sample/specimen → genomic
dataset/import version → genotype observations, so one person may have WES,
WGS, rerun, or multiple-specimen datasets without duplicating phenotype data.
After import, each VCF sample can be linked to an existing individual, used to
create a new individual, or left as phenotype unavailable.

Managed review VCFs and indexes are content-addressed under
`~/.iei-variant-review/sample-library/files/`, so identical file content is not
stored twice. Each dataset records the original name/path/checksum when the
path remains available; WES/WGS and Compact/Full scope; QC and WGS prefilter
settings; retention routes; installed
annotation/resource versions; source/retained record counts; timestamps; and a
hash of the complete import settings. The **Sample Library** page reopens a
review, edits its sample label, connects phenotype records, rebuilds its
cohort entry, or removes the managed dataset without deleting an external
original VCF.

The workbench also provides a local **genotype-first cohort search** derived
from the library. Add directories of annotated single- or multi-sample VCFs once, then
query every carrier of an exact variant/rsID or carriers of qualifying variants
in a gene. The indexed variant, annotation, and non-reference genotype records
stay in `~/.iei-variant-review/cohort.sqlite3`. Directly indexed external VCFs
remain in place; library-derived cohort entries can be rebuilt from their
managed review VCF.
Results are grouped as one row per unique allele. Clicking a variant opens its
carrier list plus stored annotation, transcript, genotype, source-profile, and
coordinate details. When matched findings are sent into the main Review workspace,
the service uses tabix to retrieve each exact record from the indexed prepared
VCF and restores its complete populated INFO, VEP CSQ, and sample FORMAT
evidence on demand. This keeps the SQLite index compact while retaining access
to population frequencies and any other annotations present in the source.
Carrier checkboxes can instead load selected individuals' complete stored
review sets, using each file's original Full or Compact WGS import profile.
Complete-set browser loads are limited to 50 sample entries and 200,000 stored
carrier observations; very large full-WGS selections remain searchable but
must be reviewed as matched findings or re-indexed with the Compact WGS profile.
If an indexed source has moved or been deleted, matched-finding Review reports
the source problem and uses the compact SQLite fields for that carrier instead.
The sample manager removes exact sample/file entries, cascades their genotype
rows, and reclaims variants with no remaining carriers. Reimporting the source
VCF restores a removed sample.

Cohort intake validates an existing `.tbi`/`.csi` for coordinate-sorted BGZF
VCFs. A missing index, ordinary gzip stream, uncompressed VCF, or unsorted VCF
is automatically converted to a sorted BGZF working copy under
`~/.iei-variant-review/cohort-vcf-cache/` and indexed there; the source file is
never modified. Prepared copies are fingerprinted by source path, size, and
modification time and reused on later forced imports. Indexed files are read by
four chromosome-sharded worker processes by default. Each worker writes
batched natural-key records to a disposable staging database, after which one
transaction merges the stages into the cohort database. Set
`IEI_COHORT_INDEX_READERS=1` for serial troubleshooting or another positive
integer to tune the reader count. When native `bcftools`/`tabix` are absent,
the workbench uses the configured Docker/Podman image; if neither backend is
available, or a discovered runtime cannot complete preparation, it reports a
warning and retains the serial staged-import fallback.

**Compact WGS is the default workstation profile.** It runs the same
four-reader candidate filter before SQLite staging. The default requires
`gnomAD popmax <= 0.01` or an unavailable per-variant value, then retains the
union of coding/essential-splice, qualifying SpliceAI, qualifying promoterAI,
and ENCODE SCREEN cCRE-overlap routes. **Full WGS** is an advanced option that
indexes every PASS carrier call from the original VCF. It supports exhaustive
exact searches within those indexed calls but can use roughly 10 GB of SQLite
space for one genome, so it is not recommended for routine workstation use.
Users may replace
the cCRE route with all noncoding regions or no additional noncoding regions.
Missing score values do not satisfy a score route. The database records the
Full/Compact profile and Exome/Whole-genome source scope separately, displays
mixed provenance, and warns that an absent noncoding result is not exhaustive
when any compact source is present. The scope label also determines whether
regulatory review is available when a stored individual is reopened. Legacy
Compact entries migrate to Whole genome; older Full entries are marked
`scope not recorded` until refreshed. Import progress includes prefilter
scanned and retained counts as well as staged PASS and carrier counts.

Every cohort file has a human-readable profile label plus the complete settings
hash. Carrier results show the contributing WES/WGS and Compact/Full profiles,
and searches can be restricted by assay or exact profile. A positive carrier
finding remains useful across heterogeneous profiles; absence from a candidate
index is **not** interpreted as evidence that an individual lacks the variant.
The software deliberately does not calculate cohort allele frequencies because
callability, capture, and candidate-retention profiles may not be comparable.

The **Storage** page reports database, managed-library, upload, cohort-cache,
WGS-review-cache, and log footprints. It can remove only rebuildable temporary
or cache files and can explicitly compact SQLite to return unused pages to the
filesystem; compaction never deletes samples or variants. As a planning guide,
compact candidate WGS storage is typically about 30–120 MB per sample (roughly
3–12 GB for 100 WGS or 15–60 GB for 500), excluding external original
annotated VCFs. Full-WGS cohort indexing can instead reach hundreds of GB for
100 samples and terabyte scale for several hundred.

Storage can be configured from that page without editing YAML: **Annotation
datasets**, **Sample Library & Cohort**, and an optional **Temporary workspace**
can use separate local SSD locations. A safe **Copy existing data** migration
checks free space, verifies copied files, preserves the original location, and
requires a restart before the new root becomes active. The application never
silently creates an empty default cohort database when a configured external
library drive is disconnected. See
[`docs/SAMPLE_LIBRARY_AND_STORAGE.md`](docs/SAMPLE_LIBRARY_AND_STORAGE.md#configurable-workstation-locations).

The workbench includes compact, versioned gnomAD v4.1.1 constraint, HGNC,
IUIS October 2024, and ClinGen gene-disease-validity/dosage resources. The
variant screen provides source-specific filters and a dedicated **Gene** tab;
these resources are joined during review and do not need to be added to the
VCF. Licensed OMIM files are never shipped or downloaded automatically, but
can be indexed from a user-selected local folder under **Gene knowledge**. See
`docs/BUNDLED_WORKBENCH_REFERENCES.md` and `docs/GENE_KNOWLEDGE.md`.

VEP annotation resources can be checked and set up from **Run VEP first → Set
up annotation datasets**. One-click actions install the recommended automatic
downloads for either exome or whole-genome analysis and refresh changing public
sources such as ClinVar. dbNSFP registration and licensed PromoterAI setup are
shown separately with direct source links and guided instructions. SCREEN
Registry V4 cCRE regions and the hg19 input bundle ship with the native release;
LoGoFunc remains an optional research annotation. See
[`docs/REFERENCE_SETUP.md`](docs/REFERENCE_SETUP.md).

The local workbench also stores individual demographics and plain-text
phenotypes separately from sequencing samples. Records may be entered manually
or imported from mapped CSV, TSV, or XLSX columns, with reusable mapping
profiles and sample-link validation. Reported race and reported ethnicity are
kept separate; neither is treated as genetic ancestry. HPO mapping and
phenotype-based prioritization are not performed in this release. Variant Review
has a dedicated **Phenotype** tab that first uses the stable Sample Library
dataset-to-individual link and retains VCF-sample lookup for legacy or directly
indexed data; a missing link is shown explicitly and can be added from the phenotype
manager. See
`docs/PHENOTYPE_INPUT.md`.

The workbench also supports session-local **trio analysis**. Upload a standard
PED file or manually assign proband, mother, and father to identify
confidence-tiered de novo candidates and compound-heterozygous pairs classified
by parental origin or available phase. A jointly genotyped multi-sample VCF is
preferred; absence from a separate parental VCF is never interpreted as a
confident `0/0` call. See `docs/TRIO_ANALYSIS.md`.

The run will:
1. retain only explicit `FILTER=PASS` records and **restrict the input to coding
   exons + splice sites** (defaults; builds the
   BED once from the release-matched Ensembl GTF — see *Scope* below),
2. retain all transcript consequences, flag the preferred consequence per ALT
   allele and gene, annotate MANE transcript status for the review UI, and
   download the latest ClinVar (version-stamped),
3. (re)build the ClinVar amino-acid-match catalog if ClinVar changed,
4. build the VEP command from your config and run it in the container,
5. write `results/sample.vep.vcf.gz`,
6. recompute the frameshift PTC 50-bp rule and run sample-specific Haplosaurus
   consequence post-processing, then
7. write `results/sample.vep.aamatch.vcf.gz` (+ tabix index) with the
   `ClinVar_path_aa_match` flag added.

Useful flags: `--dry-run` (print the assembled container command and stop),
`--no-clinvar` (skip the per-run ClinVar download), `--all-variants` (annotate
**every** variant, not just coding+splice — for WGS / non-coding work; see
*Scope* below), `--include-filtered` (retain non-PASS calls for deliberate
review/debugging), and `--input-assembly GRCh38|GRCh37|auto`. GRCh38 is the
canonical annotation/cohort assembly; GRCh37 is converted with the
assembly-gap-aware BCFtools/liftover plugin before any GRCh38 region filter,
while `auto` refuses ambiguous
headers. See `docs/GRCH37_INPUT.md`. The PASS default can also be changed with
`run.pass_only`.

## Scope: coding/exome (recommended for a workstation) vs whole-genome

**By default this pipeline restricts annotation to coding exons + splice
sites**, and this is the recommended mode when running on a PC/workstation.
The restriction is a `bcftools view -R <bed>` pre-filter on the input VCF
*before* VEP (`region.coding_only: true`), so VEP only ever processes
on-target variants. The BED is built once from the CDS features of the
**release-matched Ensembl GTF** (same Ensembl release as your VEP cache, so
coordinates line up exactly), with each exon padded by `region.padding_bp`
(default **8 bp**). That 8 bp window captures the essential/consensus splice
sites — it matches both the splice-consensus window dbNSFP itself annotates
(−3 to +8) and the Sequence Ontology `splice_region_variant` definition
(3 exonic / 8 intronic); the essential GT-AG dinucleotides (±1–2) are a subset.

**Why this matters — and is exome analysis really the practical ceiling for a
PC?** For clinical/Mendelian interpretation, yes, in practice. The cost driver
is variant count, not genome size:

- A whole-genome VCF is typically **~4–5 million** variants; a whole-exome VCF
  is **tens of thousands** after the coding+splice restriction removes ~98–99%.
- Runtime scales with the count. Calibrated on a 64-CPU HPC node with this exact
  plugin stack, VEP annotates **~3.7 million variants/hour** (≈0.27 h per
  million): a 2.43M-variant set took ~32 min, a 47.3M-variant joint call set
  took ~12.6 h. A workstation with 8–18 cores runs several-fold slower per core
  and, more importantly, is memory- and I/O-bound with the large tabix plugins
  (dbNSFP, SpliceAI) mmapped.
- So the coding+splice subset (typically **1–2%** of a WGS callset) is what
  turns a multi-hour, large-RAM HPC job into **minutes** on a laptop/workstation.

**Whole-genome / non-coding annotation remains substantially more expensive.**
It is supported from the Import page or with `--all-variants`. WGS intake
validates an existing BGZF/tabix or CSI pair and otherwise creates a sorted,
indexed working copy without changing the submitted VCF. VEP then uses the
configured parallel workers. Expect long annotation walltimes on a workstation;
full-genome score generation still belongs on suitable HPC/GPU infrastructure.

Annotated WGS review uses a separate workstation-safe intake path. Four
chromosome readers conservatively prefilter the indexed VCF before the reduced
result is opened in the browser. The Import page polls this background task and
shows live preparation, chromosome-filtering, merge, compression, and indexing
progress with scanned/retained record counts. Every PASS variant overlapping
the configured coding+splice BED defines the exonic/essential-splice route.
Defaults are gnomAD popmax `<= 0.01` (or unavailable), SpliceAI `>= 0.5`,
absolute promoterAI score `>= 0.8`, and SCREEN Registry V4 cCRE overlap. PASS/QC
and population frequency are global requirements; exonic/essential-splice,
SpliceAI, promoterAI, and the selected noncoding region mode are OR routes. The
noncoding region control offers cCRE overlap (default), all noncoding regions,
or no additional noncoding regions. The last option still retains qualifying
SpliceAI and promoterAI variants. CADD, other predictors, and gene lists are not
used at import time. The indexed input and filtered result
are fingerprinted and reused when the source and settings are unchanged. The
review copy preserves every retained site but compacts redundant transcript
annotations to MANE, then VEP PICK, then one fallback per allele/gene. The
browser reads BGZF incrementally instead of materializing the complete
decompressed WGS VCF as one large string.

Precomputed SpliceAI MANE and promoterAI score tables may contain SNVs but no
matching indel. To avoid silently discarding these unscored alleles, the compact
WGS import also retains a sequence-resolved intronic indel with no SpliceAI
score and a sequence-resolved promoter indel with no promoterAI score. Promoter
overlap uses the installed Illumina TSS +/- 500 transcript map. These safety
routes still require PASS/QC and the population-frequency rule, work even when
the additional noncoding-region mode is `none`, and add the allele-specific
`IEI_UNSCORED_INDEL` INFO flag for review. A populated score below its threshold
does not use this exception. The corresponding predictor field must be declared
in the VCF schema, so a wholly absent annotation dataset does not activate the
missing-indel route.

For whole-genome imports, the variant detail screen also queries the installed
SCREEN Registry directly for every opened GRCh38 allele. It reports cCRE
overlap or an explicit verified non-overlap. A genomic region can simultaneously
have a transcript-specific coding consequence and be a regulatory element. The
regulatory element may act on the same gene, another gene, or multiple genes;
this positional overlap is not independent pathogenicity evidence. Each
overlapping cCRE includes its EH38E accession, overall class,
and the complete release-matched Ensembl gene-TSS context within +/-500 kb with
strand-aware distance. The review table shows protein-coding genes by default
and offers an **Include non-protein-coding genes** control without discarding
them from the underlying result. This is proximity context only: the nearest or
VEP-annotated gene is not necessarily regulated by the cCRE, and the software
does not present these genes as predicted targets.
The separately prepared categorical tissue aggregates and ontology-selected
immune/hematopoietic biosamples are documented in
[`docs/SCREEN_TISSUE_IMMUNE_DATA.md`](docs/SCREEN_TISSUE_IMMUNE_DATA.md). This
layer intentionally omits assay Z scores and preserves evidence-completeness
metadata so unavailable assays are never interpreted as negative results. Its
reproducible AlphaGenome-style curation uses exact Cell Ontology contexts,
ENCODE audits, baseline-state selection, and one categorical vote per donor;
the complete 448-profile source matrix remains available and unchanged. The
prepared baseline layer also records assay-specific capability, global distinct
donor counts, Cell Ontology parent/child dependence, tolerated audit warnings,
and IEI-relevant lineage gaps. Activated/stimulated profiles are not exposed as
a companion view at this stage. When the optional prepared context manifest is
configured, the bottom of the whole-genome variant overview shows a compact
tissue/immune summary and the variant-scoped **Regulatory evidence** workspace
shows categorical tissue and donor-aware immune evidence. The regulatory tab
and controls are hidden for exome imports. Mixed class families,
H3K4me3-associated calls,
accessibility-only calls, and classification-unavailable contexts remain
distinct. The built-in display sets include Immune core, Immune all, and
Select all; users may also save multiple named context sets. A simple
whole-genome **Immune context** variant-list filter requires positive evidence
in any immune-related tissue or curated immune-cell context. Advanced named
sets likewise qualify a variant when any selected context is positive;
missing or negative evidence never acts as an automatic exclusion. SCREEN observations, future regulatory-to-gene
links, and future sequence-model predictions remain separate modules rather
than a combined regulatory score.
For multi-gigabyte inputs, enter the existing absolute workstation path in the
WGS review panel to avoid making an additional browser-upload copy.

PromoterAI and the optional full CADD v1.7 whole-genome scores are exposed only
for WGS jobs. The local UI prepares the two licensed Illumina PromoterAI files
into a compact transcript-aware indexed dataset; it neither downloads nor
redistributes them. CADD can be downloaded/resumed from the dataset setup
screen. That action fetches only the official score-only SNV and gnomAD r4.0
indel tables, indexes, and MD5 files (about 83 GiB), verifies them, and uses the
standard VEP CADD plugin directly. It never downloads the much larger
`inclAnno` tables or creates a duplicate combined VCF.

**Targeted panels / custom exome capture:** point `region.custom_bed` at your
own BED (gene panel, capture kit) and it is used verbatim instead of the
built coding BED.

GRCh38 VCFs using UCSC-style `chr1`/`chrM` contig labels are normalized in a
workspace copy to the Ensembl cache convention (`1`/`MT`) before region
filtering. Coordinates, alleles, FORMAT fields, and genotypes are unchanged.

GRCh37/hg19 inputs are never mixed directly into this step. The controlled
intake writes a derived GRCh38 VCF, a reference-correction audit VCF, an
unsupported-record VCF, a liftover-reject VCF, QC JSON, and provenance JSON
while preserving the original locus in INFO. A source ALT that becomes the
GRCh38 reference is excluded from annotation only when every called sample is
reference after genotype-aware allele remapping.
The original input is not modified. Re-alignment/re-calling against GRCh38 is
preferred when reads are available.

## Configuring annotations

For normal workstation use, open **Import VCF → Run VEP first**. After selecting
files, the next screen shows which required and optional annotation datasets
are available and provides the per-run switches and performance settings.
Users do not need to open or edit YAML.

For scripted/CLI use and administrator reference locations,
`config/annotation.config.yaml` remains the default template. See
**`docs/ANNOTATIONS.md`** for what every source is, which tier it belongs to,
and how to obtain it.

## Try the tiny example (no container, no downloads)

A self-contained smoke test exercises the whole wiring — config parsing, the
VEP command builder, the container-command assembly (dry run), and the ClinVar
amino-acid-match on a simulated VEP output — without needing Docker or any
reference data:

```bash
bash test/test_dry_run.sh
```

Expected tail:

```
[1/3] builder --json
  argv tokens: 40  mounts: 7
[2/3] run_annotation.sh --dry-run
  dry-run assembled OK
[3/3] clinvar_aa_match.py on simulated VEP output
  [aa_match] ref_residues=2 records=3 missense=2 matched=1 csq_usable=True
  match flag present
ALL DRY-RUN CHECKS PASSED
```

Unit tests for the two Python components:

```bash
python test/test_build_command.py   # config -> VEP command (7 tests)
python test/test_aa_match.py         # ClinVar aa-match + reducer (8 tests)
```

To exercise the installed annotation data—not only the command wiring—run the
small public regression panel:

```bash
bash scripts/run_annotation_regression.sh
```

It annotates eight public GRCh38 controls (three known LoGoFunc OR4F5 missense
alleles, NCSTN frameshift, STAT3 missense, IL2RG splice donor, IL2RG
stop-gained, and a TERT promoter variant),
then asserts the PTC-based LOFTEE 50-bp correction, LOFTEE, AlphaMissense,
CADD, SpliceAI, ClinVar, and ClinVar amino-acid matching.
LoGoFunc and promoterAI are tested when their optional local tracks are
installed and reported as explicit `SKIP`s otherwise. The input contains one synthetic sample named
`REGRESSION`; it contains no patient data.

## Output

An annotated, bgzipped VCF. All VEP annotations live in the `CSQ` INFO field,
with every transcript consequence retained and the preferred consequence for
each ALT allele + gene marked `PICK=1`. MANE Select and MANE Plus Clinical are
preferred by the explicit pick order; LOFTEE, plugins and custom tracks are CSQ
subfields; the amino-acid-match adds `INFO/ClinVar_path_aa_match` (0/1). The
`##INFO` header for that flag records the ClinVar release used. Sample columns
(`FORMAT` / genotype) are passed through unchanged, so zygosity is preserved.

For frameshift consequences, the postprocessor recalculates LOFTEE's
`50_BP_RULE` at the premature termination codon created by the shifted reading
frame. Successful calculations replace the value inside `CSQ/LoF_info`.
`LoF_50_BP_RULE_original`, `LoF_50_BP_RULE_PTC`,
`PTC_dist_from_last_exon`, and `PTC_calc_status` preserve the comparison and
provenance. The original `LoF=HC/LC` classification is retained.

For multi-indel events, `INFO/IEI_HAPLOTYPE_FRAME` records the sample,
transcript, partner variant(s), combined protein consequence, and one of three
states: fully restored and phase-confirmed, partially restored (another allele
copy remains disrupted), or possible restoration with unresolved phase. The
review UI excludes only the fully restored state by default. It keeps partial
and unresolved events visible and retains the original per-variant LOFTEE
annotation for audit.

Every completed run also writes two annotation-completeness artifacts beside
the final VCF:

- `<final.vcf.gz>.annotation_qc.json` for software/audit use
- `<final.vcf.gz>.annotation_qc.html` for human review

The certificate uses source-appropriate denominators: AlphaMissense and CADD
on missense records, LOFTEE on predicted loss-of-function records, and
SpliceAI on transcript-overlapping MANE SNVs. It records coverage, limited
examples of missing annotations, ClinVar matches, repeat/segdup overlaps,
configured resource versions, and whether promoterAI is installed. `WARN`
means annotation coverage needs review; it does not remove variants or assign
clinical significance.

## Notes on the container

- The diagnostic profile uses **dbNSFP v5.4a**, the current academic release
  when this profile was updated, distributed by the dbNSFP project as a
  single GRCh38-sorted, tabix-indexed BGZF file ready for VEP. Recent dbNSFP
  releases are built on newer transcript sets than the pinned VEP 113
  image/cache. Coordinate-level dbNSFP lookup works by GRCh38 allele, but
  transcript-specific fields must be regression-tested across this release
  difference. Run `pipeline/check_dbnsfp_version.py` to see update and
  compatibility recommendations before changing either resource.
- **VEP gnomAD frequencies can differ slightly from the gnomAD Browser.**
  Depending on the VEP cache release and matching path, a variant without an
  rsID may not receive a gnomAD frequency even when the normalized allele is
  present in the Browser. Therefore, a blank gnomAD annotation means
  “unavailable from this VEP annotation,” not definitive absence from gnomAD.
  This is expected to have limited practical impact because variants without
  rsIDs are generally rare, but important candidates should be confirmed in
  the gnomAD Browser by normalized chromosome, position, REF, and ALT.
- **LOFTEE must be the `grch38` branch** for GRCh38 (GERP bigwig + GRCh38
  conservation SQL). This is baked into the image at `/opt/vep/src/loftee` and
  exposed to VEP as `$LOFTEE_DIR`; `loftee_path: auto` in the config resolves to
  it inside the container.
- **BCFtools/liftover** is built from pinned bcftools and plugin commits for
  assembly-gap-aware hg19 intake. It uses both source and destination FASTAs,
  remaps genotype/allele-indexed fields, and audits source calls that become
  the GRCh38 reference. See `docs/GRCH37_INPUT.md`.
- See `docker/README.md` for Singularity build instructions and compatibility
  details.

## Development disclosure

This software has been developed with AI-assisted coding using **OpenAI Codex
(GPT-5.6 Sol)** and **Anthropic Claude Code (Claude Opus 5)**, under human
direction and review. AI assistance does not constitute independent software
validation; users remain responsible for validating the pipeline for their
intended research or clinical-laboratory context.

## License

MIT — see `LICENSE`.
