# IEI Variant Review — local application

Localhost workbench for reviewing VEP-annotated VCFs in an inborn-errors-of-
immunity diagnostic workflow. It is not intended to be deployed as a public or
hosted website.

## Workstation architecture

- The user interface is opened in a browser at `http://127.0.0.1:3000`.
- The UI and annotation service bind to loopback only and are not exposed to
  the LAN.
- Already annotated VCFs are parsed locally. A browser-selected file is copied
  only to the loopback workstation service when the user keeps it in the
  Sample Library or requests server-side WGS preparation.
- Raw VCF annotation is executed by the existing local VEP container pipeline.
- Browser-selected raw VCFs are streamed directly into workstation-local job
  storage; they are never sent to an external service.
- Annotation job metadata and logs persist in `~/.iei-variant-review/`.
- The Sample Library owns content-addressed managed review VCFs under
  `~/.iei-variant-review/sample-library/`. The genotype-first cohort is a
  rebuildable derivative in `~/.iei-variant-review/cohort.sqlite3`.
- This is local workstation software, not a hosted website.

The application lands on **Import VCF**, with **Run VEP first** selected by
default and two deliberately separate paths:

1. **Raw VCF → local VEP annotation** through a persistent SQLite-backed queue.
2. **VEP-annotated VCF → review** directly in the browser.
3. **Many annotated VCFs → persistent cohort index** for genotype-first carrier
   searches.
4. **Retained review → Sample Library** with stable individual/sample/dataset
   identity, provenance, and storage controls.

## Current MVP

- queues raw single- or multi-sample VCFs for local annotation
- accepts GRCh38 directly or performs a controlled GRCh37/hg19-to-GRCh38
  conversion with rejected-record QC and provenance
- accepts one or more annotated `.vcf` / `.vcf.gz` files for review
- excludes non-PASS records during import
- defaults to MANE Select and MANE Plus Clinical consequences, using VEP's
  allele-gene `PICK` consequence only when no MANE transcript exists; all
  transcripts remain available
- displays HGVS, gnomAD popmax, impact, LOFTEE, CADD, AlphaMissense, ClinVar,
  SpliceAI and promoterAI when present
- automatically joins bundled gnomAD v4.1.1 pLI, LOEUF, missense Z, LoF o/e,
  and gene-quality flags by gene symbol
- includes bundled IUIS October 2024 IEI (505 genes) and dominant-inheritance
  (137 genes) filters
- filters by samples, gene sets, impact, gnomAD popmax, ClinVar P/LP,
  homozygous calls, candidate compound heterozygotes and predictor thresholds
- accepts standard PED input or manual proband/mother/father mapping for
  confidence-tiered de novo screening and parental-origin/phase-aware
  compound-heterozygous analysis
- retains parental `0/0` calls, allele-specific AD, PL and phase-set evidence
  from multi-sample records and shows the trio genotype matrix on variant pages
- provides gene, candidate compound-het and saved-candidate views plus TSV export
- indexes directories containing hundreds or thousands of annotated VCFs into
  local SQLite, skipping unchanged files on refresh
- retains compact review VCFs in a persistent Sample Library by default, with
  Review once as an explicit temporary option
- labels and filters heterogeneous cohort profiles by assay and settings hash;
  it does not calculate cohort allele frequencies
- finds carriers by exact GRCh38 variant/locus/rsID or by qualifying variants
  in a gene, with impact, gnomAD popmax, predictor, ClinVar, genotype, MANE,
  RepeatMasker and SegDup filters
- preserves allele-specific multiallelic genotypes, DP, GQ, allele balance,
  phasing status, and source VCF in carrier results
- stores individual demographics and plain-text phenotypes with manual entry
  or confirmed CSV/TSV/XLSX column mapping, reusable import profiles, and VCF
  sample-link validation
- keeps reported race and reported ethnicity separate and does not interpret
  either as genetic ancestry

The gnomAD and IUIS resources are part of the software and require no user
download or VCF annotation. The **Import & QC** page shows their release and
counts and allows deliberate local overrides. Haploinsufficiency is not
inferred from IUIS inheritance or gnomAD constraint; a lab-curated list can be
loaded separately. See `../docs/BUNDLED_WORKBENCH_REFERENCES.md` for provenance
and the maintainer update procedure.

## Run locally

The review UI requires Node.js 22.13 or newer. The launcher can also use the
Node runtime bundled with Codex Desktop when it is present.

From the pipeline root, start both processes:

```bash
bash scripts/start_workbench.sh
```

Then open `http://127.0.0.1:3000`.

Or start them separately:

```bash
# terminal 1, from the pipeline root
python3 -m local_service.workbench_service

# terminal 2
cd webui
npm install
npm run dev
```

The annotation API is available only at `http://127.0.0.1:43117`. The **Import
& QC** page reports whether it is connected. For raw annotation, drop `.vcf` /
`.vcf.gz` files or choose a folder, then review parameters and reference
availability on the next screen. Advanced users can still enter existing
absolute workstation paths.

## Genotype-first cohort search

For routine intake, retain a sample in **Sample Library** and leave **Include
qualifying variants in Cohort Search** enabled. The library can later reopen,
relink, or add that dataset to Cohort Search. Indexed samples show a status
badge rather than a routine maintenance button; repair and rebuild actions
appear only when needed or under **More actions**. Direct path-based cohort indexing remains
available for existing collections.

Open **Cohort search** in the workbench and enter one or more annotated VCF
paths, or a directory containing them. Directories can be scanned recursively.
The indexer accepts single- and multi-sample `.vcf` / `.vcf.gz` files, retains
explicit `FILTER=PASS` records only, and stores non-reference calls in the
local cohort database. Re-adding an unchanged file is fast because it is
recognized by resolved path, size, and modification time.

The cohort is GRCh38-only. Explicit GRCh37 files are rejected. Header-ambiguous
VCFs require checking **I confirm header-ambiguous VCFs are GRCh38** before
indexing. Chromosome labels are normalized
(`chr1` → `1`, `chrM` → `MT`), but alleles are matched exactly, so source VCFs
should use a consistent left-normalized representation.

Two query modes are available:

1. **Exact variant** accepts `4:1004329:C:T`, `4-1004329-C-T`, a locus such as
   `4:1004329`, or an exact rsID. It returns all indexed carriers without
   applying pathogenicity filters.
2. **Qualifying variants in gene** returns carrier calls after the selected
   impact, gnomAD popmax, CADD, AlphaMissense, SpliceAI, ClinVar, genotype,
   MANE, RepeatMasker, and SegDup filters.

Results can be exported as a carrier-level TSV. The cohort index is a discovery
aid, not a substitute for confirming sample identity, relatedness, callability,
or orthogonal validation. A missing carrier means no qualifying non-reference
call was indexed; it does not prove that the sample is confidently homozygous
reference at that locus.

Compact WGS is the recommended default. Full WGS indexing is an advanced
option because indexing every PASS carrier can require about 10 GB per genome.
The **Storage** page reports managed files, temporary uploads, caches, logs, and
reclaimable SQLite space; cleanup and database compaction are explicit actions.
See `../docs/SAMPLE_LIBRARY_AND_STORAGE.md`.

## Trio analysis

Open **Family analysis**, upload a six-column PED/FAM file or assign the
proband, mother, and father manually. The workbench reports high-confidence,
possible, possible-parental-mosaic, artifact and Mendelian-conflict de novo
evidence, with configurable DP/GQ/allele-balance screening thresholds.

Compound-heterozygous pairs are built only after both variants independently
pass the active filters. Pairs are labelled confirmed trans by parental
inheritance, confirmed trans by a shared phase set, possible trans, phase
unknown, or cis/excluded.

A jointly genotyped multi-sample VCF is strongly preferred. Missing records
from separate parental VCFs do not establish `0/0` genotypes and cannot produce
high-confidence de novo calls. See `../docs/TRIO_ANALYSIS.md` for the evidence
model and current limitations.

The queue defaults match the diagnostic workflow:

- coding exons plus splice padding only
- explicit `FILTER=PASS` records only
- ClinVar refresh/use enabled

Each setting can be changed per job. Reference availability, required-source
status, optional tracks, genome build, PASS/coding defaults, ClinVar refresh,
and VEP workers are presented in the UI. Worker count defaults to a conservative
recommendation derived from the workstation's logical CPU count; a bounded
slider allows an override and can be reset to automatic.

The annotation stack is intentionally pinned to the validated VEP 113 / GRCh38
bundle. The UI reports the installed cache and container releases but does not
offer an in-place Ensembl updater. A future VEP or resource upgrade should ship
as a tested software release so that VEP, LOFTEE, transcript resources, plugins,
and the annotation-completeness regression panel remain coordinated.

The default region option is labelled **Exome only** in the UI. Its adjacent
information button works by hover, keyboard focus, or click and explains that
this means protein-coding regions plus essential splice sites, recommended for
a desktop/workstation; whole-genome annotation is better suited to
high-performance computing. Input genome build defaults to detection from VCF
header metadata; an ambiguous header stops for an explicit build choice.

The service writes an internal per-job YAML snapshot for reproducibility;
routine users do not edit YAML.

## Windows through WSL2

Run the complete application inside WSL2, then use the normal Windows browser:

```bash
# in Ubuntu/WSL
cd ~/diagnostic_pipeline/vep-annotate
bash scripts/start_workbench.sh
```

Open `http://127.0.0.1:3000` from Windows. WSL forwards localhost to the
Windows host. Keep the project, inputs, working outputs, and large VEP
references in the WSL Linux filesystem (for example under `~/`) rather than
`/mnt/c/`; annotation is heavily I/O-bound. Docker Desktop must have WSL
integration enabled, or Docker Engine can run directly in the distribution.

For a local production build:

```bash
npm run build
# terminal 1
python3 -m local_service.workbench_service
# terminal 2
npm start
```

Both production processes still bind only to loopback.

## Clinical scope

This is a prioritization and review tool. It does not assign ACMG/AMP
classifications and does not replace validation or clinical interpretation.
