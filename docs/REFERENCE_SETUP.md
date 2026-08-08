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
  curations. Allow up to approximately 90 GiB on a new installation.
- **Recommended for WGS** installs the exome set plus CADD v1.7 whole-genome
  SNV and indel score tables. Allow up to approximately 180 GiB on a new
  installation.
- **Update installed datasets** refreshes ClinVar and ClinGen variant
  curations. VEP, dbNSFP, CADD, SpliceAI, and SCREEN remain pinned until a
  validated software release changes them.

Existing complete files are verified and skipped, and interrupted supported
downloads remain resumable. The bulk actions cannot obtain dbNSFP or
PromoterAI on the user's behalf: dbNSFP requires academic registration and
PromoterAI requires a separate Illumina license. Their cards are therefore
kept together in the prominent **Registration or license required** section.
LoGoFunc is shown under optional research annotations and is not installed or
enabled by either recommended profile.

## Choose the annotation-data location first

For a normal workstation setup, use **Storage → Annotation datasets → Change
location** before downloading large resources. This central annotation root
holds the VEP cache, FASTA, dbNSFP, CADD, SpliceAI, ClinVar, SCREEN, and other
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

## dbNSFP 5.3.1a — manual academic registration

dbNSFP is required by the diagnostic profile and cannot be downloaded
automatically because its academic distribution requires registration.

1. Open the [dbNSFP academic download page](https://www.dbnsfp.org/download).
2. Register with an institutional email address.
3. Use the access code sent by email to request the academic download links.
4. Download and extract the GRCh38 files for release 5.3.1a.
5. In the dataset card, enter the extracted folder and select **Prepare
   downloaded dbNSFP**. The workbench runs the long preparation locally and
   keeps its progress and log visible. Allow approximately 220 GiB of
   temporary/output space. The command-line equivalent remains:

   ```bash
   bash scripts/prepare_dbnsfp.sh /path/to/dbNSFP5.3.1a_unzipped_dir
   ```

6. Return to the dataset setup screen. It must detect both the configured
   `dbNSFP5.3.1a_grch38.gz` and `dbNSFP5.3.1a_grch38.gz.tbi`.

The prepared dataset is approximately 50 GB. Keep it on fast local storage
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

## ENCODE SCREEN cCREs — included with the native release

The whole-genome import screen uses the public GRCh38 SCREEN Registry V4 cCRE
BED as its default noncoding-region route. This is a native region intersection,
not a VEP plugin or CSQ annotation. The prepared Registry V4 BED, tabix index,
and Ensembl 113 gene-TSS table ship with the native application bundle. If a
bundled file is damaged or missing, the dataset card offers **Repair bundled
files**. The repair command-line equivalent is:

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

## hg19/GRCh37 input — included with the native release

The normalized UCSC hg19 primary FASTA, FASTA indexes, and pinned
hg19-to-GRCh38 chain also ship with the native application bundle. This adds
approximately 915 MB but avoids a fragile first-use download for legacy VCFs.
The dataset screen validates these files automatically and offers **Repair
bundled files** only when one is missing. Re-alignment and re-calling against
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

ClinVar residue matching uses the downloaded release. Its pathogenic
amino-acid residue table is rebuilt automatically when ClinVar changes.

## ClinGen variant curations — installable and updateable in the UI

Select **Install latest snapshot** for the first setup, or **Check and update
snapshot** later. The updater downloads the official public
[ClinGen Evidence Repository](https://erepo.clinicalgenome.org/evrepo/)
classification export and builds two local resources:

- a compact exact-GRCh38-allele VCF for audit and provenance;
- SQLite containing the complete disease-, inheritance-, and expert-panel-
  specific assertion rows, including evidence codes and interpretation text.

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

No separate user setup is required for:

- **LOFTEE** — the GRCh38 plugin is included in the pinned VEP container,
  together with its validated ancestor, conservation database, and GERP
  resources. Project reference: [konradjk/loftee](https://github.com/konradjk/loftee).
- **RepeatMasker** — the included UCSC hg38 track is cleaned and contig-normalized
  for the Ensembl VEP cache. Project reference:
  [RepeatMasker](https://www.repeatmasker.org/).
- **Segmental duplications** — the included UCSC hg38 `genomicSuperDups` track
  is cleaned and contig-normalized. Source reference:
  [UCSC Table Browser](https://genome.ucsc.edu/cgi-bin/hgTables?db=hg38&hgta_group=varRep&hgta_track=genomicSuperDups).
- **ENCODE SCREEN cCREs** — the prepared Registry V4 GRCh38 BED, tabix index,
  and release-matched Ensembl 113 gene-TSS context table.
- **hg19 input bundle** — normalized UCSC hg19 primary FASTA and indexes plus
  the pinned hg19-to-GRCh38 chain.
- **Frameshift PTC 50-bp rule** — implemented by this software using the
  release-matched GTF and GRCh38 FASTA.
- **ClinVar residue match** — implemented by this software and rebuilt from
  the locally installed ClinVar release.

If a bundled component is reported missing, repair or reinstall the validated
reference bundle rather than substituting an untested release.

Release packaging validates exact byte sizes and SHA-256 checksums from
`config/native_reference_bundle.json`. Maintainers build the approximately
943 MiB native payload with `scripts/build_native_reference_bundle.sh`.

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

## Licensed integration

PromoterAI is optional and WGS-only. After obtaining `tss.tsv` and
`promoterAI_tss500.tsv.gz` from Illumina under the user's own license, open the
PromoterAI dataset card in the local UI, enter their folder, and run the local
preparation. The software validates, compacts, indexes, and checksums the files
without shipping or uploading the licensed source data. This is independent of
the public LoGoFunc setup described above.
