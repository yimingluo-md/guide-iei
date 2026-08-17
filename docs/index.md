# GUIDE-IEI Manual

**GUIDE-IEI** — **G**enomic **U**ser-friendly **I**n-depth **D**iagnostic-analysis **E**nvironment for **I**nborn **E**rrors of **I**mmunity.
A clinician-developed, locally run, open-source WES/WGS analysis platform.

This manual covers installation, dataset setup, running annotation, and the
review workbench in detail. For the project overview, see the
[main page](https://github.com/yimingluo-md/guide-iei).

## Getting started

1. [Installation & requirements](INSTALLATION.md) — environment doctor,
   supported platforms, Windows/WSL2, where to keep the clone, verifying the
   installation
2. [Annotation dataset setup](DATASET_SETUP.md) — one-click setup, dbNSFP
   registration, licensed PromoterAI, optional CADD and LoGoFunc

## Using GUIDE-IEI

3. [Running annotation](RUNNING_ANNOTATION.md) — command line and UI routes,
   exome vs whole-genome scope, GRCh37 input, configuration
4. [The review workbench](REVIEW_WORKBENCH.md) — import, Sample Library,
   cohort search, whole-genome review, regulatory evidence, phenotype, trio
   analysis, storage
5. [Understanding the output](OUTPUT.md) — the annotated VCF, LoF curation
   details, the annotation-QC certificate, container notes

## Reference

- [Annotation sources](ANNOTATIONS.md) — what every source is, which tier it
  belongs to, and how to obtain it
- [Dataset reference](dataset-reference.md) — technical details behind each
  dataset card in the UI
- [Reference setup](REFERENCE_SETUP.md) — the dataset setup screen and
  download scripts
- [SCREEN tissue & immune data](SCREEN_TISSUE_IMMUNE_DATA.md) — the prepared
  ENCODE regulatory context layer
- [GRCh37/hg19 input](GRCH37_INPUT.md) — assembly detection, liftover QC,
  provenance, limitations
- [Trio analysis](TRIO_ANALYSIS.md) — pedigree input, de novo tiers,
  compound-het phase
- [Phenotype input](PHENOTYPE_INPUT.md) — demographics and phenotype records
- [Sample Library & storage](SAMPLE_LIBRARY_AND_STORAGE.md) — persistent
  identity, cohort profiles, disk management
- [Gene knowledge](GENE_KNOWLEDGE.md) — HGNC/IUIS/ClinGen and private OMIM
  handling
- [Bundled workbench references](BUNDLED_WORKBENCH_REFERENCES.md) — gnomAD
  constraint and IUIS provenance

## Repository layout

```
config/annotation.config.yaml   administrator defaults used by CLI and the UI
docker/Dockerfile               VEP 113 + LOFTEE grch38 + samtools + DBD::SQLite
docker/build.sh                 build the image (docker or podman)
scripts/setup_environment.sh    host-environment doctor + no-admin bootstrap
scripts/download_references.sh  fetch VEP cache / FASTA / LOFTEE / RepeatMasker / SegDup
scripts/install_recommended_datasets.sh  one-click exome/WGS public dataset setup
scripts/update_refreshable_datasets.sh  refresh ClinVar + ClinGen variant curations
scripts/build_native_reference_bundle.sh  package shipped SCREEN + hg19 resources
scripts/build_coding_bed.sh     build coding+splice BED (Ensembl GTF)
scripts/prepare_dbnsfp.sh       verify + install a downloaded dbNSFP release
scripts/fetch_clinvar.sh        download + version-stamp the latest ClinVar
scripts/run_annotation.sh       main entry point: config -> VEP -> annotated VCF
scripts/liftover_grch37_to_grch38.sh  controlled legacy-VCF intake into GRCh38
scripts/build_clinvar_aa_reference.sh   build the aa-match catalog from ClinVar
scripts/update_workbench_references.sh  rebuild bundled gnomAD/IUIS UI resources
scripts/update_gene_knowledge.sh  rebuild public HGNC/IUIS/ClinGen gene knowledge
scripts/update_clingen_erepo.sh   install/update ClinGen expert variant assertions
scripts/sync_to_onedrive.sh     copy the working tree (no .git) to a synced folder
scripts/start_workbench.sh      launch the workbench (service + UI, supervised)
pipeline/build_vep_command.py   translate the config into VEP argv + bind-mounts
pipeline/loftee_ptc_50bp.py     replace frameshift 50_BP_RULE using the resulting PTC
pipeline/haplotype_consequences.py validate sample GT/phase for frame-restoring haplotypes
pipeline/clinvar_aa_match.py    add INFO/ClinVar_path_aa_match to the VCF
pipeline/clingen_erepo_annotate.py  add exact allele-level ClinGen assertion IDs
pipeline/reduce_vep_to_aa_reference.py  VEP-tab -> aa-match catalog
local_service/                  loopback API + persistent SQLite job queue
webui/                          the local review workbench (Next.js)
test/                           tiny VCF + config + tests (no container needed)
```
