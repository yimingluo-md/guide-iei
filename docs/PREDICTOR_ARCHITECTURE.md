---
title: "Predictor architecture"
parent: Reference
nav_order: 17
---

# Predictor architecture

GUIDE-IEI separates a predictor's biological meaning from the code that reads
its files. The canonical contract is
[`config/predictor-registry.json`](../config/predictor-registry.json), validated
and exposed as immutable Python objects by
[`pipeline/predictor_registry.py`](../pipeline/predictor_registry.py).

This design has two goals:

- a score cannot silently move between alleles, genes, transcripts, or protein
  changes; and
- a conventional indexed predictor can be added without another one-off VEP
  plugin and command-builder branch.

The registry is an executable semantic contract, not automatic UI discovery.
Adding a registry entry does not by itself install a dataset, parse a new
source format, or create a new workbench panel. Those integration points are
listed in [Adding a predictor](#adding-a-predictor).

## The three registry layers

Each entry belongs to one of three layers:

| Layer | It defines | Important fields |
|-------|------------|------------------|
| **Resource** | The files and terms behind one data source | assembly, distribution method, license acknowledgement, public source page, config path, required/indexed assets |
| **Annotator** | How the resource attaches data to a biological target | adapter, implementation, exact match scope and dimensions, normalization, transcript-version policy, fallback and cardinality |
| **Predictor** | The scientific output presented or filtered | applicability, defaults, optional status, metric fields, types, roles, ranges, directions and filterability |

One resource can support more than one annotator. dbNSFP is the main example:
transcript-specific protein predictors and allele-level conservation/CADD
fields read the same installed file but have different matching contracts.

The available adapters are `vep_builtin`, `vep_plugin`,
`generic_indexed_lookup`, `vep_custom_track`, `postprocessor`, and
`service_lookup`. The last is reserved for evidence resolved by the local
service; no active predictor uses it yet.

`config/annotation.config.yaml` remains the operational configuration: it
selects enabled resources and gives their local paths. The registry says what
those resources and outputs mean. Do not duplicate match semantics in a new
configuration flag.

### Metric contract

Every metric has a stable lower-snake-case ID and an exact VCF/CSQ field name.
It also declares:

- a value type: `float`, `integer`, `category`, `boolean`, or `text`;
- a role such as score, probability, prediction, classification, flag,
  coordinate, distance, count, or provenance;
- score direction: `higher`, `lower`, `absolute`, or `none`;
- an optional valid numeric range;
- whether the value is derived; and
- whether it is filterable.

Filterable metrics are stored as typed cohort values. Non-filterable fields
are compact provenance, not accidental filter columns. A score and its source
target must therefore be registered separately.

## Match scopes

The scope determines the identity of one prediction observation. The registry
validator requires the dimension set to agree exactly with the scope, so a
developer cannot label a gene-specific score as an allele-only score.

| Scope | Required identity | Typical use |
|-------|-------------------|-------------|
| `none` | no biological target | Runtime metadata; no active predictor currently uses it |
| `allele` | chromosome, position, reference, alternate | CADD, conservation, ClinVar/ClinGen/GenIA and allele-attached regional flags |
| `allele_gene` | allele + stable Ensembl gene ID | FuncVEP |
| `allele_gene_symbol` | allele + gene symbol | SpliceAI's source-gene contract |
| `allele_transcript` | allele + Ensembl transcript | Transcript-specific dbNSFP outputs |
| `transcript_consequence` | allele + Ensembl transcript + Sequence Ontology consequence | VEP SIFT/PolyPhen, LOFTEE and PTC recalculation |
| `allele_transcript_protein` | allele + transcript + protein position + amino-acid change | LoGoFunc |
| `allele_transcript_tss_strand` | allele + transcript + transcription start site + strand | PromoterAI |
| `interval` | chromosome, start, end | A source interval when its coordinates are retained in the observation; no active predictor currently publishes this scope |
| `gene_protein_residue` | gene symbol + protein position | A residue catalog independent of a genomic allele; no active predictor currently publishes this scope |
| `sample_haplotype` | sample + allele + phase set + transcript | Phased frame-restoration evidence |

An allele always means the normalized genomic
`chromosome:position:reference:alternate` identity on the annotator's declared
assembly. Contig and allele
normalization policies are explicit registry fields. Transcript scopes also
declare whether versions are exact, removed to stable IDs, or tried exact and
then stable.

The allowed match statuses are `exact`, `partial`, `ambiguous`, and
`unmatched`. Only an exact match may be treated as a target-specific score.
Partial or ambiguous observations can retain useful provenance—for example,
"the allele exists in the source"—but must not copy a score to a different
gene or transcript. Fallback and cardinality fields document whether that
provenance is permitted and whether zero, one, or several source records may
exist.

Two details are deliberately explicit:

- RepeatMasker and segmental-duplication data originate as intervals, but VEP
  currently emits only an aggregate overlap label and not the source interval
  coordinates. Their published observations are therefore allele-scoped. Use
  `interval` only when the observation retains `start` and `end`.
- Clinical-source amino-acid matching is computed from transcript/residue
  evidence upstream, while its compatibility flags are published per ALT
  allele. The accompanying detail tokens retain the matched transcript and
  source record, allowing the browser to bind evidence to the selected
  transcript instead of treating an allele-wide positive as a MANE match.

## Current predictor mapping

The JSON registry remains authoritative. This table summarizes every active
annotator in schema version 1:

| Annotator and current outputs | Adapter | Scope |
|-------------------------------|---------|-------|
| `vep_core` — VEP core SIFT and PolyPhen | `vep_builtin` | `transcript_consequence` |
| `dbnsfp` — AlphaMissense, REVEL, SIFT, PolyPhen-2 and the other transcript-specific protein predictors | `vep_plugin` | `allele_transcript` |
| `dbnsfp_allele` — coding CADD, GERP++, phyloP, phastCons and MutationTaster | `vep_plugin` | `allele` |
| `loftee` — LOFTEE | `vep_plugin` | `transcript_consequence` |
| `spliceai` — SpliceAI | `vep_plugin` | `allele_gene_symbol` |
| `cadd_wgs` — CADD whole-genome | `vep_plugin` | `allele` |
| `promoterai` — PromoterAI | `vep_plugin` | `allele_transcript_tss_strand` |
| `logofunc` — LoGoFunc | `vep_plugin` | `allele_transcript_protein` |
| `funcvep` — FuncVEP CTI, CTE and SP | `generic_indexed_lookup` | `allele_gene` |
| `repeatmasker`, `segdup` — regional overlap labels | `vep_custom_track` | `allele` |
| `clinvar` — ClinVar assertions | `vep_custom_track` | `allele` |
| `clingen_erepo` — ClinGen Evidence Repository assertions | `postprocessor` | `allele` |
| `genia` — registered-user GenIA variant records | `postprocessor` | `allele` |
| `loftee_ptc_50bp` — frameshift PTC/50-bp recalculation | `postprocessor` | `transcript_consequence` |
| `haplotype_consequences` — phased frame-restoration evidence | `postprocessor` | `sample_haplotype` |
| `clinvar_aa_match` — ClinVar protein-change/residue flags and details | `postprocessor` | `allele` |
| `clingen_aa_match` — ClinGen protein-change/residue flags and details | `postprocessor` | `allele` |
| `genia_aa_match` — GenIA protein-change/residue flags and details | `postprocessor` | `allele` |

The registry also inventories non-predictor resources such as liftover,
ENCODE cCREs, SCREEN context and the VEP runtime. They participate in setup
and reproducibility but do not create a typed predictor observation merely by
being listed.

Postprocessor order is also part of the runtime contract. The main run writes
the VEP result, applies PTC/haplotype and the shared clinical-source protein
matcher, then applies ClinGen exact-allele records followed by optional GenIA
exact-allele records. QC and the run manifest are written last. This keeps each
source's multi-record INFO evidence independent and ensures QC reads the final
fields.

## Runtime contracts

The same registry is consumed at several boundaries:

1. **Startup and command construction.**
   [`scripts/preflight.sh`](../scripts/preflight.sh) and
   [`pipeline/build_vep_command.py`](../pipeline/build_vep_command.py) discover
   enabled generic indexed annotators from the registry, validate their
   assets, and require the shared container plugin before starting VEP.
2. **Annotation output.** Built-in VEP logic, source-specific adapters, the
   shared indexed adapter, custom tracks, and postprocessors emit the exact
   fields declared as predictor metrics.
3. **Browser review.** [`webui/app/vcf.ts`](../webui/app/vcf.ts) converts VCF
   fields to normalized `PredictorObservation` objects. At parse time it
   rejects unknown predictors, wrong scopes, out-of-scope target dimensions,
   wrong value types or numeric ranges, and values placed on the wrong side
   of the value/provenance boundary. Optional registry-pinned binary
   classifications are also evaluated here so every UI consumer uses the same
   threshold and labels.
   Predictor-specific presentation remains explicit UI code.
4. **Service and cohort storage.** The local service validates installed
   manifests before advertising supported managed datasets.
   [`local_service/cohort_store.py`](../local_service/cohort_store.py) derives
   canonical target keys from registry dimensions and stores observations,
   releases, typed filterable values, and compact provenance separately.
5. **QC and reproducibility.** Annotation QC checks biologically applicable
   outputs. [`pipeline/write_run_manifest.py`](../pipeline/write_run_manifest.py)
   records the registry identity and configured resource assets beside each
   result.

The browser and cohort representations are intentionally parallel but serve
different lifetimes. Browser observations support immediate local review;
cohort observations persist release identity and typed values in SQLite.
Neither is permitted to invent a match scope that the registry does not
declare.

## Generic indexed-score adapter

Use the shared adapter for a precomputed GRCh38 table whose target can be
described by a genomic allele and, optionally, an Ensembl gene, Ensembl
transcript, or protein change. Its files are:

- a coordinate-sorted BGZF TSV;
- its tabix `.tbi` index; and
- a `guide-iei.indexed-scores/v1` JSON manifest.

[`pipeline/indexed_scores.py`](../pipeline/indexed_scores.py) is the canonical
manifest validator and [`docker/IndexedScores.pm`](../docker/IndexedScores.pm)
is the VEP adapter. The manifest declares:

- resource ID, name, release and GRCh38 assembly;
- optional consequence applicability;
- table columns and index metadata;
- required match dimensions and their normalization;
- typed output fields, ranges, directions and descriptions;
- optional binary-classification thresholds, labels, comparison direction and
  public threshold-set provenance;
- four required provenance fields: match, match status, source target and
  allele availability; and
- optional installed-file names, sizes, timestamps and SHA-256 checksums.

`match.required` begins with `allele`. The currently supported additional
manifest dimensions are `ensembl_gene_id`, `ensembl_transcript_id`, and
`protein_change`. The command builder checks that these dimensions agree with
the registry scope, that applicability agrees with predictor applicability,
and that every output/provenance field agrees with the registry's type, range,
direction, role and filterability. It then invokes one `IndexedScores` plugin
for that resource.

A numeric registry metric may declare `binary_classification` when an upstream
release publishes an authoritative binary cutoff. The threshold must be finite,
inside the metric's value range, and use the comparison implied by score
direction. New manifests copy this metadata from the registry. Older manifests
without it remain valid because classification is derived from the current
registry; a manifest that declares conflicting metadata is rejected. Such a
label describes the predictor's stated endpoint and must not be presented as a
clinical classification unless the upstream contract truly defines one.

The adapter emits scores only when exactly one record satisfies every required
dimension. An allele-only match, missing query target, or duplicate exact
record emits partial/ambiguous provenance without scores. This behavior is a
safety property and should not be weakened for convenience.

The registry vocabulary is broader than the generic adapter. A future
gene-symbol table, TSS/strand table, retained interval, or sample haplotype is
representable in the registry, but is not currently a supported generic
manifest dimension. Supporting one generically requires a coordinated change
to both `pipeline/indexed_scores.py` and `docker/IndexedScores.pm`, plus Python
and Perl parity tests. Until then, use the appropriate specialized adapter.

## Adding a predictor

Follow this order so scientific matching is settled before UI work begins.

1. **Review the source and its terms.** Record the assembly, release identity,
   permitted distribution, authoritative public landing page, checksum, file
   format, row uniqueness, and update policy. Decide whether users must supply
   the file.
2. **Define the biological key.** Write down exactly which of allele, Ensembl
   gene, gene symbol, transcript/version, consequence, protein position/change,
   TSS/strand, interval, sample and phase set are required. Define normalization,
   cardinality and what partial or ambiguous means. Never infer a narrower key
   merely because it is easier to query.
3. **Choose the adapter.** Prefer `generic_indexed_lookup` for a conventional
   allele plus Ensembl gene/transcript/protein table. Reuse a supported VEP
   plugin when its matching semantics are correct. Use a postprocessor for
   derived multi-record or sample-aware evidence. Add a specialized adapter
   only when the shared contract cannot express the source safely.
4. **Extend the registry.** Add the resource and its assets, one annotator, and
   one or more predictors. Declare every emitted score and provenance field.
   When an authoritative release supplies a binary cutoff, declare it once as
   metric `binary_classification` metadata with labels and source provenance.
   If a new match scope or metric concept is genuinely required, extend the
   enums and dimension validation rather than using an approximate existing
   scope.
5. **Add configuration and setup.** Add a `plugins.<Name>` or other appropriate
   config block, preflight/status checks, and a clear optional dataset card.
   A generic adapter removes per-predictor command-building code; it does not
   remove source-specific download, validation, preparation, or UI consent.
6. **Prepare data transactionally.** Validate the source before replacing an
   installed version. Normalize contigs/alleles and stable IDs, reject duplicate
   target keys unless cardinality permits them, sort and index the table, then
   write the manifest last. Preserve enough release/checksum metadata to
   reproduce a run.
7. **Normalize output consumers.** Map emitted fields into browser observations
   and cohort observations with the registry metric IDs. Add a dedicated UI
   panel only when it improves interpretation; generic typed storage and
   filtering should not require another SQLite schema column.
8. **Add applicability-aware QC and documentation.** Missing data must not be
   treated as benign. Explain score direction, scope, limitations, intended
   interpretation and license/setup requirements.
9. **Test exact, partial, absent and ambiguous cases.** Include multi-ALT input,
   contig aliases, normalized indels where applicable, transcript versions,
   overlapping genes, duplicate source rows, missing targets, out-of-range
   values, stale indexes and malformed manifests.

### Licensed or user-supplied data

Never commit a private, expiring, institution-proxy, signed, or credentialed
download URL. The registry `source_url` must be a credential-free public
landing page. Do not put access tokens in config, a run manifest, a dataset
manifest, logs, tests, screenshots, or issue text.

The preferred workflow is either an acknowledged download from a stable,
credential-free official URL or a local file picker followed by validation and
preparation. If a source can only be fetched with user authorization, keep the
authorization material in a short-lived local secret outside the repository,
redact it from subprocess output, and store only public source metadata and
content checksums. `license_ack_required` records that the workflow must obtain
an acknowledgement; it does not grant redistribution rights. Licensed score
rows must not be bundled in fixtures—use tiny synthetic records instead.

FuncVEP is the reference implementation for this model: after acknowledgement,
GUIDE-IEI downloads the official pinned archive or accepts an existing copy;
the preparer verifies its identity, excludes ClinVEP, creates the local indexed
table, and records public release provenance without shipping the licensed
scores.

GenIA uses the local-file variant of this policy. GUIDE-IEI links only to the
[GenIA homepage](https://geniadb.org/) and its
[published paper](https://doi.org/10.1016/j.jaci.2023.11.022); it includes no
credential, account-specific or direct-download URL, and no source records.
`local_service/genia.py` detects five independently installable schemas,
rebuilds a derived SQLite database transactionally, copies forward installed
components omitted from an update, and stores provenance per component. The
four gene/phenotype components use local service lookup; the GRCh38 variant
component feeds the allele-scoped `genia` postprocessor. Its upstream CSI index
is not part of the installed asset contract.

## Test checklist

At minimum, a predictor change should cover these layers:

- registry schema, references, dimensions, metrics and licensing;
- dataset preparation, malformed input, duplicate keys and transactional
  replacement;
- Python/Perl manifest-validator parity for generic indexed predictors;
- preflight and VEP command generation for optional and required modes;
- exact, partial, ambiguous, absent and multi-ALT annotation behavior;
- browser observation scope, target, metric/provenance split and presentation;
- cohort release identity, canonical target key, typed filtering and reimport;
- QC applicability and run-manifest resource provenance; and
- documentation with no private links or licensed data.

Focused local checks include:

```bash
python3 test/test_predictor_registry.py
python3 test/test_indexed_scores_plugin.py
python3 test/test_funcvep_dataset.py
python3 test/test_build_command.py
python3 test/test_preflight.py
python3 -m unittest local_service.test_predictor_storage
python3 -m unittest local_service.test_genia
(cd webui && npm test)
```

Run the repository's broader test and container smoke suites before release.
The real source dataset should also receive one release-level validation on a
supported clean machine; synthetic fixtures prove logic, not compatibility
with a newly published upstream file.
