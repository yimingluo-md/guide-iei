# GUIDE-IEI

**G**enomic **U**ser-friendly **I**n-depth **D**iagnostic-analysis
**E**nvironment for **I**nborn **E**rrors of **I**mmunity —
a clinician-developed, locally run, open-source WES/WGS analysis platform.

GUIDE-IEI annotates exome and genome VCFs with a comprehensive, curated
annotation stack (Ensembl VEP, LOFTEE, dbNSFP, ClinVar, ClinGen, SpliceAI,
ENCODE SCREEN regulatory data, and more) and opens the results in a local
review workbench built for clinicians and wet-lab scientists. Everything runs
on your own computer: patient variants never leave your machine.

## Motivation

<!-- PLACEHOLDER — to be written by Yiming:
     I am a clinician and send genetic testing myself. Commercial software is
     expensive and lacks some critical features. This is user-developed
     software built for the people who actually review the variants. -->

## What makes it different

<!-- PLACEHOLDER — to be edited by Yiming. Skeleton to react to: -->

- **Free and open source** — MIT-licensed, no subscription, no per-sample fee.
- **Runs entirely locally** — annotation and review happen on your own
  workstation; no patient data is uploaded to any service.
- **Built for clinicians and wet-lab scientists** — plain-language dataset
  setup, a click-to-define glossary for every genetics term, and guided
  one-click installation of the recommended datasets.
- **In-depth loss-of-function curation** — LOFTEE plus a PTC-based 50-bp-rule
  recalculation for frameshifts and haplotype-aware detection of
  frame-restoring indel pairs.
- **Whole-genome ready** — a workstation-safe WGS review path with regulatory
  context from ENCODE SCREEN, including curated tissue and immune-cell
  evidence.
- **A real review environment, not just a pipeline** — persistent sample
  library, local cohort search, trio analysis, phenotype records, and
  per-run annotation-QC certificates.

## Research use only

This software is provided for research purposes: variant-of-uncertain-
significance prioritization, novel monogenic etiology discovery, and as a
research/interpretation aid in clinical diagnostics. It is not FDA-cleared and
not CLIA-validated. Any variant used in patient care must be independently
confirmed and validated in a CLIA-certified laboratory; the output of this
software is not a diagnostic result on its own.

## Quickstart

Three commands get a new machine to a running workbench:

```bash
git clone https://github.com/yimingluo-md/guide-iei.git && cd guide-iei
bash scripts/setup_environment.sh --install   # environment doctor + user-space setup
bash scripts/start_workbench.sh               # launch the workbench
```

Then open the workbench in your browser and install annotation datasets from
**Run VEP first → Set up annotation datasets** (one-click recommended setups
for exome or whole-genome work). The full walkthrough, including the
command-line route, is in the **[manual](docs/index.md)**.

## Supported platforms

| Platform | Status | Notes |
|----------|--------|-------|
| **Linux** | ✅ native | Primary target. |
| **macOS** (Intel or Apple Silicon) | ✅ native | Container via Docker Desktop, Podman, or the bundled no-admin Colima setup. |
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

<!-- PLACEHOLDER — to be written by Yiming:
     e.g. more epigenetic data layers, additional disease modules beyond IEI,
     packaged one-click application builds. -->

## Development disclosure

This software has been developed with AI-assisted coding using **OpenAI Codex
(GPT-5.6 Sol)** and **Anthropic Claude Code (Claude Opus 5)**, under human
direction and review. AI assistance does not constitute independent software
validation; users remain responsible for validating the pipeline for their
intended research or clinical-laboratory context.

## License

MIT — see `LICENSE`.
