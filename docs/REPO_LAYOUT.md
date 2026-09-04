---
title: "Repository layout"
parent: Reference
nav_order: 16
---

# Repository layout

```
config/annotation.config.yaml   administrator defaults used by CLI and the UI
config/predictor-registry.json  typed resources, match scopes, metrics, and licenses
docker/Dockerfile               VEP 113 + LOFTEE grch38 + samtools + DBD::SQLite
docker/build.sh                 build the image (docker or podman)
scripts/setup_environment.sh    host setup check + no-admin bootstrap
scripts/download_references.sh  fetch VEP cache / FASTA / LOFTEE / RepeatMasker / SegDup
scripts/install_recommended_datasets.sh  one-click exome/WGS public dataset setup
scripts/update_refreshable_datasets.sh  refresh ClinVar + ClinGen variant curations
scripts/build_native_reference_bundle.sh  package shipped SCREEN + hg19 resources
scripts/build_coding_bed.sh     build coding+splice BED (Ensembl GTF)
scripts/download_dbnsfp.sh      resume + verify a user-authorized dbNSFP download
scripts/prepare_dbnsfp.sh       verify + install a downloaded dbNSFP release
scripts/fetch_clinvar.sh        download + version-stamp the latest ClinVar
scripts/run_annotation.sh       main entry point: config -> VEP -> annotated VCF
scripts/liftover_grch37_to_grch38.sh  controlled legacy-VCF intake into GRCh38
scripts/build_clinical_protein_catalog.sh  build a ClinVar/ClinGen/GenIA protein-match catalog
scripts/update_workbench_references.sh  rebuild bundled gnomAD/IUIS UI resources
scripts/update_gene_knowledge.sh  rebuild public HGNC/IUIS/ClinGen gene knowledge
scripts/update_clingen_erepo.sh   install/update ClinGen expert variant assertions
scripts/sync_to_onedrive.sh     copy the working tree (no .git) to a synced folder
scripts/start_workbench.sh      launch the workbench (service + UI, supervised)
pipeline/build_vep_command.py   translate the config into VEP argv + bind-mounts
pipeline/predictor_registry.py  validate and expose the immutable predictor contract
pipeline/indexed_scores.py      validate shared indexed-predictor manifests
pipeline/loftee_ptc_50bp.py     replace frameshift 50_BP_RULE using the resulting PTC
pipeline/haplotype_consequences.py validate sample GT/phase for frame-restoring haplotypes
pipeline/clinical_protein_match.py  shared protein-match command (compatibility entry point)
pipeline/clinvar_aa_match.py    implement per-source protein-change/residue evidence
pipeline/prepare_clinical_protein_catalog.py  export and reduce source P/LP missense records
pipeline/clingen_erepo_annotate.py  add exact allele-level ClinGen assertion IDs
pipeline/genia_alleles.py       shared GRCh38 contig/allele normalization for GenIA
pipeline/genia_annotate.py      add exact allele-level GenIA records from local SQLite
pipeline/reduce_vep_to_aa_reference.py  legacy ClinVar VEP-tab catalog reducer
local_service/                  loopback API + persistent SQLite job queue
local_service/genia.py          detect/import five private GenIA components atomically
local_service/test_genia.py     synthetic-only GenIA installer and lookup tests
webui/                          the local review workbench (Next.js)
test/                           tiny VCF + config + tests (no container needed)
```

See [Predictor architecture](PREDICTOR_ARCHITECTURE.md) before adding a new
score source or changing how an existing predictor is matched.
