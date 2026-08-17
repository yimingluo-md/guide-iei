# FAQ

[Manual home](index.md)

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

## Where do I ask a question that is not answered here?

Open an issue on the
[GitHub repository](https://github.com/yimingluo-md/guide-iei/issues).
