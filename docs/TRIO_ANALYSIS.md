---
title: "Trio analysis (reference)"
parent: Reference
nav_order: 11
---

# Trio and family analysis

The local workbench can use a mother-father-child pedigree to prioritize de
novo variants and to assign parental origin to qualifying
compound-heterozygous pairs. This analysis occurs after VEP annotation; VEP
annotations remain allele-specific, while family evidence comes from the VCF
`FORMAT` fields.

## Recommended input

A jointly genotyped, multi-sample VCF containing the proband and both parents
is strongly preferred. At every candidate site it should contain:

- `GT` for all three samples;
- `DP` and `GQ`;
- allele depths (`AD`);
- genotype likelihoods (`PL`) when available; and
- phase set fields (`PS` or `PID`) when the callset is phased.

The workbench preserves the complete trio genotype evidence even though only
non-reference carriers become variant-review rows.

Separate ordinary single-sample VCFs may be opened together, but absence of a
record from a parental VCF is not evidence for a `0/0` genotype. Such a
candidate is labelled **possible de novo**, never **high-confidence de novo**.
For stronger evidence, jointly genotype gVCFs or recheck the site in parental
BAM/CRAM files outside this MVP.

## Pedigree input

Open **Family analysis** and either map the three VCF sample names manually or
upload a standard whitespace-delimited six-column PED/FAM file:

```text
family_id sample_id father_id mother_id sex affected
FAMILY_1  CHILD     FATHER    MOTHER    2   2
FAMILY_1  FATHER    0         0         1   1
FAMILY_1  MOTHER    0         0         2   1
```

PED sex uses `1=male`, `2=female`, and other values as unknown. PED affected
status is parsed for compatibility but is not used for phenotype ranking.
Sample names must match the VCF header exactly.

The optional **Parental relationships confirmed** checkbox records reviewer
context only. The workbench does not perform kinship testing and does not
assign ACMG/AMP PS2 or PM6.

## De novo screening

Each proband carrier is classified as:

- **High-confidence de novo candidate** — child carries the ALT allele; both
  parents have well-supported `0/0` genotypes; trio DP/GQ and allele-depth
  evidence meet the configured thresholds.
- **Possible de novo** — a parental genotype, depth, quality, or allele-depth
  measurement is missing or insufficient.
- **Possible parental mosaicism** — a nominally reference parent has ALT
  balance above the configured screening limit.
- **Likely artifact** — proband DP/GQ or allele balance fails the configured
  screen.
- **Mendelian conflict** — the genotype combination is not a simple de novo
  model.
- **Inherited** — at least one transmitting parent has a well-supported ALT
  call. For a homozygous ALT child, an insufficient call in the other parent
  leaves the origin of the other copy unresolved.

Defaults are screening values, not clinical rules:

| Setting | Default |
|---|---:|
| Proband minimum DP | 10 |
| Parent minimum DP | 10 |
| Minimum GQ | 20 |
| Proband ALT balance | 0.20–0.80 |
| Parent maximum ALT balance | 0.02 |

Every variant detail page shows the unmodified GT, DP, GQ, REF/ALT depths,
allele balance, and phase set for all three samples.

### Uniparental contexts: X, Y, and mitochondria

The inheritance model uses the transmitting parent or parents at each
locus. The classifier uses the one-based, inclusive GRCh38 PAR intervals on X and Y
[reported by Olney et al., Figure 1](https://pmc.ncbi.nlm.nih.gov/articles/PMC7374973/).

- **Non-PAR X, male proband** — hemizygous; only the mother's genotype
  gates the de novo call. A `1/1` (diploid-caller style) or haploid `1`
  is the expected shape, not a Mendelian conflict, and the heterozygous
  allele-balance upper bound is waived (a true hemizygous call sits near
  AB 1.0). GRCh38 PAR1/PAR2 boundaries are respected; inside the PARs the
  diploid model applies.
- **Non-PAR Y, male proband** — only the father's genotype is informative.
  GRCh38 Y PAR1 and PAR2 use biparental diploid inheritance.
- **Mitochondria, any proband** — maternal transmission only; a paternal
  ALT call never marks the variant inherited. Heteroplasmy makes allele
  balance continuous, so the diploid upper bound is likewise waived; the
  lower artifact bound still applies as a screen and is adjustable.

Proband sex comes from the PED file or the manual mapping panel. Unknown
sex leaves non-PAR X and Y inheritance unassessed. A Y call in a proband
recorded as female, a haploid autosomal or PAR call, or a heterozygous GT
at an expected single-copy X/Y locus also requires ploidy review.
**Include artifacts, conflicts and unassessed calls** shows these reasons.

## Compound heterozygotes

The analysis first applies the active variant filters independently to both
variants. It then groups distinct heterozygous proband variants by gene and
classifies each pair as:

- **Confirmed trans by inheritance** — one variant is maternal and the other
  paternal.
- **Confirmed trans by phasing** — both variants share a phase set and occur
  on opposite haplotypes.
- **Possible trans** — for example, one inherited variant plus one de novo
  candidate, without physical phase.
- **Phase unknown** — both variants qualify but origin/phase is insufficient.
- **Cis / excluded** — both occur on the same phased haplotype or were
  transmitted by the same parent.
- **Excluded, hemizygous region** — the pair lies on non-PAR X in a male
  proband, where a single haplotype makes a compound heterozygote
  impossible; heterozygous-appearing calls there suggest genotyping
  artifact or an overlapping CNV rather than two alleles in trans.

Mitochondrial pairs and pairs with unresolved ploidy appear under
**Show cis and excluded pairs**. The compound filter and badges use the
remaining pairs. Without a trio, both heterozygous calls must come from
the same carrier, gene and contig. Calls on the same phased haplotype in
one source are excluded. In cohort review, each participating carrier
must pass active QC at both alleles. The homozygous filter likewise
requires a carrier with a qualifying diploid ALT genotype.
The Variant Review **Haploid GT or possible single-copy X/Y** filter finds
haploid ALT calls and diploid-coded ALT calls at non-PAR X/Y when sex is male
or unknown. Unknown sex and diploid encoding leave the latter calls ambiguous;
review ploidy before interpreting them as hemizygous. PAR calls are selected
according to GT ploidy, and active carrier QC applies.

This is candidate discovery, not proof of molecular diagnosis. Review read
evidence, transcript compatibility, gene-disease mechanism, sample identity,
and orthogonal confirmation before clinical use.

## Current MVP limitations

- One trio is active in a browser session at a time.
- The workbench does not joint-genotype separate gVCFs.
- BAM/CRAM read review, kinship verification, and read-backed phasing are not
  yet integrated.
- X-inactivation reasoning, sex-aware models beyond the hemizygous and
  mitochondrial contexts above, and larger pedigrees will require a later
  pedigree-analysis extension.
