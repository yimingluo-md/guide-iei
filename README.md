# GUIDE-IEI

**G**enomic **U**ser-friendly **I**n-depth **D**iagnostic-analysis
**E**nvironment for **I**nborn **E**rrors of **I**mmunity — a
clinician-developed, locally run, open-source WES/WGS analysis platform.

GUIDE-IEI annotates exome and genome VCFs with a curated annotation stack
(Ensembl VEP, LOFTEE, dbNSFP, ClinVar, ClinGen, SpliceAI, ENCODE SCREEN
regulatory data, and more) and presents the results in a local review
workbench designed for clinicians and wet-lab scientists. All analysis is
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
management. Clinicians and scientists therefore need a practical way to
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
- **Designed for clinicians and laboratory scientists** — A web-based user
  interface, guided dataset setup, and plain-language explanations make
  analysis accessible.
- **In-depth annotation** — More than 30 computational predictors,
  comprehensive loss-of-function evaluation, including LOFTEE, recalculation
  of the 50-bp rule to assess NMD escape, and detection of potentially
  frame-restoring indel pairs, as well as amino-acid- and residue-match
  annotations.
- **Non-coding prioritization for WGS** — Deep-intronic splice prediction,
  promoter prediction, and ENCODE cCRE regulatory context support non-coding
  variant analysis, with plans for further expansion.
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
- **A cloud service.** GUIDE-IEI is intentionally designed for one reviewer
  working on one workstation. Local operation keeps patient data under the
  user's control and avoids subscription fees, but it also means that
  performance depends on the workstation, reference datasets require
  substantial local storage, backups remain the user's responsibility, and
  simultaneous multi-user review is not supported.
- **An automated classification or reporting system.** GUIDE-IEI does not
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

**Mac one-click:** open the [latest release](https://github.com/yimingluo-md/guide-iei/releases/latest),
download `GUIDE-IEI-macOS-<version>.zip`, unzip it, move
`GUIDE-IEI.app` to Applications if desired (a standard account can use its
own `~/Applications` folder), and double-click it. This standalone app works
on macOS 13 or newer on Intel and Apple-silicon Macs and installs its writable
application files and verified user-space runtimes on first launch; no Python, Node,
Docker Desktop, Homebrew, Git, Xcode, or bioinformatics software needs to be
installed first.

**Windows one-click:** download and fully extract the repository ZIP, then
double-click `desktop/windows/GUIDE-IEI.bat`. The launcher checks for a real
WSL2 Linux environment and offers or explains the one-time Ubuntu installation
when it is missing. Docker Desktop is optional for reviewing an already
annotated VCF, but is required to run the VEP annotation engine. The
[installation chapter](docs/guide/02-install.md) walks through both platforms.

> **Mac, first open:** the current release is unsigned, so macOS blocks it —
> a dialog says *"GUIDE-IEI" Not Opened* with only **Move to Trash** and
> **Done**. Click **Done** (not Move to Trash), open **System Settings →
> Privacy & Security**, scroll to the Security section where it says
> *"GUIDE-IEI" was blocked*, click **Open Anyway**, authenticate, and confirm
> **Open**. This is required once for each newly downloaded unsigned release.

**Developer/terminal route:** three commands; nothing is installed outside
managed user folders, and no administrator rights are required on macOS
(a factory-fresh Mac should use the graphical release because `git clone`
itself requires Git):

```bash
git clone https://github.com/yimingluo-md/guide-iei.git && cd guide-iei
bash scripts/setup_environment.sh --install   # setup check + user-space install
bash scripts/start_workbench.sh               # launch the workbench
```

The workbench then opens in the browser; annotation datasets are installed
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
  registration and licensed datasets)
- [Running annotation](docs/RUNNING_ANNOTATION.md) (exome vs whole-genome
  scope, GRCh37 input)
- [The review workbench](docs/REVIEW_WORKBENCH.md) (sample library, cohort
  search, WGS review, regulatory evidence, trio analysis)
- [Understanding the output](docs/OUTPUT.md)

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
