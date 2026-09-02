---
title: Glossary
nav_order: 5
---

# Glossary

<!-- GENERATED from webui/app/glossary-data.ts by webui/scripts/export-glossary.mjs.
     Do not edit by hand — edit the glossary data and re-run the exporter. -->

Plain-language definitions of the genetics terms used across GUIDE-IEI. The
same definitions appear inside the application: click any underlined term to
see it in place.

## Genome & data

**genome build** *(also: GRCh38, GRCh37, hg19)*
: The reference version coordinates refer to. GRCh38 is current; hg19/GRCh37 is the older build still used by some labs. A variant's position differs between builds, so build mix-ups produce wrong results.

**VCF**
: Variant Call Format: the standard file listing an individual's differences from the reference genome, one variant per line, with genotype and quality information.

**exome** *(also: whole genome, whole-genome, WES, WGS)*
: Exome sequencing covers the ~2% of the genome that codes for protein; genome sequencing covers essentially everything, including regulatory regions.

**coding region** *(also: coding regions, non-coding, noncoding, protein-coding)*
: Coding regions are translated into protein. Non-coding regions include introns, regulatory elements, and everything between genes; variants there act mainly by altering splicing or gene regulation.

**transcript** *(also: transcripts)*
: One version of a gene's RNA. Most genes have several; a variant's predicted effect can differ between transcripts, which is why one is chosen for primary display.

**MANE Select** *(also: MANE Plus Clinical, MANE)*
: The single transcript per gene jointly agreed by NCBI and Ensembl as the clinical standard. Some genes also have a MANE Plus Clinical transcript — an additional isoform needed to interpret certain diseases — so one variant can legitimately carry two MANE-designated consequence rows; the one-row-per-variant display shows MANE Select and counts the rest in a +N chip.

## Variant effects

**missense**
: Changes one amino acid to another. Effect ranges from harmless to severe; the protein-damage predictors exist mainly to help judge these.

**synonymous**
: Changes the DNA without changing the amino acid. Usually harmless, but can occasionally affect splicing.

**premature stop** *(also: nonsense, stop-gained, stop gained, premature termination)*
: Creates an early “stop” signal, truncating the protein.

**frameshift** *(also: frameshifts)*
: An insertion or deletion that shifts the three-letter reading frame, garbling the protein from that point and usually creating a premature stop downstream.

**loss of function** *(also: loss-of-function, pLoF, LoF)*
: A variant expected to abolish a gene copy's function — premature stops, frameshifts, and canonical splice-site changes. “Predicted LoF” is a claim needing confidence checks (see LOFTEE).

**splice site** *(also: splice sites, splice-site, canonical splice site, splice region)*
: The bases where introns are cut out of RNA. The two bases at each end (canonical site) are critical; nearby changes can also alter splicing (see SpliceAI).

**nonsense-mediated decay** *(also: NMD)*
: The cell's disposal system for RNAs with premature stops. If the damaged RNA is destroyed, little protein is made; variants that escape NMD may instead produce a truncated protein.

**impact**
: VEP's coarse severity grouping: HIGH ≈ protein-truncating or splice-destroying; MODERATE ≈ protein-altering (mostly missense and in-frame changes); LOW/MODIFIER ≈ unlikely to change the protein.

## Population frequency

**allele frequency** *(also: allele frequencies, population frequency)*
: How common a variant is in a reference population. Variants common in healthy people are unlikely to cause severe early-onset disease.

**popmax** *(also: gnomAD popmax)*
: The variant's highest frequency in any single major population (e.g., East Asian, African). A variant rare worldwide may still be common in one population.

**gnomAD**
: The largest public collection of population sequencing data (~800k individuals), the standard source for allele frequencies. A blank value here means “not seen by this annotation,” not proof the variant is unknown.

## Predictors & scores

**CADD**
: A general deleteriousness score. Scaled so ≥20 means the top 1% most deleterious of all possible variants, ≥30 the top 0.1%. Higher = more likely damaging.

**SpliceAI**
: Predicts splicing disruption, 0–1. ≥0.5 is commonly treated as significant, ≥0.8 as high-confidence; works for variants well outside classic splice positions, including deep intronic variants.

**AlphaMissense**
: DeepMind's missense predictor; score 0–1 with calibrated classes (likely benign / ambiguous / likely pathogenic).

**LOFTEE**
: Classifies predicted loss-of-function consequences as high confidence (HC) or low confidence (LC) for a particular transcript and gives reasons. This is not functional evidence or a pathogenicity classification.

**conservation** *(also: conservation scores, GERP, phyloP, phastCons)*
: How strongly evolution has preserved a position across species. High conservation implies change is poorly tolerated.

**pLI** *(also: LOEUF, constraint)*
: Gene-level (not variant-level) intolerance to loss of function, from population data. pLI ≥ 0.9 or LOEUF < 0.6 ≈ genes where losing one copy matters — where dominant LoF disease is plausible.

**PromoterAI**
: Predicts whether a variant near a gene's transcription start disrupts its expression; absolute scores ≥0.8 are treated as high-confidence.

**LoGoFunc**
: Research predictor of missense mechanism: gain of function, loss of function, or neutral. A hypothesis generator, not a pathogenicity call.

**FuncVEP**
: Research predictor of damaging functional effects for GRCh38 missense SNVs. GUIDE-IEI reports CTI, CTE, and SP only after exact allele-and-Ensembl-gene matching. Higher scores predict greater functional damage; they are not clinical pathogenicity classifications.

## Inheritance & family

**de novo** *(also: de-novo)*
: Present in the child but in neither parent — new in this generation. A strong clue in severe early-onset disease, but requires confident parental genotypes.

**heterozygous** *(also: homozygous, zygosity)*
: Whether one copy (heterozygous) or both copies (homozygous) of the position carry the variant.

**hemizygous**
: Only one copy exists at all — chiefly the X chromosome in males, where a single variant acts alone.

**compound heterozygous** *(also: compound-heterozygous, compound het)*
: Two different variants in the same gene, one on each copy — together disabling both copies, mimicking recessive disease.

**trans** *(also: cis)*
: Same copy (cis) or opposite copies (trans) of the gene. Compound heterozygosity requires trans; two variants in cis leave the other copy intact.

**phasing** *(also: phase set, phased)*
: Determining which parental copy carries each variant. Sequencers phase nearby variants directly (a “phase set”); otherwise parental genotypes settle it.

**mosaicism** *(also: mosaic)*
: A variant present in only a fraction of cells — e.g., a parent carrying a low-level variant in blood can transmit “de novo-looking” disease to a child.

## Regulatory

**cCRE** *(also: cCREs, cis-regulatory element, cis-regulatory elements)*
: Candidate cis-regulatory element: a region (promoter, enhancer, etc.) that ENCODE identified as likely to control gene activity. Overlap flags a variant as potentially regulatory — it does not name the target gene.

**promoter** *(also: promoters, enhancer, enhancers)*
: Regulatory regions: promoters sit at a gene's start and launch transcription; enhancers act from a distance, often on non-nearest genes.

## Sequencing quality

**read depth** *(also: DP)*
: How many sequencing reads covered the position. Low depth (≲10) makes calls unreliable.

**genotype quality** *(also: GQ)*
: The caller's confidence in the genotype, capped at 99; ≥20 is a common floor.

**allele balance** *(also: AB)*
: For heterozygotes, the fraction of reads showing the variant — expect ~0.5. Strong skew suggests an artifact or mosaicism.
