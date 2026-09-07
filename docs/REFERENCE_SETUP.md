---
title: "Reference setup"
parent: Reference
nav_order: 8
---

# Annotation dataset setup

The local workbench checks annotation files and indexes before VEP runs. From
the landing screen, select **Set up annotation datasets**. The same dataset
panel also appears after a VCF is selected and before an annotation job starts.

The UI shows the configured local path for each file. Patient VCFs are never
sent to any of the sites listed below; these links are used only to obtain
public annotation resources.

## Quick setup for non-technical users

The top of the dataset screen provides three primary actions:

- **Recommended for exome** installs the pinned VEP cache/FASTA and LOFTEE
  data, SpliceAI MANE, RepeatMasker, SegDup, ClinVar, and ClinGen variant
  curations, plus missing gene-model, cCRE/TSS, and liftover references.
- **Recommended for WGS** installs the same set plus SCREEN tissue/immune
  contexts. CADD whole-genome is a separate optional download, not part of
  this action. The SpliceAI table is the same MANE SNV resource in both profiles.
- **Update installed datasets** refreshes ClinVar and ClinGen variant
  curations. VEP, dbNSFP, CADD, SpliceAI, and SCREEN remain pinned until a
  validated software release changes them.

The free-space check includes a conservative preparation allowance, not just
download bytes; do not interpret it as a measured installed size. See
[disk-space planning](guide/03-datasets.md#plan-disk-space-first).

Existing complete files are checked and skipped, and interrupted supported
downloads remain resumable. The bulk actions cannot obtain dbNSFP or
PromoterAI on the user's behalf because dbNSFP requires academic registration
and PromoterAI requires a separate Illumina license. FuncVEP remains outside
the recommended bulk profiles because it is optional and requires explicit
acknowledgement; its own card can download the official archive directly after
that acknowledgement. dbNSFP and PromoterAI remain prominent guided setups;
FuncVEP appears with LoGoFunc, CADD, and OMIM under **Optional add-ons**.
GenIA appears under **User action needed** because its files must be supplied
by the user, but remains optional.

The recommended action also downloads any missing large reference payload:
the Ensembl gene model used by the
frameshift rule, the SCREEN cCRE/gene-TSS files, and the GRCh37 liftover bundle.
An interrupted repair keeps completed files and can be retried from the same
button.

## Choose the annotation-data location first

For a normal workstation setup, use **Storage → Annotation datasets → Change
location** before downloading large resources. This central annotation root
holds the VEP cache, FASTA, dbNSFP, CADD, SpliceAI, FuncVEP, the derived GenIA
database, ClinVar, SCREEN, and other
release-matched tracks. The UI resolves stock `references/...` configuration
paths through this selected root for both VEP jobs and resource downloads, so
the bundle remains coherent without exposing YAML paths in ordinary use.

Use a fast internal or continuously attached external SSD. The Storage page
tests write access, reports used/free space, and checks enough free space
before starting its large downloads. An advanced absolute path already present
in the annotation configuration remains a per-dataset override; it is not
silently moved when the central root changes. See
[`docs/SAMPLE_LIBRARY_AND_STORAGE.md`](SAMPLE_LIBRARY_AND_STORAGE.md#configurable-workstation-locations)
for safe migration and external-disk guidance.

## dbNSFP — paste the academic GRCh38 download link

dbNSFP is required by the diagnostic profile. GUIDE-IEI cannot obtain access
on a user's behalf because the academic distribution requires registration,
but it handles the download and installation after the user supplies the link.

1. Open the [dbNSFP academic download page](https://www.dbnsfp.org/download).
2. Register with an institutional email address.
3. Use the access code sent by email to request the academic download link.
4. Copy the link whose filename ends in `_grch38.gz`. The email also lists a
   similarly named `_grch37.gz` file; do not use that GRCh37 link. The release
   version in the filename may change over time. The correct link may be a long
   Outlook Safe Links address shown by an institutional email client.
5. Paste it into the dbNSFP dataset card and select **Download and install
   dbNSFP**. GUIDE-IEI unwraps Safe Links, derives the matching `.tbi` and
   `.md5` URLs, downloads with eight resumable connections, verifies the
   published MD5 and tabix index, and installs the files into Annotation
   datasets storage.

The link is never placed in a job log or configuration file and its private
temporary file is removed when the job ends. A retry can resume completed
ranges even if the user has to request a refreshed academic link.

The installed dataset is approximately 52 GB. Keep it on fast local storage
when possible. Do not select every dbNSFP column by default; the annotation UI
provides a curated set of additional predictors for each run.

## SpliceAI MANE v1.4 — downloadable in the UI

Select **Download / resume** for SpliceAI MANE. The workbench uses a resumable
validated download and installs both:

- [Ensembl masked MANE v1.4 GRCh38 SNV scores](https://ftp.ensembl.org/pub/data_files/homo_sapiens/GRCh38/variation_plugins/spliceai_scores.masked.snv.ensembl_mane_v1.4.grch38.vcf.gz)
- [Tabix index](https://ftp.ensembl.org/pub/data_files/homo_sapiens/GRCh38/variation_plugins/spliceai_scores.masked.snv.ensembl_mane_v1.4.grch38.vcf.gz.tbi)

The VCF is approximately 27 GB. Keep the workbench and computer running during
the initial download. If the connection is interrupted, select
**Download / resume** again.

## CADD v1.7 whole genome — downloadable in the UI

CADD is optional and appears only for Whole genome annotation. Select
**Download / resume** on its dataset card, or run:

```bash
bash scripts/download_cadd_wgs.sh config/annotation.config.yaml
```

The resumable downloader installs and MD5-verifies only the files consumed by
the standard VEP CADD plugin:

- `whole_genome_SNVs.tsv.gz` and its tabix index
- `gnomad.genomes.r4.0.indel.tsv.gz` and its tabix index
- the four official MD5 sidecars

This requires about 83 GiB. The `inclAnno` files (625 GB and 11 GB), 335 GB
release bundle, and dbscSNV file are not downloaded because the plugin emits
only `CADD_RAW` and `CADD_PHRED`. No combined VCF or second large copy is
created. The references are mounted read-only and queried with tabix.

CADD is available for non-commercial use; review the
[official CADD v1.7 GRCh38 directory and terms](https://kircherlab.bihealth.org/download/CADD/v1.7/GRCh38/)
before enabling it. The official indel table covers gnomAD genomes r4.0
indels, so a novel indel may have no precomputed score. Missing scores remain
missing; CADD is displayed after import and is not a WGS import criterion.

## ENCODE SCREEN cCREs — installed by dataset setup

The whole-genome import screen uses the public GRCh38 SCREEN Registry V4 cCRE
BED as its default noncoding-region route. This is a native region intersection,
not a VEP plugin or CSQ annotation. The prepared Registry V4 BED, tabix index,
and Ensembl 113 gene-TSS table are installed by reference setup; the current
application archive should not be assumed to contain them. The dataset card
offers download/repair controls when they are missing. The command-line equivalent is:

```bash
bash scripts/download_references.sh config/annotation.config.yaml --only ccre
```

The downloader pins the official Registry V4 source, keeps primary contigs and
the cCRE accession/class columns, normalizes chromosome names, and creates a
BGZF/tabix BED. It also derives a compact table of all primary-contig,
gene-level TSS positions from the release-matched Ensembl 113 GTF already used
by the pipeline. No additional user annotation file is needed. Updating either
release in a future software version will invalidate and rebuild the relevant
local context.

## hg19/GRCh37 input — installed by dataset setup

The normalized UCSC hg19 primary FASTA, FASTA indexes, and pinned
hg19-to-GRCh38 chain are installed by reference setup. Allow for these files
and their preparation space even when using a packaged application.
The dataset screen checks availability and offers repair when necessary.
Re-alignment and re-calling against
GRCh38 remains preferable when source reads are available.

When a variant is opened, the review screen always reports cCRE status,
including an explicit **does not overlap** result. For every overlapping cCRE,
it displays the EH38E accession, overall class, coordinates, and Ensembl gene
TSS context within 500 kb of the cCRE interval with a strand-aware signed
distance. The complete protein-coding and non-protein-coding result is retained;
the interface shows protein-coding genes by default and provides an option to
reveal the rest. These genes are a proximity inventory, not target-gene
predictions. In particular, the nearest or VEP-annotated gene is not necessarily
regulated by the cCRE; experimental and later cell/tissue-specific regulatory
evidence must be considered separately.

## ClinVar — downloadable and refreshable in the UI

Select **Download latest** for ClinVar. The workbench downloads the current
[NCBI ClinVar GRCh38 VCF and index](https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/),
records its release date, and updates the stable local `clinvar_latest` copy.
ClinVar is updated regularly, so this action may be repeated. The default
annotation workflow can also refresh ClinVar immediately before a VEP run.

ClinVar protein-change and residue matching uses the downloaded release. Its
P/LP missense catalog is rebuilt automatically when ClinVar or the VEP cache
changes.

## ClinGen variant curations — installable and updateable in the UI

Select **Install latest snapshot** for the first setup, or **Check and update
snapshot** later. The updater downloads the official public
[ClinGen Evidence Repository](https://erepo.clinicalgenome.org/evrepo/)
classification export and builds two local resources:

- a compact exact-GRCh38-allele VCF for audit and provenance;
- SQLite containing the complete disease-, inheritance-, and expert-panel-
  specific assertion rows, including evidence codes and interpretation text.

The annotation workflow also maintains a compact P/LP missense catalog for
ClinGen protein-change and residue matching. It is bound to the ClinGen source
checksum and VEP cache, and is rebuilt rather than reused when either changes.

ClinGen assertions are not collapsed to a single strongest classification: the
same allele can have different assertions for different diseases or modes of
inheritance. Retracted records are retained only in the audit database and are
not shown as active evidence. The preparation must map at least 99.5% of active
rows to exact GRCh38 alleles; unresolved complex representations are counted in
the manifest and are never approximated by regional overlap.

An update downloads into a temporary directory, validates schema, UUID
uniqueness, mapping coverage, checksums, and SQLite integrity, then replaces the
working snapshot. A failed update leaves the preceding snapshot intact.
Annotation runs use only this installed local copy—patient alleles are never
submitted to a ClinGen API. The VCF output records stable assertion UUIDs and
core metadata; full review details are resolved from the matching local
snapshot, with a compact VCF fallback if that snapshot later becomes
unavailable.

Command-line equivalent:

```bash
bash scripts/update_clingen_erepo.sh config/annotation.config.yaml
```

## Components included with the validated software bundle

These components are part of the validated stack. Their code may ship with
the application/container, but large reference data are installed separately
by the recommended setup:

- **LOFTEE** — the GRCh38 plugin is included in the pinned VEP container,
  while its validated ancestor, conservation database, and GERP resources
  are downloaded during setup. Project reference: [konradjk/loftee](https://github.com/konradjk/loftee).
- **RepeatMasker** — the downloaded UCSC hg38 track is cleaned and contig-normalized
  for the Ensembl VEP cache. Project reference:
  [RepeatMasker](https://www.repeatmasker.org/).
- **Segmental duplications** — the downloaded UCSC hg38 `genomicSuperDups` track
  is cleaned and contig-normalized. Source reference:
  [UCSC Table Browser](https://genome.ucsc.edu/cgi-bin/hgTables?db=hg38&hgta_group=varRep&hgta_track=genomicSuperDups).
- **ENCODE SCREEN cCREs** — the prepared Registry V4 GRCh38 BED, tabix index,
  and release-matched Ensembl 113 gene-TSS context table.
- **hg19 input bundle** — normalized UCSC hg19 primary FASTA and indexes plus
  the pinned hg19-to-GRCh38 chain.
- **Frameshift PTC 50-bp rule** — implemented by this software using the
  release-matched GTF and GRCh38 FASTA.
- **Clinical-source protein matching** — implemented by this software and
  rebuilt from the locally installed ClinVar, ClinGen, and optional GenIA
  variant sources.

If a bundled component is reported missing, repair or reinstall the validated
reference bundle rather than substituting an untested release.

A separate maintainer tool, `scripts/build_native_reference_bundle.sh`, can
build an approximately 943 MiB payload with byte-size/SHA-256 checks from
`config/native_reference_bundle.json`. It is not currently invoked by the
standard application release workflow; its existence is not evidence that
those payload files are inside a release ZIP.

## LoGoFunc — optional public download

The dataset screen can download/resume and validate the pinned public release
from [Zenodo record 13835271](https://zenodo.org/records/13835271). The source
table is approximately 3.66 GB and remains external to the repository. To use
the command line instead:

```bash
bash scripts/download_logofunc.sh config/annotation.config.yaml
```

If the source file and its `.tbi` index already exist, use **Use existing
source** in the UI or run:

```bash
bash scripts/prepare_logofunc.sh /path/to/LoGoFunc
```

Preparation verifies the published MD5 checksums, BGZF/tabix structure, GRCh38
coordinates, columns, probabilities, prediction classes, and a sorted sample.
It then creates local reference links and a provenance manifest without
copying the large table. LoGoFunc is optional and annotation continues without
it when unavailable.

## FuncVEP — optional licensed archive

FuncVEP predicts functional effects for all possible missense SNVs represented
in its GRCh38 release. GUIDE-IEI does not bundle or redistribute the licensed
scores. Review its
[PolyForm Strict License 1.0.0](https://polyformproject.org/licenses/strict/1.0.0),
then use the **FuncVEP** card under **Optional add-ons**:

The upstream project identifies this license for FuncVEP. It permits qualifying
noncommercial uses but does not grant distribution or software-modification
rights. The
Zenodo record does not expose a separate dataset-license field, so users must
confirm that their intended local use is authorized rather than relying on
GUIDE-IEI to make that determination.

1. Review and explicitly acknowledge the linked license terms.
2. Select **Download and install FuncVEP**. GUIDE-IEI fetches the pinned ZIP
   resumably from the [official Zenodo record](https://zenodo.org/records/20595206).
3. Alternatively, choose an existing official ZIP with the native file picker.
4. Keep at least 24 GiB free for automatic download plus preparation, or 20 GB
   free when preparing a ZIP stored on another filesystem.

The workstation verifies the archive's pinned identity and ZIP integrity,
streams its table without unpacking an 11 GB permanent copy, retains only
`FuncVEP_CTI`, `FuncVEP_CTE`, and `FuncVEP_SP`, sorts and BGZF-compresses the
GRCh38 table, builds a tabix index, and records checksums and the match contract
in a local manifest. Processing stays local. An automatically downloaded ZIP
is retained in Annotation datasets storage; a selected ZIP remains unchanged
in its original location. Nothing is uploaded or redistributed.

The equivalent command is:

```bash
# Automatic official download and preparation:
bash scripts/download_funcvep.sh \
  config/annotation.config.yaml --acknowledge-license

# Existing official ZIP:
bash scripts/prepare_funcvep.sh \
  /absolute/path/to/FuncVEP_and_ClinVEP_scores_all_possible_missense_variants.zip \
  config/annotation.config.yaml --acknowledge-license
```

Annotation is emitted only for an exact GRCh38 allele and version-stripped
Ensembl gene stable ID match. CTI includes clinically trained component
predictors; CTE excludes clinically trained predictors and also excludes
AlphaMissense because its development used ClinVar variants for model selection
and tuning; SP excludes all
features derived from other variant-effect predictors. Higher values predict
greater functional damage. GUIDE-IEI applies the final publication's binary
cutoffs (CTI ≥0.419606448098318, CTE ≥0.519261866786599, and SP
≥0.440940891937106) and displays **Damaging** or **Neutral**. These are
functional-effect predictions, not clinical pathogenicity classifications or
ACMG PP3/BP4 evidence strengths. The official archive also contains ClinVEP
scores, but this integration deliberately neither prepares nor displays them.
FuncVEP remains optional, and an absent archive does not prevent other
annotation.

## GenIA — optional registered-user components

[GenIA](https://geniadb.org/) provides immune gene–disease, phenotype, and
variant records described in its
[published paper](https://doi.org/10.1016/j.jaci.2023.11.022). GUIDE-IEI does
not bundle GenIA credentials, download URLs, source exports, or data rows.
Obtain the GenIA exports outside GUIDE-IEI, then use
the **GenIA** card under **User action needed**:

1. Select one or more local export files.
2. Review the detected roles. GUIDE-IEI recognizes the GEI gene–disease list,
   disease catalog, disease–phenotype associations, phenotype vocabulary, and
   GRCh38 variant VCF by schema rather than filename.
3. Select **Add or update GenIA files**. Any single component or subset is
   valid; installed components not selected in this update remain installed.

The downloaded VCF's CSI index is not required. GUIDE-IEI reads the selected
files in place, validates them, and writes only a private derived
`genia/genia.sqlite3` under Annotation datasets storage. Component provenance
contains the source filename, SHA-256, detected schema, usable row count, and
installation time. The user-managed source files are not copied, retained by
GUIDE-IEI, changed, or uploaded.

Installation uses a new temporary database and publishes it only after every
selected component and SQLite integrity have passed. A web login page,
unrecognized schema, malformed component, or failed update therefore leaves
the current GenIA database intact. A partial update copies forward the omitted
installed component tables and their provenance.

If the card reports that the derived GenIA index is unreadable, omitted
components cannot be copied safely. Select all five exports to reinstall the
complete index, or select the available subset, then check the separate
**Replace the unreadable derived GenIA index** confirmation. A subset repair discards the unreadable
index and installs only the selected components; every omitted component must
be added again later if it is still needed.

The four gene/phenotype roles work independently: GEI supplies source
relationships and curation status; `IEI=Y` disease-catalog rows can supply
relationships with unknown curation status; disease–phenotype associations can
stand alone; and the vocabulary enriches installed associations but creates no
gene assertion on its own. The **GenIA GEI gene** filter uses only genes from
the GEI gene–disease list; the other four component types do not add genes to
that filter.

The fifth role is separate allele evidence. It must explicitly declare
GRCh38/hg38, and GUIDE-IEI matches its records only to the exact normalized
chromosome, position, reference, and alternate. Ambiguous source alleles with
`N`, and source `REF` alleles that disagree with the configured GRCh38
reference, are not indexed; the card reports the reason counts without treating
the otherwise successful installation as failed. The source codes include `NC` (**Not classified**) and `RF`
(**Risk factor**); neither is reinterpreted as a clinical classification.
Likewise, `Relevant_in=0` is a zero reported-subject count, not benign
evidence. The export has no gene/disease context, so the exact-allele display
does not infer either from a selected transcript.

Only this fifth component enables GenIA protein matching. On the next
annotation run, GUIDE-IEI derives a P/LP missense catalog with the configured
VEP cache. The source short name is not trusted as a gene or transcript;
matching uses VEP-derived gene, stable Ensembl transcript, protein position,
and amino acids as comparison coordinates only; it does not create GenIA
gene–disease context. A later update of only a gene or phenotype component
preserves the catalog belonging to the installed variant component.

## PromoterAI — licensed integration

PromoterAI is recommended for whole-genome analysis (WGS-only). After obtaining `tss.tsv` and
`promoterAI_tss500.tsv.gz` from Illumina under the user's own license, open the
PromoterAI dataset card in the local UI, enter their folder, and run the local
preparation. The software validates, compacts, indexes, and checksums the files
without shipping or uploading the licensed source data. This is independent of
the public LoGoFunc setup described above.
