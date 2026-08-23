---
title: "Quality control and sanity checks"
parent: User Guide
nav_order: 10
---

# Quality control and sanity checks

A wrong result that *looks* reasonable is the most dangerous output any
pipeline can produce. GUIDE-IEI ships three layers of defense; this chapter
shows how to use them, and — the part no software can fully automate — what
plausible numbers look like.

## The annotation-QC certificate

Every completed run writes two files beside the output VCF: a
machine-readable `*.annotation_qc.json` and a human-readable
`*.annotation_qc.html`. Open the HTML after any run that matters.

The certificate reports **coverage per annotation source, each against its
appropriate denominator** — AlphaMissense and CADD against missense
records, LOFTEE against predicted-LoF records, SpliceAI against the MANE
SNVs it can score — so "95% coverage" means 95% of the records that source
*should* have annotated, not a diluted whole-file average. It also records
the resource versions used, ClinVar matches, repeat/segdup overlap counts,
and concrete examples of any missing annotations.

A **WARN** means annotation coverage needs review — a dataset may be
missing, stale, or mismatched. It never removes variants and never implies
anything about pathogenicity. Typical causes, in order: a dataset not
installed, a dataset installed after the run (re-run to pick it up), or an
input whose variants fall outside a source's scope.

*[Screenshot: annotation-QC certificate HTML]*

## The regression panel

One command answers "is my installation annotating correctly?":

```bash
bash scripts/run_annotation_regression.sh
```

It annotates eight public GRCh38 control variants (known missense,
frameshift, splice-donor, stop-gained, and promoter alleles in NCSTN,
STAT3, IL2RG, TERT, and OR4F5) and asserts the expected values from every
installed source — LOFTEE and the PTC-based 50-bp correction, AlphaMissense,
CADD, SpliceAI, ClinVar and the amino-acid match. Optional sources report
an explicit `SKIP` when not installed, never a silent pass. The input
contains one synthetic sample and no patient data.

Run it after first setup, after any dataset update, and whenever a result
makes you doubt the installation.

## Plausible magnitudes

Orders of magnitude to carry in your head. Your numbers will vary with
capture kit, caller, and ancestry — but not by orders of magnitude:

| Stage | Exome | Genome |
|---|---|---|
| PASS variants in the VCF | tens of thousands | ~4–5 million |
| After Compact WGS prefilter | — | tens of thousands (a real example: ~4.3 M → ~23,000) |
| Rare (popmax ≤ 0.01) HIGH-impact | dozens | dozens |
| Rare HIGH + MODERATE | low hundreds | a few hundred |

**When to stop and investigate rather than interpret:**

- **Implausibly few** — a genome showing only a handful of rare
  protein-altering variants is a broken filter or a data problem, not a
  clean genome. A contig-naming mismatch between input and reference
  files, for example, can silently empty a filter route while the run
  completes without error; the implausible count is the only visible
  symptom.
- **Implausibly many** — tens of thousands of rare HIGH-impact calls
  usually means a build mismatch (GRCh37 data annotated as GRCh38), a
  frequency source that failed to attach, or non-PASS records included.
- **A blank column** — if an entire annotation column is empty, check the
  QC certificate and dataset readiness before anything else.

**The checklist when numbers look wrong:** open the QC certificate → check
dataset readiness on the setup screen → confirm the input's genome build →
run the regression panel → re-run the setup check
(`bash scripts/setup_environment.sh`). Each step localizes the problem
further; together they cover the failure modes seen in practice.
