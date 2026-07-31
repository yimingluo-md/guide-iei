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
- **PTC-based LOFTEE 50-bp correction** for frameshifts, calculated locally
  from the release-matched GTF and indexed FASTA.
- **Sample-specific Haplosaurus post-processing** so nearby indels that restore
  a reading frame are reviewed as a haplotype rather than two isolated LoFs.

## Layout

```
config/annotation.config.yaml   administrator defaults used by CLI and the UI
docker/Dockerfile               VEP 113 + LOFTEE grch38 + samtools + DBD::SQLite
docker/build.sh                 build the image (docker or podman)
scripts/download_references.sh  fetch VEP cache / FASTA / LOFTEE / RepeatMasker / SegDup
scripts/build_coding_bed.sh     build coding+splice BED (Ensembl GTF) for region restriction
scripts/prepare_dbnsfp.sh       rebuild a downloaded dbNSFP release for GRCh38 (one-time)
scripts/fetch_clinvar.sh        download + version-stamp the latest ClinVar
scripts/run_annotation.sh       main entry point: config -> VEP -> annotated VCF
scripts/liftover_grch37_to_grch38.sh  controlled legacy-VCF intake into GRCh38
scripts/build_clinvar_aa_reference.sh   build the aa-match catalog from ClinVar
scripts/update_workbench_references.sh  rebuild bundled gnomAD/IUIS UI resources
scripts/sync_to_onedrive.sh     copy the working tree (no .git) to a cloud-synced folder
pipeline/build_vep_command.py   translate the config into VEP argv + bind-mounts
pipeline/loftee_ptc_50bp.py     replace frameshift 50_BP_RULE using the resulting PTC
pipeline/haplotype_consequences.py validate sample GT/phase for frame-restoring haplotypes
pipeline/clinvar_aa_match.py    add INFO/ClinVar_path_aa_match to the VCF
pipeline/reduce_vep_to_aa_reference.py  VEP-tab -> aa-match catalog
local_service/                  loopback API + persistent SQLite job queue
webui/                          local IEI variant-review workbench
docs/ANNOTATIONS.md             per-source reference: what each is, how to get it
docs/GRCH37_INPUT.md             assembly detection, liftover QC, provenance, limitations
docs/TRIO_ANALYSIS.md            pedigree input, de novo tiers, compound-het phase
docs/BUNDLED_WORKBENCH_REFERENCES.md  gnomAD constraint + IUIS provenance
test/                           tiny VCF + config + tests (no container needed)
```

## Requirements

- **Docker** (or Podman / Singularity / Apptainer)
- **Python 3.8+** with **PyYAML** (`pip install pyyaml`) — for the config parser
- **Node.js 22.13+** (including npm) — for the local review UI
- Disk for references: the VEP cache alone is ~25 GB; dbNSFP is a ~50 GB
  download (academic registration required — see below) and needs ~200 GB
  scratch for its one-time GRCh38 rebuild; SpliceAI (if enabled) adds tens of
  GB more.

No VEP, LOFTEE, bgtools, or Perl installation on the host — everything runs in
the container.

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

```bash
# 1. build the container image (once)
bash docker/build.sh

# 2. download the freely-scriptable references (VEP cache, FASTA, LOFTEE,
#    required SpliceAI MANE SNVs, RepeatMasker, SegDup, hg19->hg38 chain)
bash scripts/download_references.sh config/annotation.config.yaml

# 2b. dbNSFP (~50 GB) is NOT auto-downloaded: register at dbnsfp.org/download
#     for an academic access code, request + download v5.3.1a, unzip, then:
bash scripts/prepare_dbnsfp.sh /path/to/dbNSFP5.3.1a_unzipped_dir

# Advisory only: report whether a newer academic dbNSFP release exists.
python3 pipeline/check_dbnsfp_version.py --config config/annotation.config.yaml
#    promoterAI / LoGoFunc: place manually, see docs/ANNOTATIONS.md

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

The workbench also provides a persistent, local **genotype-first cohort
search**. Add directories of annotated single- or multi-sample VCFs once, then
query every carrier of an exact variant/rsID or carriers of qualifying variants
in a gene. The indexed variant, annotation, and non-reference genotype records
stay in `~/.iei-variant-review/cohort.sqlite3`; raw VCF files remain in place.

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

The workbench includes compact, versioned gnomAD v4.1.1 gene-constraint and
IUIS October 2024 IEI resources. It joins pLI/LOEUF and related gene metrics by
gene symbol and loads the IUIS IEI/dominant filters automatically; these
gene-level resources do not need to be added to the VCF. See
`docs/BUNDLED_WORKBENCH_REFERENCES.md`.

VEP annotation resources can be checked and set up from **Run VEP first → Set
up annotation datasets**. The UI provides dbNSFP registration and preparation
instructions, resumable SpliceAI MANE download, latest-ClinVar download, local
path/index checks, bundled-resource provenance links, and visible download
status. See [`docs/REFERENCE_SETUP.md`](docs/REFERENCE_SETUP.md).

The local workbench also stores individual demographics and plain-text
phenotypes separately from sequencing samples. Records may be entered manually
or imported from mapped CSV, TSV, or XLSX columns, with reusable mapping
profiles and sample-link validation. Reported race and reported ethnicity are
kept separate; neither is treated as genetic ancestry. HPO mapping and
phenotype-based prioritization are not performed in this release. See
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
the configured coding+splice BED is retained unconditionally, preserving the
complete diagnostic exome subset. Outside that BED, defaults are gnomAD popmax `< 0.01`, SpliceAI
`>= 0.5`, absolute promoterAI `>= 0.5`, and no CADD retention threshold. An
optional gene list plus symmetric GTF window is an AND restriction. Frequency
and gene restrictions are ANDed with the evidence group; enabled SpliceAI,
promoterAI, and CADD thresholds are ORed. Missing annotation values are retained
rather than treated as negative evidence. The indexed input and filtered result
are fingerprinted and reused when the source and settings are unchanged. The
review copy preserves every retained site but compacts redundant transcript
annotations to MANE, then VEP PICK, then one fallback per allele/gene. The
browser reads BGZF incrementally instead of materializing the complete
decompressed WGS VCF as one large string.
For multi-gigabyte inputs, enter the existing absolute workstation path in the
WGS review panel to avoid making an additional browser-upload copy.

promoterAI and the optional full CADD v1.7 whole-genome track are exposed only
for WGS jobs. Both are bring-your-own resources; the software does not download
the CADD dataset.

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

It annotates five public GRCh38 ClinVar controls (NCSTN frameshift, STAT3
missense, IL2RG splice donor, IL2RG stop-gained, and a TERT promoter variant),
then asserts the PTC-based LOFTEE 50-bp correction, LOFTEE, AlphaMissense,
CADD, SpliceAI, ClinVar, and ClinVar amino-acid matching.
promoterAI is tested when its licensed track is installed and reported as an
explicit `SKIP` otherwise. The input contains one synthetic sample named
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

- The diagnostic profile uses **dbNSFP v5.3.1a**, the current academic release
  when this profile was updated. dbNSFP 5.3.x was rebuilt on GENCODE 49 /
  Ensembl 115, while the currently pinned VEP image/cache remains release 113.
  Coordinate-level dbNSFP lookup works by GRCh38 allele, but transcript-specific
  fields must be regression-tested across this release difference. Run
  `pipeline/check_dbnsfp_version.py` to see update and compatibility
  recommendations before changing either resource.
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
