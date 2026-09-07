---
title: FAQ
nav_order: 4
---

# FAQ

[Manual home](index.md)

## Why does annotation say the container cannot access a file that exists on my Mac?

The Mac and the container's Linux virtual machine have separate filesystem
views. An input or reference on an external drive may be readable on the Mac
but absent inside Colima or Docker Desktop. GUIDE-IEI checks both views before
annotation and names the folder that needs attention. These checks do not
upload data or change VM sharing settings.

- First confirm that the drive is connected and the file is readable. A **host
  access** error indicates a local path, permission, or dataset-installation
  problem, not necessarily a container sharing problem.
- **Colima:** when no jobs are running, stop the active profile and use
  `colima start --edit` to add the named folder to that profile's `mounts` list.
  Mark output/work folders writable and preserve existing mount entries. If
  using a named profile, select that same profile when stopping and starting it.
  With the GUIDE-IEI-managed installation, its executables may not be on your
  terminal's PATH. For the default profile and default tools directory:

  ```bash
  PATH="$HOME/.iei-variant-review/tools/bin:$PATH" colima stop
  PATH="$HOME/.iei-variant-review/tools/bin:$PATH" colima start --edit
  ```

  Substitute the actual tools directory if customized. In the editor, mount
  the specific required folders rather than granting broad disk access.
- **Docker Desktop:** add the named folder in **Settings → Resources → File
  sharing**, apply the change, then retry the job.
- Alternatively, choose input/output/reference locations that are already
  shared. You do not need to keep all data inside the GUIDE-IEI repository.

Annotation scratch files are placed in a private temporary directory beside the
output and cleaned on exit, avoiding macOS's commonly unshared `/var/folders`
temporary location. Do not restart a container engine while another job uses it.

## Setup says a managed Colima dependency (`lima` or `limactl`) is missing

Run `bash scripts/setup_environment.sh --install` from the GUIDE-IEI folder.
It repairs missing launcher links in the managed tools directory, including
older partial installations where Docker is already present. The default
`--check` mode only reports the problem and changes nothing. No Homebrew
installation or administrator access is required for this link repair.

## Can GUIDE-IEI analyze somatic variants?

Not in the current release. GUIDE-IEI is a germline analysis platform: its
filters and population-frequency logic assume constitutional variants, and it
is not a tumor pipeline. Somatic and mosaic variants do matter in IEI — for
example, somatic *FAS* variants in ALPS and mosaic *NLRP3* variants in CAPS
are well described — but reliably detecting them requires appropriate
upstream sequencing depth and calling.

A **simple allele-fraction filter** is planned: it would use the sample's
allele depth (AD/DP) to surface variants whose allele fraction departs from
germline expectations (roughly 50% heterozygous / 100% homozygous), flagging
possible somatic or mosaic events for manual review. Allele fractions from a
standard germline caller are suggestive, not diagnostic; deep targeted
sequencing remains the appropriate confirmation.

## Why is AlphaMissense blank for the selected MANE transcript?

This can be normal. AlphaMissense predictions are specific to a particular
protein/transcript consequence, and the released prediction tables do not
contain a score for every possible consequence on every Ensembl transcript.
GUIDE-IEI shows a score only when the installed source provides one for the
selected transcript; it does not substitute a score from another isoform.
A blank value means **unavailable**, not benign, and does not by itself mean
that annotation failed.

The AlphaMissense authors released reference code and precomputed human
predictions, but [did not release the trained model
weights](https://github.com/google-deepmind/alphamissense#alphamissense).
Their official repository therefore states that the code is not intended for
making new predictions. GUIDE-IEI cannot rerun the published model locally to
fill a missing transcript score, and training a different model would not
reproduce the published AlphaMissense result. Review the other available
variant, transcript, gene, population, and clinical evidence instead.

## Does GUIDE-IEI ever send data off my machine?

Annotation and review are fully local: patient VCFs, genotypes, and
phenotype records do not leave the computer. There is **one deliberate,
opt-in exception**: on the variant page, an indel with no precomputed
SpliceAI score offers a **"Get SpliceAI score online"** action. Invoking it
transmits that single variant's position and alleles (chromosome, position,
REF, ALT — nothing else) to the Broad Institute's public SpliceAI Lookup
service and displays the returned scores, clearly labeled as an online
result. The lookup never runs automatically, never in batch, and each
result is stored locally so a variant is transmitted at most once. Dataset
downloads and ClinVar refreshes also use the internet, but these transfer
only public reference data *to* the machine.

## Can I review a multi-sample cohort VCF?

Yes — directly. A file with 16 or more samples opens in **cohort review
mode**: one row per variant, the usual filters, and a per-variant
**Carriers** panel listing which individuals carry it with their genotype
evidence. Keep an exome in **Exome** scope: large retained exome imports also
use local preparation and a compact browser projection. Use **Whole genome**
only for genome data and its regulatory retention routes. A stored
cohort reopens from the Sample Library (Select all → Open combined
review), which also supports opening one individual or a selected subset,
and removing selected datasets in bulk.
**Cohort search** complements this with genotype-first queries — carriers
of a gene or an exact variant across everything indexed. One bound: an
unfiltered cohort where common variants are carried by nearly everyone can
exceed the browser's carrier capacity; the import then directs you to the
population-frequency prefilter instead of freezing. See the
[cohort analysis chapter](guide/08-cohort-search.md).

## Where do I ask a question that is not answered here?

First check [Troubleshooting](TROUBLESHOOTING.md), including Windows direct-WSL
startup, failed downloads, missing scores, and oversized imports. Share only
redacted logs; do not post patient VCFs or private dataset access links.

Open an issue on the
[GitHub repository](https://github.com/yimingluo-md/guide-iei/issues).
