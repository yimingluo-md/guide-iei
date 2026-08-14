# Annotation reference data

This folder is the software-default location for annotation datasets (VEP
cache, FASTA, LOFTEE, SpliceAI, dbNSFP, CADD, ClinVar, SCREEN, PromoterAI,
LoGoFunc, liftover bundle). It ships empty; only this README is tracked.

Populate it from **Run VEP first → Set up annotation datasets** in the
workbench, or from the command line:

```bash
bash scripts/download_references.sh config/annotation.config.yaml
bash scripts/install_recommended_datasets.sh config/annotation.config.yaml exome
```

Everything in here is re-downloadable or rebuildable and is never committed
(multi-GB files; dbNSFP and PromoterAI are additionally
registration- or license-gated). To keep datasets on another disk instead,
use **Storage → Locations → Annotation datasets** in the workbench.
