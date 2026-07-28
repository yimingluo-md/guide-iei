# Annotation dataset setup

The local workbench checks annotation files and indexes before VEP runs. From
the landing screen, select **Set up annotation datasets**. The same dataset
panel also appears after a VCF is selected and before an annotation job starts.

The UI shows the configured local path for each file. Patient VCFs are never
sent to any of the sites listed below; these links are used only to obtain
public annotation resources.

## dbNSFP 5.3.1a — manual academic registration

dbNSFP is required by the diagnostic profile and cannot be downloaded
automatically because its academic distribution requires registration.

1. Open the [dbNSFP academic download page](https://www.dbnsfp.org/download).
2. Register with an institutional email address.
3. Use the access code sent by email to request the academic download links.
4. Download and extract the GRCh38 files for release 5.3.1a.
5. From the pipeline directory, prepare one position-sorted, bgzipped,
   tabix-indexed file:

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

## ClinVar — downloadable and refreshable in the UI

Select **Download latest** for ClinVar. The workbench downloads the current
[NCBI ClinVar GRCh38 VCF and index](https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/),
records its release date, and updates the stable local `clinvar_latest` copy.
ClinVar is updated regularly, so this action may be repeated. The default
annotation workflow can also refresh ClinVar immediately before a VEP run.

ClinVar residue matching uses the downloaded release. Its pathogenic
amino-acid residue table is rebuilt automatically when ClinVar changes.

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
- **Frameshift PTC 50-bp rule** — implemented by this software using the
  release-matched GTF and GRCh38 FASTA.
- **ClinVar residue match** — implemented by this software and rebuilt from
  the locally installed ClinVar release.

If a bundled component is reported missing, repair or reinstall the validated
reference bundle rather than substituting an untested release.

## Deferred integrations

promoterAI and LoGoFunc are shown as deferred. This release does not download,
configure, or require either resource.
