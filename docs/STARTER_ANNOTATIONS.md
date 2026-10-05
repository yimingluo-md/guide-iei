# Compact starter annotations — implementation status

Developer preparation notes, not instructions for the released application.
The public installer has not changed. Full-data audits and isolated installation
and VEP tests have passed. The [public mirror](https://huggingface.co/datasets/luoyiming1991/guide-iei-essential-annotations-grch38)
was published on 2026-10-04 and anonymous installation passed. The app manifest
now enables all three components, pinned to data revision
`3dae640d2fc6672a5f21e5b53a2ba7a15953b0a5`, with per-file sizes and SHA-256 hashes.
An arm64 Mac preview was built and passed isolated startup and packaged-browser
smoke tests. A full clean-machine installation and signed public release remain
separate gates.

## Implemented foundation

Unit tests (synthetic fixtures; no full dataset downloads) use the same runner
as clean-clone CI, collecting both pytest functions and unittest classes:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q test local_service
```

- `pipeline/starter_annotations.py`: bounded-memory release-time preparation of
  AlphaMissense MANE, coding-SNV CADD Phred, and essential-site SpliceAI subsets.
- `pipeline/predictor_registry.py`: optional `logical_id` and
  `provider_priority`, with pure provider selection. Existing predictors retain
  their original identity, fields, and preference unless callers explicitly use
  the new provider interface. Explicit previous/user choices are not replaced
  silently when their resource is unavailable.
- The registry declares disabled standalone AlphaMissense and CADD providers
  for the existing `IndexedScores` adapter, using distinct annotation fields.
- Unconfigured provider blocks omit `enabled`: managed activation turns them on
  only after a validated starter package is installed. Explicit `enabled: false`
  is respected even without a file path. This also applies to preserved older
  YAML: remove that flag (automatic mode) or set it to true to opt in. Explicit
  table/manifest pairs use the existing generic VEP command builder.
- Preparation writes a new directory atomically, validates duplicate keys,
  builds a tabix index, verifies indexed record counts, and records source/output
  hashes without host paths. It never writes into a user's installed dataset or
  changes annotation configuration.

## Implemented reader integration

`pipeline/starter_evidence.py` and the browser's `starterObservation` consume
the same registry fields and shared contract fixtures. Starter scores feed the
existing AlphaMissense/CADD cards and cohort search columns, not extra cards.

- AlphaMissense requires explicit exact-match provenance, an agreeing stable
  transcript ID, and the same amino-acid substitution and residue position.
  Query protein changes can come from VEP's amino-acid/position fields or HGVS.
- Indexed scores must be scalar, finite and in range; zero is valid. Partial,
  ambiguous, wrong-target and malformed values cannot become filterable scores.
- Existing dbNSFP-only VCFs keep their interpretation. When starter fields are
  declared in a VCF, that starter provider controls its logical score, including
  missing values. A missing starter score is not silently filled from dbNSFP.
  Explicit genome-wide CADD Phred values still take precedence over coding CADD.
- Score magnitude never chooses a provider. Starter Phred is not paired with a
  raw score from dbNSFP. If both providers appear in an external VCF, both source
  observations are retained separately for provenance, but the UI card remains
  singular.
- SQLite import retains the starter provider identity and binds exact
  transcript/protein observations only to their matching annotation.
- Annotation QC reports starter AlphaMissense coverage on MANE Select missense
  SNV records and starter CADD coverage on coding SNV records separately from
  dbNSFP coverage. Run manifests record configured provider IDs, logical IDs,
  match scopes and fields; this is not a claim that every variant has a score.

A complete verified package activates managed starter paths before per-job user
overrides. Installed full dbNSFP/SpliceAI paths and explicit starter paths are
preserved. Without a complete package, the existing dbNSFP requirement remains.

## Unified first-launch setup (development)

- The native window offers **Setup** and **Storage** tabs. It shows the existing
  default/selected annotation location and free-space estimate before the user
  clicks **Prepare GUIDE-IEI**. There is no mandatory license checkbox.
- One service-owned worker reserves setup against annotation jobs, imports,
  updates and storage changes. It prepares the environment, essential references,
  compact predictors, then official ClinVar and ClinGen snapshots in that order.
- Latest ClinVar/ClinGen are fetched when missing. Retries reuse completed
  snapshots; opening the application does not itself refresh them.
- Storage controls call the existing location and verified-migration APIs.
  Selecting another folder does not move data; verified copying retains the
  original. A supervised restart activates changed roots.
- Advanced WGS, registered-user and optional resources stay in the analysis
  workbench. The gene-model-only reference action avoids requiring SCREEN just
  to obtain the GTF used by LOFTEE/PTC. LOFTEE algorithms are unchanged.
- `config/essential-annotations.json` will pin each mirror component separately.
  `pipeline/starter_package.py` checks sizes/hashes, stages downloads, publishes
  components atomically and preserves replaced files for recovery. Full-table
  SpliceAI installation never writes into the immutable starter directory.
- A previously ready installation can reopen without being forced to obtain
  the new package. Clean-machine setup is intentionally unavailable while the
  manifest says `preparing`; release builds reject such a manifest.

`scripts/build_starter_release.py --download --output <new-build-directory>`
builds all three full subsets in the development area. It downloads approximately
120 GB of upstream source data, not patient data. It neither installs nor
publishes its outputs. Validated build: `references/starter-builds/essential-20261004-r2`.
Local mirror staging: `references/starter-releases/essential-20261004-v1`.

## Biological contracts

### AlphaMissense

Use the official GRCh38 canonical and isoform tables; select primary-chromosome
MANE Select stable transcript IDs from the MANE v1.5 summary. Retain the original
source transcript version, protein substitution, original score and label, and
the current MANE transcript version separately. Do not manufacture a score for
a transcript that has no published prediction.

Lookup requires the exact allele, transcript stable ID, and amino-acid change
including residue position, as in the official VEP AlphaMissense plugin's
transcript-matched mode. Matching stable IDs alone is insufficient. This is not
a claim that two transcript versions have identical full protein sequences.
The field-level matching and source versions must remain visible in provenance.

The official canonical and isoform tables sometimes disagree even for identical
allele/transcript/protein keys (observed at 1:12746434:T:A,
ENST00000614859.5, F2I: 0.1201 versus 0.1341). Canonical predictions take precedence
over isoform predictions for the same **allele + stable transcript ID + protein
change** key, including across source transcript versions. Sorting groups this
normalized key before versions; the selected row retains its original version.
This is independent of input order
and score magnitude; overlaps/disagreements are counted. Within the preferred
source class, rows whose version exactly matches pinned MANE v1.5 take precedence.
Conflicts among those exact-version rows (or among other-version rows when no
exact version exists) fail preparation by default. The explicit
`--ambiguous-source-policy withhold` option excludes the complete ambiguous key
and records counts/examples; it never falls back to a supplemental score when
the preferred source conflicts. This is needed for genuine conflicting
duplicates in the published isoform table. Supplemental isoform data extends coverage.
Identical score/label pairs from different versions of the same source collapse
deterministically, preferring exact MANE-version provenance. Unresolved differing
pairs in the preferred source are withheld; no fallback to another source is used. Full
normalized-key uniqueness is checked through the tabix index during preparation
and release validation, and is required by the publication gate.

### CADD

Use the union of protein-coding CDS and terminal stop-codon intervals in the pinned GTF (initial target:
Ensembl 113, matching the current VEP cache). Include all coding SNVs, not just
missense substitutions. Ensembl GTF represents stop codons separately from CDS;
including those features preserves stop-lost SNV coverage. Retain original Phred precision. Exclude raw scores,
indels, UTR-only positions, and intronic padding. The table's score is allele
scoped: a position coding in one transcript can be intronic in another. The
score must not be represented as a prediction specific to that other transcript;
its observation retains the allele-only match scope.

### SpliceAI

Derive the first/last two intronic bases at each MANE Select v1.5 splice junction
from the exact versioned MANE GTF. Both strands are supported; annotated
non-GT/AG junctions are not dropped. Single-exon transcripts contribute no sites.
Keep matching source gene entries at overlapping loci and all four DS/DP values,
including zeros. The MANE summary and its pinned Ensembl GTF sometimes use
different names for the same exact versioned transcript. Both transcript-anchored
names are accepted; the original score entry is retained, not relabeled. This
does not use fuzzy matching or accept unrelated neighboring genes. Do not
substitute older D=50 scores or infer indel coverage.

At annotation time, the essential-site provider uses `SpliceAIStarter`, with
an exact SNV plus stable Ensembl gene match. The engine includes a small,
score-free gene-name map reproducibly generated from the same pinned MANE
summary/GTF (`scripts/build_spliceai_gene_map.py`). It requires those input
hashes in the dataset's preparation manifest. Missing/changed VEP symbols do
not lose scores; unrelated overlapping genes, unknown gene identities and
conflicting source scores do not receive them. Multiple gene entries in one
VCF record are parsed separately. Full optional tables continue to use the
upstream SpliceAI plugin. Source symbols are preserved for provenance, and
both browser and store retain all four delta positions through +/-500 bases.

The source release manifest must identify GRCh38, MANE v1.5, D=500, M=1, and the
input VCF hashes. Preparation rejects files not matching that manifest.
Chr21-only builds are validation artifacts, never whole-genome releases.

## Local preparation

Development dependencies: Python >=3.9, `pysam`, and POSIX `sort`; these are not
new runtime requirements for end users. Large source and output files belong
under the ignored `references/` directory, not in Git.

Example AlphaMissense build from local official sources:

```bash
python -m pipeline.starter_annotations alphamissense \
  --source references/starter-sources/AlphaMissense_hg38.tsv.gz \
  --source references/starter-sources/AlphaMissense_isoforms_hg38.tsv.gz \
  --mane-summary references/starter-sources/MANE.GRCh38.v1.5.summary.txt.gz \
  --release AlphaMissense-2023-MANE1.5 \
  --ambiguous-source-policy withhold \
  --output references/starter-builds/alphamissense-mane1.5-v1
```

CADD takes one `--source` and a protein-coding `--gtf`. SpliceAI takes one or
more chromosome VCFs, `--mane-summary`, `--gtf`, and `--source-manifest`.
Use `--help` for the complete CLI. Existing output directories are never
overwritten: select a new build/version directory for a new preparation.
When a local tabix index is present, CADD and SpliceAI query only relevant
intervals instead of parsing the entire genome-wide source table.

New preparations include a checksummed `LICENSE-NOTICES.json` with full license
text, attribution, retained source notices, and subset modifications. These
notices are also readable offline under **Databases & licenses**. Starter
datasets have no mandatory acknowledgment checkbox; their use restrictions
still apply. Other datasets' existing access requirements are unchanged.

Prepared outputs remain marked `development-not-for-distribution` pending
source-pin review, completeness checks, and package validation. Older development
outputs without notices must be rebuilt before delivery. Preparation does not
fabricate a user's license acknowledgment.

## Measured development builds

These are local preparation results, not installer payloads or installed user
resources. Sizes below use decimal MB (1 MB = 1,000,000 bytes).

| Resource | Verified result | Compressed data + index |
|---|---|---|
| AlphaMissense MANE v1.5 selection (2026-10-05 version-resolution correction) | 67,723,760 unique runtime keys across 24 primary chromosomes; source scores for 17,640 of 19,299 selected transcripts before conflict withholding | 665.9 MB |
| Essential-site SpliceAI, all 24 primary chromosomes | 2,193,822 records: all three alternate SNVs at 731,274 targeted bases | 22.9 MB |
| Coding-SNV CADD | 107,765,334 rows: three alternate SNVs at every one of 35,921,778 coding/stop-codon bases | 527.0 MB |

The AlphaMissense build read 216,256,584 rows from the canonical and isoform
tables. It resolved 977,972 overlapping stable-transcript keys by canonical-source
precedence (968,549 had different scores or labels), removed 728,606 equivalent
duplicates (including 528 across source versions), resolved 95,924 same-source
version conflicts by the exact pinned MANE version, and withheld 12 remaining
ambiguous keys from the preferred source. The exact examples and
source/output SHA-256 values are recorded in `preparation.json`. Source coverage
is not a promise of complete predictions for every variant of every transcript.
The 1,659 MANE transcripts without source scores remain genuinely uncovered.

The initial `2026-10-04-v1` AlphaMissense table is superseded: it contained
518,100 version-colliding runtime keys across 97 MANE transcripts. The correction
first restored an unambiguous prediction for 422,176 of those keys. The subsequent
version-resolution correction recovers the other 95,924 keys across 30 MANE
transcripts: exactly one source row matches the pinned MANE version in each case.
The 12 genuinely unresolved keys remain withheld. This is a preparation correction, not a new model
or recomputed prediction. Existing annotated VCFs require reannotation.

Validation included a complete tabix record-count round trip, strict data/index
SHA-256 verification, registry/manifest compatibility, and real-locus checks of
canonical precedence and ambiguous-key withholding. The coding CADD target
union in the local Ensembl 113 GTF contains 35,921,778 primary-chromosome bases;
the MANE essential splice-site target contains 731,274 distinct intronic bases.
Neither target count establishes that upstream scores exist at every site.

The first whole-genome SpliceAI build retained 2,192,718 records. A subsequent
full-payload audit found 1,104 source records excluded solely because the MANE
summary and Ensembl GTF used different names for their exact same transcripts.
The transcript-anchored name handling above fixes this. The revised build
retains all 2,193,822 records and passed its full-payload audit.

Repeatable opt-in release checks (all outputs go into NEW development folders):

These audit tools refuse `python -O`/`PYTHONOPTIMIZE`, so their assertion gates
cannot silently disappear. The VEP check fingerprints both `IndexedScores.pm`
and `SpliceAIStarter.pm` plus the MANE 1.5 gene map inside the image. New mirror
staging requires those identities in its passing report.

CADD source data and index identities are pinned in
`config/cadd-starter-source.json`, using the previously audited source set.
This prevents silently accepting a changed upstream file/checksum pair; it is
not an independent upstream signature. Changing source releases requires a
reviewed pin update. Prepared components still carry their own SHA-256 pins.
The manifest description records the requested release rather than assuming 1.7.
The pinned SpliceAI source contract deliberately rejects targeted indels and
multi-allelic rows instead of silently omitting unexpected input.

```bash
python scripts/validate_starter_release.py --build "/path/to/build" --output "/path/to/new-audit-folder" --am-versioned-baseline "/path/to/original-r2/alphamissense.tsv.gz"
python test/test_starter_annotation_e2e.py --build "/path/to/build" --output "/path/to/new-vep-folder"
python test/test_essential_clinical_downloads.py --output "/path/to/new-clinical-folder"
node webui/tests/starter-real-payload.mjs "/path/to/new-vep-folder/starter.vep.vcf.gz"
```

The first check verifies real file hashes, complete indexed record counts,
AlphaMissense runtime-key uniqueness across every row and a full-row comparison
against the original versioned table (including Ensembl GTF version agreement),
sampled original-source scores, every retained SpliceAI site's gene context,
and isolated installation/retry/corruption/repair. The second annotates public
synthetic variants through the existing VEP engine after requiring its baked
plugins and gene map to match current source, including ten cross-version regression loci;
it is not validation of app startup on a clean machine.
The third downloads fresh official ClinVar and ClinGen snapshots without
replacing the workstation's installed databases. None substitutes for a
clean-machine test or native-window visual validation.

`scripts/publish_starter_release.py` stages only artifacts identified by passing
audit and VEP reports. Upload requires explicit `--upload`. It writes a candidate
manifest pinned to the returned immutable Hugging Face commit; it does not edit
the application's manifest. The public download/install must also be tested
before activating that candidate. Component-specific license notices travel
with the payload; these data are not relabeled MIT.
Use `--reuse-unchanged-from config/essential-annotations.json` for a component-only
correction: unchanged scores, indexes, manifests and notices keep their previous
immutable pins and installation receipts. The new app plan therefore downloads
only the corrected AlphaMissense component, preserving older installed versions
and all existing analysis results.

The latest focused regression run passed 253 Python tests, covering preparation,
publication gates, installation, registry, command generation, indexed scoring,
run manifests, local service and guide checks. Browser unit tests passed 160
tests with 1 skip; TypeScript checking and native launcher syntax checks passed.

Historical real-payload results (2026-10-04; these checks missed cross-version
AlphaMissense collisions and do not validate the corrected table):

- All data/index/notice hashes and complete tabix record counts passed.
- CADD source comparisons: 312 spatially sampled rows; SpliceAI: 311. Every
  retained SpliceAI row was also checked against the exact MANE splice-site gene
  context. AlphaMissense canonical precedence and withheld examples passed.
- Isolated local-copy installation, retry reuse, corruption detection, repair
  and damaged-copy retention passed. Anonymous public-mirror installation also
  passed: all 14 files, strict hashes, a space-containing selected data path,
  atomic installation and retry reuse without downloading again.
- Existing VEP113 image, with its baked plugin verified against source: 74 public
  synthetic variants passed; 36 exact AlphaMissense, 588 CADD and 372 SpliceAI
  transcript observations agreed with source scores. The cohort reader agreed.
- Browser import of that output: 1,361 transcript-level rows; 36 AlphaMissense
  and 588 CADD values matched VEP; no fabricated CADD raw scores.
- Fresh official ClinVar (release 2026-09-28) and ClinGen setup passed in isolated
  storage. ClinGen mapped 13,230/13,270 active assertions (99.70%) to 13,219 alleles;
  SQLite integrity passed. These are dated test snapshots, not bundled releases.

Evidence is retained under `test/out/starter-validation-20261004-r2`,
`test/out/starter-vep-20261004-r2`, `test/out/essential-clinical-20261004`, and
`test/out/starter-anonymous-download-20261004`.
These checks do not substitute for pending clean-installer/native UI tests.

Historical stable-ID-only correction validation (`2026-10-05-v2`; superseded by the version-resolution correction):

- All 67,627,836 AlphaMissense rows passed a complete normalized runtime-key
  uniqueness scan, strict hash verification and tabix validation. The old table
  is rejected by the new uniqueness check.
- Real VEP113 annotation of 83 public synthetic alleles passed, including all
  nine cross-version regression loci. The browser reader checked 1,576 transcript
  rows: 43 AlphaMissense, 805 CADD and 420 SpliceAI observations agree with VEP.
- CIITA, TAPBP, CFH, DMD, ATRX, HNF1A and COL1A1 examples recover their canonical
  predictions. The MAPT and WT1 examples have conflicting isoform-only source
  records and remain unscored as intended.
- 221 focused Python tests passed, including the local service, package-only
  upgrade/reuse, duplicate-match QC warnings, registry and publication gates.
- Published AlphaMissense component `2026-10-05-v2` at immutable Hugging Face
  revision `41fb66ed1d11ccf9e0687133ccd48cfaa0051964`. Anonymous public download,
  installation, strict hashes and download-free retry passed for the candidate
  app plan. CADD and SpliceAI retain their original component pins.

Evidence: `test/out/starter-validation-20261005-r1` and
`test/out/starter-vep-20261005-r1`, plus
`test/out/starter-anonymous-download-20261005-r1`. No patient records or active installed
databases were modified by these tests.

Version-resolution correction checks (2026-10-05):

- Rebuilt from the original canonical and isoform sources: 67,723,760 rows,
  with 95,924 same-source version conflicts resolved and 12 unresolved keys
  withheld. Canonical-source precedence is unchanged.
- Full-row comparison against all 68,241,860 rows of the original versioned
  table passed, including source scores, labels and transcript provenance.
  All 30 recovered transcript versions agree with the Ensembl 113 GTF.
  Full runtime-key uniqueness, strict hashes, indexes and isolated
  installation/retry/corruption/repair checks also passed.
- Real VEP113 annotation of 84 synthetic alleles passed, including ten
  regression genes. MAPT, WT1 and EIF4G3 recover the exact pinned MANE-version
  prediction. The browser reader checked 1,594 transcript rows: 46 AlphaMissense,
  823 CADD and 420 SpliceAI scores agree with VEP.
- All 939 Python tests passed (15 skipped), including a subprocess regression
  with a competing regular `test` package. Release-test helpers load by path.
- Published AlphaMissense component `2026-10-05-v3` at immutable Hugging Face
  revision `dbfc10be74a60f8d538ee05f6e4950a3039d4e05`. Fresh anonymous installation
  passed for all 14 files, including strict hashes, a space-containing data path,
  atomic installation and download-free retry. The app source now pins this
  release; CADD and SpliceAI retain their original component pins.

Evidence: `test/out/starter-validation-20261005-r2`,
`test/out/starter-vep-20261005-r2`, and
`test/out/starter-anonymous-download-20261005-r2`. Existing installed datasets
and patient records were not changed. Packaged apps need a rebuild to include
the updated pin; previously annotated VCFs require reannotation.

Mac preview verification (2026-10-04):

- Built `dist/macos-starter-preview-20261004`, including the existing verified
  VEP engine and the activated immutable manifest. The production web export,
  TypeScript check and native compilation with warnings treated as errors passed.
- `IEI_TEST_NATIVE_APP=1 IEI_TEST_ESSENTIAL_SETUP=1` packaged smoke passed:
  isolated empty storage, bundled Python, ready public package contract, default
  data path and explicit Prepare gate without starting downloads. Duplicate
  launch handling, Quit and immediate same-port reopening passed.
- Packaged-browser smoke passed with synthetic variants: engine repair controls,
  disconnected/reconnected states, Quit confirmation and shutdown notice,
  preservation/export of loaded review data, and no external browser requests.
- Ad-hoc signature verification passed before and after testing. This preview
  is not Developer ID signed or notarized and is not a public release. Tests did
  not perform the complete native one-click download of all large references.
- The dataset README now links GUIDE-IEI to its GitHub repository; that README-only
  update does not change the application's validated data revision.

## Clean-setup simulation and download recovery (2026-10-04)

- The opt-in `test/test_clean_macos_setup.py` uses the packaged app, empty
  tools/data/library directories and a separate Colima VM. A process sandbox
  hides existing Docker/tool installations and annotation resources without
  deleting them. It downloads real public resources and retains a JSON report
  and logs. `test/test_clean_macos_annotation.py` subsequently checks cold reopen
  and synthetic raw exome/WGS annotation, only if setup succeeds.
- The first real download encountered curl HTTP/2 exit 92 at 84.6% of the VEP
  mirror archive. The old mirror wrapper discarded its partial file and fell
  back to downloading the canonical archive from zero. The test was stopped
  cleanly to correct this; it is not recorded as a successful setup.
- Mirror downloads now use validated resumable ranges and pinned SHA-256
  verification before publishing the completed file. Older sequential `.part`
  files are retried without discarding their prefix on transport errors.
- Synthetic HTTP regressions verify disconnect recovery, process interruption
  and resume without re-fetching completed ranges, checksum rejection, and
  replacement of corrupt same-size files. The focused regression run passed
  58 Python tests plus dbNSFP and ClinVar shell checks.
- An additional 143 annotation/container regression tests passed (201 Python
  tests across the two focused runs, plus the two shell checks).
- A corrected ad-hoc-signed preview was built in
  `dist/macos-starter-preview-20261004-r2`. The fresh real-payload repeat passed
  in 11.1 minutes on this Mac/network: new tools, VM, bundled engine, all
  essential references, three starter components and official clinical data.
  Cold reopening reused the installation without repeating setup; dbNSFP was
  absent and not required. Reports are in `test/out/clean-macos-20261004-r6`.
- Both synthetic raw-VCF annotation jobs completed: 73 exome records versus 74
  whole-genome records. Each produced 36 AlphaMissense, 588 CADD, 372 SpliceAI
  and 219 LOFTEE transcript observations, with genotypes preserved. LOFTEE
  covered all 38 eligible records and recomputed the single frameshift's PTC.
  The test app and its separate VM stopped cleanly afterward. The user's
  installed application and databases are unchanged. This is not an Intel,
  fresh macOS account, or Gatekeeper/notarization test.
- Successful job execution does **not** mean complete score coverage: SpliceAI
  QC warned for 12 of 36 sampled eligible records. At 4:6070055 and 7:150405905
  (and adjacent sites), source symbols `AC092442.3` and `RP11-511P7.5` have no
  matching VEP-cache symbol on the relevant MANE transcripts. The source
  contains scores, but the symbol-gated plugin does not emit them. Investigate
  validated stable-gene identity/alias matching before release; do not silently
  attach scores to a different overlapping gene.
- Native UI observation also found that the selected data-location label may
  remain “Checking the configured data location…” during preparation and on a
  ready cold reopen, despite all outputs using the correct isolated directory.
  This display issue is separate from the corrected download recovery.

## Remaining integration and release gates

1. Exercise installation/repair, provider preferences and storage changes with
   real payloads, including native-window visual validation and restart tests.
2. Validate optional full-SpliceAI MANE v1.5 installation and chromosome routing
   alongside the compact starter. The original 24 chromosome files are reused;
   legacy single-file configurations remain supported.
3. Repeat the passing official ClinVar/ClinGen setup on a genuinely clean machine.
4. Update the user guide only when the new workflow actually works. The compact
   data are downloaded components, not embedded in the application installer.
5. Run clean-machine exome/WGS and upgrade tests; rebuild, sign, notarize and
   inspect the distributable before public delivery. No release is authorized
   by a successful subset build alone.

Follow-up correction (2026-10-04): the two issues above are resolved. The
separately tagged test engine recovered 48 transcript observations (420 SpliceAI
observations total); every emitted score was checked against the original
starter VCF and the mapped source gene. Negative tests cover wrong/missing gene
identity, unrelated overlapping genes, REF/ALT/contig mismatches, indels,
conflicting duplicate scores and incompatible MANE provenance.

The updated packaged preview upgraded only the isolated test engine, reused
the installed references, and passed exome/WGS annotation with **36/36 eligible
SpliceAI records** and overall QC **PASS** in both jobs. Reports are under
`test/out/clean-macos-fixes-20261004`. Native visual inspection confirmed the
correct selected data directory and automatic Storage-tab population. Startup
snapshots retain that directory during preparation, failure/retry and ready
states. Existing annotated VCFs need re-annotation to obtain previously omitted
scores; no starter-dataset re-download is required. This is a local preview,
not a new signed/notarized public release.

The final preview (`dist/macos-starter-preview-20261004-r4`) also passed a cold
reopen without creating any new setup/download jobs, then repeated both raw-VCF
annotation jobs successfully (`test/out/clean-macos-fixes-final-20261004`).
The browser reader retained all 420 SpliceAI observations from the real VEP
fixture, including all 48 recovered gene-name mismatches; all eight score and
position fields matched the VCF. The bundled gene map was independently
regenerated from the pinned sources and compared exactly. The test app and
isolated VM were stopped afterward; the installed user app was not replaced.

## Optional full SpliceAI MANE v1.5 integration

The full-table action now uses the existing public
`luoyiming1991/spliceai-mane-v1.5-d500-m1-snv` release, pinned to revision
`90445b422a04dafd620c206093020ddbdc7817ba`. No new mirror or transformed full
dataset is required. All 24 original BGZF chromosome VCFs and tabix indexes
remain unchanged (34.3 GB compressed VCFs). License and attribution notices
are retained beside the installation. The installer verifies every SHA-256,
preserves interrupted downloads, publishes only a complete installation, and
reuses intact assets during repair. Existing legacy single-file installations
remain usable until explicitly upgraded.

`plugins.SpliceAI.format: mane_v1.5_sharded` selects the manifest-based adapter;
`snv` points to the installed `manifest.json`. Omitting `format` retains the
legacy single-VCF contract. The adapter uses the same validated MANE gene
mapping as the compact starter, routes directly to the matching chromosome,
opens handles lazily per VEP worker, and bounds aggregate result-cache entries
to approximately the single-file cache budget. Full tables cover MANE Select
transcript-span SNVs, not indels or MANE Plus Clinical transcripts.

Opt-in real-data validation is in `test/test_spliceai_full_e2e.py`; the report
is `test/out/spliceai-full-20261004/validation.json`. All 24 original chromosome
files/indexes were hash-verified using previously downloaded public files
(not another fresh 34 GB network download). For 156 public test alleles, all
648 scored transcript observations were identical between chromosome-sharded
and combined-fixture lookup and between one/two VEP workers, with hits on
every chromosome including X/Y. Final small-fixture runtimes were 2.11 seconds
sharded versus 2.07 seconds combined at two workers; this is not a WGS-scale
throughput benchmark. Run manifests record the pinned source and all chromosome
checksums. Source/configuration changes and a separate test engine are prepared;
the installed application and signed public release are not replaced by this test.
