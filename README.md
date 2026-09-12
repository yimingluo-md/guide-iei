# GUIDE-IEI

**G**enomic **U**ser-friendly **I**n-depth **D**iagnostic-analysis
**E**nvironment for **I**nborn **E**rrors of **I**mmunity — a
clinician-developed, locally run, open-source WES/WGS analysis platform.

GUIDE-IEI annotates exome and genome VCFs with a curated annotation stack
(Ensembl VEP, LOFTEE, dbNSFP, ClinVar, ClinGen, SpliceAI,
[AlphaGenome AVI](docs/ALPHAGENOME_AVI.md), ENCODE SCREEN
regulatory data, and more) and can add optional, user-supplied GenIA gene,
phenotype, and exact-allele evidence. It presents the results in a local review
workbench designed for clinician-researchers and wet-lab scientists. All analysis is
performed on the user's own computer. Network access occurs only when the
user initiates a dataset download, an update check, or an optional
per-variant SpliceAI lookup for indels ([FAQ](docs/FAQ.md)).

## Motivation

The developer is a clinician running a genetics clinic and ordering genetic
testing for patients with suspected inborn errors of immunity (IEI). The
universe of IEI has expanded so rapidly that panel testing can no longer keep
up, while access to whole exome sequencing (WES) and whole genome sequencing
(WGS) has improved substantially in the United States.

It is common to see patients in whom a monogenic disorder remains strongly
suspected despite negative testing. Clinical labs differ in their thresholds for
reporting variants of uncertain significance (VUS), and this is a particular
challenge in IEI, whose diverse manifestations may not be well captured by a
standard HPO-based workflow. At the same time, carefully selected VUS can be
re-classified through in vitro functional studies or inform clinical
management. Clinician-researchers and wet lab scientists therefore need a practical way to
re-analyze genomic data as knowledge evolves, to ask, for example, "Could this patient have a
condition described in the literature two weeks ago?", and to identify
plausible VUS that warrant functional investigation or could influence
management.

Meanwhile, discoveries of monogenic disease have been disproportionately
concentrated in protein-coding regions, which constitute only about 2% of the
human genome. Major progress is being made in predicting the effects of
non-coding variation, including deep intronic variants, promoter variants, and
other regulatory elements. Because these methods and resources are evolving
rapidly, bioinformatic infrastructure is needed to translate them into
testable biological hypotheses. So alongside the bread-and-butter annotation
of coding variants, this software is also built to expand capacity for
discovering monogenic etiologies in the non-coding genome.

## What makes it different

- **Local and open source** — Patient data are analyzed entirely on the
  user's workstation, with no subscriptions or per-sample fees.
- **Designed for clinician-researchers and laboratory scientists** — A web-based user
  interface, guided dataset setup, and plain-language explanations make
  analysis accessible.
- **In-depth annotation** — More than 30 computational predictors,
  comprehensive loss-of-function evaluation, including LOFTEE, recalculation
  of the 50-bp rule to assess NMD escape, and detection of potentially
  frame-restoring indel pairs, as well as amino-acid- and residue-match
  annotations.
- **Non-coding prioritization for WGS** — Deep-intronic splice prediction,
  promoter prediction, AlphaGenome AVI SNV impact scores, and ENCODE cCRE
  regulatory context support non-coding variant analysis.
- **Longitudinal local review** — A sample library, phenotype records, trio
  analysis, genotype-first cohort searches, and annotation-coverage reports
  support reanalysis as knowledge evolves.

## What GUIDE-IEI is not

Being clear about scope matters as much as listing features.

- **Not a data-deposition or collaboration platform.** Tools like seqr,
  Franklin, or VarSome Clinical are built around a central server: multi-user
  projects, role-based access, shared curation, large-scale cohort deposition.
  GUIDE-IEI is the opposite by design — one workstation, one reviewer, no
  server. The built-in cohort search is for a single clinic's or lab's local
  collection (tens to hundreds of samples), not for consortium-scale data
  sharing.
- **Not a FASTQ-to-VCF pipeline.** Analysis starts from a called VCF (single-
  or multi-sample, GRCh38 natively or GRCh37 via controlled liftover).
  Alignment, variant calling, and joint genotyping must happen upstream.
- **Not a cloud service.** GUIDE-IEI is intentionally designed for one reviewer
  working on one workstation. Local operation keeps patient data under the
  user's control and avoids subscription fees, but it also means that
  performance depends on the workstation, reference datasets require
  substantial local storage, backups remain the user's responsibility, and
  simultaneous multi-user review is not supported.
- **Not an automated classification or reporting system.** GUIDE-IEI does not
  assign ACMG/AMP classifications, determine pathogenicity, or generate
  clinical reports. It organizes evidence for expert review. Any finding
  considered for patient care must be confirmed and interpreted through an
  appropriately accredited clinical laboratory.

## Research use only

GUIDE-IEI is intended for research and interpretive support. It is not
FDA-cleared and has not been validated for primary clinical diagnostic use by
a CLIA-certified laboratory. Its output is not a diagnostic result. Findings
considered for patient care require independent confirmation and clinical
interpretation through a CLIA-certified laboratory or the relevant accredited
laboratory framework in the user's jurisdiction.

## Quickstart

**Mac beta — [Download the ready-to-run installer](https://github.com/yimingluo-md/guide-iei/releases/download/v0.6.2/GUIDE-IEI-macOS-0.6.2-arm64.dmg).**
For **Apple Silicon (M-series), macOS 13 or newer**. Open the DMG and drag
**GUIDE-IEI** to Applications (a standard account can use `~/Applications`).
The 0.6.2 app is Developer ID signed, notarized and stapled. Python, the review
interface and the VEP engine image are bundled. First launch needs internet
to prepare missing container tools and a Linux VM before opening the browser;
Python, Node, Homebrew and Docker Desktop need not be installed separately.
Choose data locations in **Storage**, then install large annotation datasets
in **Import & QC**. They are not included in the installer.

Use the [release page](https://github.com/yimingluo-md/guide-iei/releases/tag/v0.6.2)
for installation notes, checksums and source materials. Choose the **DMG**, not
GitHub's **Source code** links. This is a public beta, not a claim of testing
every Mac/dataset combination; start with test data and keep backups. Intel
packaging CI is a compatibility check, not a supported Intel standalone release.
See the [release checklist](docs/RELEASE_CHECKLIST.md).

**Windows one-click:** download and fully extract the repository ZIP, then
double-click `desktop/windows/GUIDE-IEI.bat`. The launcher checks for a real
WSL2 Linux environment and offers or explains the one-time Ubuntu installation
when it is missing. Docker Desktop with WSL integration is the simplest
annotation backend; advanced users can use a Linux container engine instead.
If Windows security blocks the unsigned launcher, [start directly in Ubuntu/WSL](docs/guide/02-install.md#windows-start-directly-in-wsl)
without disabling security controls. The
[installation chapter](docs/guide/02-install.md) walks through both platforms.

**Mac source ZIP/checkout:** open `desktop/macos/GUIDE-IEI-Workbench.command`
inside the complete repository. It also prepares the environment automatically;
the source-tree `.app` is not the standalone release.

**Developer/terminal route:** three commands; the managed Mac runtime needs no
administrator rights. Optional package-manager tools and Linux packages are
installed separately
(a factory-fresh Mac should use the graphical release because `git clone`
itself requires Git):

```bash
git clone https://github.com/yimingluo-md/guide-iei.git && cd guide-iei
bash scripts/setup_environment.sh --install   # setup check + user-space install
bash scripts/start_workbench.sh               # launch the workbench
```

Open `http://127.0.0.1:3000` in your browser and keep the terminal open;
annotation datasets are installed
from **Annotate VCF → Set up annotation datasets**, with one-click
recommended sets for exome or whole-genome analysis. The complete
walkthrough, including the command-line route, is in the
**[manual](docs/index.md)**.

## Supported platforms

| Platform | Status | Notes |
|----------|--------|-------|
| **Linux** | ✅ native | Primary target. |
| **macOS 13+** (Intel or Apple Silicon) | ✅ native | Container via Docker Desktop, Podman, or the bundled no-admin Colima setup. |
| **Windows** | ✅ via **WSL2** | Not supported from native Windows shells. [Setup guide](docs/INSTALLATION.md#windows-use-wsl2). |

## Documentation

The **[GUIDE-IEI manual](docs/index.md)** covers everything in detail:

- [Installation & requirements](docs/INSTALLATION.md)
- [Annotation dataset setup](docs/DATASET_SETUP.md) (including dbNSFP
  registration, licensed datasets, and component-based GenIA imports)
- [Running annotation](docs/RUNNING_ANNOTATION.md) (exome vs whole-genome
  scope, GRCh37 input)
- [The review workbench](docs/REVIEW_WORKBENCH.md) (sample library, cohort
  search, WGS review, regulatory evidence, trio analysis)
- [Understanding the output](docs/OUTPUT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Backup and restore](docs/SAMPLE_LIBRARY_AND_STORAGE.md#backup-and-restore)
- [Security & privacy model](docs/SECURITY_AND_PRIVACY.md) — what the single-user workstation design assumes, what leaves the computer, where data lives, and what an update can run

## Future plans

- **Deeper epigenetic context** — tissue- and cell-specific prediction of
  regulatory-element target genes, and direct prediction of variant impact on
  regulatory activity.
- **CNV, SV, and repeat-expansion support** — the current release covers SNVs
  and small indels only.
- **A simple allele-fraction filter for suspected somatic variants** (see the
  [FAQ](docs/FAQ.md)).
- **Native SpliceAI and PromoterAI scoring for indels** — precomputed score
  tables cover SNVs. Indels without a precomputed score are retained and
  explicitly flagged rather than excluded; running the models locally would
  score them directly.

## Development disclosure

This software has been developed with AI-assisted coding using **OpenAI Codex
(GPT-5.6 Sol)** and **Anthropic Claude Code (Claude Fable 5 and Opus 5)**, under human
direction and review. AI assistance does not constitute independent software
validation; users remain responsible for validating the pipeline for their
intended research or clinical-laboratory context.

## License

MIT — see `LICENSE`.
