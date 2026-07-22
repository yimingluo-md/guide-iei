# WES/WGS diagnostic analysis pipeline

Config-driven, containerized **local** variant annotation with Ensembl VEP +
LOFTEE, for single-sample VCFs. Clone, build the image once, download the
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
- A **single YAML config** where every annotation source is toggled on/off with
  a path — one config works whether or not you have the large/custom datasets.
- A **run script** that builds the exact VEP command from the config and runs
  it in the container (docker / podman / singularity).
- **Reference download helpers** for the freely-scriptable data, plus
  **automatic latest-ClinVar fetch** on every run.
- **ClinVar amino-acid-match** post-processing, adapted from the original awk
  step to work on VCF (`INFO/ClinVar_path_aa_match`).

## Layout

```
config/annotation.config.yaml   the single source of truth for a run
docker/Dockerfile               VEP 113 + LOFTEE grch38 + samtools + DBD::SQLite
docker/build.sh                 build the image (docker or podman)
scripts/download_references.sh  fetch VEP cache / FASTA / LOFTEE / RepeatMasker / SegDup
scripts/build_coding_bed.sh     build coding+splice BED (Ensembl GTF) for region restriction
scripts/prepare_dbnsfp.sh       rebuild a downloaded dbNSFP release for GRCh38 (one-time)
scripts/fetch_clinvar.sh        download + version-stamp the latest ClinVar
scripts/run_annotation.sh       main entry point: config -> VEP -> annotated VCF
scripts/build_clinvar_aa_reference.sh   build the aa-match catalog from ClinVar
scripts/sync_to_onedrive.sh     copy the working tree (no .git) to a cloud-synced folder
pipeline/build_vep_command.py   translate the config into VEP argv + bind-mounts
pipeline/clinvar_aa_match.py    add INFO/ClinVar_path_aa_match to the VCF
pipeline/reduce_vep_to_aa_reference.py  VEP-tab -> aa-match catalog
docs/ANNOTATIONS.md             per-source reference: what each is, how to get it
test/                           tiny VCF + config + tests (no container needed)
```

## Requirements

- **Docker** (or Podman / Singularity / Apptainer)
- **Python 3.8+** with **PyYAML** (`pip install pyyaml`) — for the config parser
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
#    RepeatMasker + SegDup auto-cleaned from UCSC)
bash scripts/download_references.sh config/annotation.config.yaml

# 2b. dbNSFP (~50 GB) is NOT auto-downloaded: register at dbnsfp.org/download
#     for an academic access code, request + download v5.1a, unzip, then:
bash scripts/prepare_dbnsfp.sh /path/to/dbNSFP5.1a_unzipped_dir
#    SpliceAI / promoterAI / LoGoFunc: place manually, see docs/ANNOTATIONS.md

# 3. annotate a single-sample VCF
bash scripts/run_annotation.sh \
    -i /path/to/sample.vcf.gz \
    -o results/sample.vep.vcf.gz \
    -c config/annotation.config.yaml
```

The run will:
1. **restrict the input to coding exons + splice sites** (default; builds the
   BED once from the release-matched Ensembl GTF — see *Scope* below),
2. download the latest ClinVar (version-stamped),
3. (re)build the ClinVar amino-acid-match catalog if ClinVar changed,
4. build the VEP command from your config and run it in the container,
5. write `results/sample.vep.vcf.gz`, then
6. post-process to `results/sample.vep.aamatch.vcf.gz` (+ tabix index) with the
   `ClinVar_path_aa_match` flag added.

Useful flags: `--dry-run` (print the assembled container command and stop),
`--no-clinvar` (skip the per-run ClinVar download), `--all-variants` (annotate
**every** variant, not just coding+splice — for WGS / non-coding work; see
*Scope* below).

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

**Whole-genome / non-coding annotation is not recommended without HPC.** It is
supported — pass `--all-variants` or set `region.coding_only: false` — but on a
workstation expect long walltimes, and note that several of the most useful
non-coding signals (SpliceAI genome-wide, precomputed regulatory tracks) require
large reference files and, for full genome recompute, their own GPU/CPU
pipelines. Run those on a cluster.

**Targeted panels / custom exome capture:** point `region.custom_bed` at your
own BED (gene panel, capture kit) and it is used verbatim instead of the
built coding BED.

## Configuring annotations

Open `config/annotation.config.yaml`. Each source has `enabled` / `required` /
a path. Turn something off, or leave it on but omit the file (with
`required: false`) and it's skipped with a warning. See **`docs/ANNOTATIONS.md`**
for what every source is, which tier it's in, and how to obtain it.

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
python test/test_build_command.py   # config -> VEP command (6 tests)
python test/test_aa_match.py         # ClinVar aa-match + reducer (8 tests)
```

## Output

An annotated, bgzipped VCF. All VEP annotations live in the `CSQ` INFO field
(one entry per picked transcript); LOFTEE, plugins and custom tracks are CSQ
subfields; the amino-acid-match adds `INFO/ClinVar_path_aa_match` (0/1). The
`##INFO` header for that flag records the ClinVar release used. Sample columns
(`FORMAT` / genotype) are passed through unchanged, so zygosity is preserved.

## Notes on the container

- **VEP release 113** is chosen so it lines up with **dbNSFP v5.1a** (GENCODE 47
  / Ensembl 113) and gnomAD v4.1. Change `container.vep_image_tag` in the config
  to move releases — keep the VEP cache release *and* the dbNSFP release in step.
- **LOFTEE must be the `grch38` branch** for GRCh38 (GERP bigwig + GRCh38
  conservation SQL). This is baked into the image at `/opt/vep/src/loftee` and
  exposed to VEP as `$LOFTEE_DIR`; `loftee_path: auto` in the config resolves to
  it inside the container.
- See `docker/README.md` for Singularity build instructions and compatibility
  details.

## License

MIT — see `LICENSE`.
