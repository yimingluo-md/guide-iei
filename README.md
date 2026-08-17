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

The developer is a clinician running a genetics clinic and ordering genetic
testing for patients with suspected inborn errors of immunity (IEI). The
universe of IEI has expanded so rapidly that panel testing can no longer keep
up, while access to whole exome sequencing (WES) and whole genome sequencing
(WGS) in the US has improved markedly.

It is common to see a patient with a strongly suspected monogenic etiology but
a negative genetic test. Commercial labs differ in their thresholds for
reporting variants of uncertain significance (VUS), and this is a particular
challenge in IEI, whose diverse manifestations may not be well captured by a
standard HPO-based analysis workflow. At the same time, some VUS can be
re-classified through in vitro functional studies or inform clinical
management. Clinicians therefore need a practical way to re-analyze genomic
data as knowledge evolves, to ask, for example, "Could this patient have a
condition described in the literature two weeks ago?", and to identify
plausible VUS that warrant functional investigation or could influence
management.

Meanwhile, discoveries of monogenic disease have been disproportionately
concentrated in protein-coding regions, which account for only about 2% of the
human genome. Major progress is being made in predicting the effects of
non-coding variation, including deep intronic variants, promoter variants, and
other regulatory elements. Because these methods and resources are evolving
rapidly, bioinformatic infrastructure is needed to translate them into
testable biological hypotheses. So alongside the bread-and-butter annotation
of coding variants, this software is also built to expand capacity for
discovering monogenic etiologies in the non-coding genome.

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
