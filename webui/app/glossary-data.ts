// Single source of truth for genetics-term definitions shown across the UI.
// Written for clinicians and wet-lab scientists; each definition stands alone
// in <= ~45 words. Terms are matched in authored copy only (never in user
// data), first occurrence per text block, via segmentText below.

export type GlossaryEntry = {
  id: string;
  term: string;
  /** Case-insensitive additional spellings that resolve to this entry. */
  aliases?: string[];
  /** Case-sensitive spellings (short abbreviations like DP/GQ/AB). */
  exactAliases?: string[];
  category: string;
  definition: string;
  /** Future user-guide anchor (Phase 4); empty until the webpage exists. */
  learnMore?: string;
};

export const GLOSSARY: GlossaryEntry[] = [
  // ------------------------------------------------------------ Genome & data
  {
    id: "genome-build", term: "genome build", aliases: ["GRCh38", "GRCh37", "hg19"],
    category: "Genome & data",
    definition: "The reference version coordinates refer to. GRCh38 is current; hg19/GRCh37 is the older build still used by some labs. A variant's position differs between builds, so build mix-ups produce wrong results.",
  },
  {
    id: "vcf", term: "VCF",
    category: "Genome & data",
    definition: "Variant Call Format: the standard file listing an individual's differences from the reference genome, one variant per line, with genotype and quality information.",
  },
  {
    id: "wes-wgs", term: "exome", aliases: ["whole genome", "whole-genome", "WES", "WGS"],
    category: "Genome & data",
    definition: "Exome sequencing covers the ~2% of the genome that codes for protein; genome sequencing covers essentially everything, including regulatory regions.",
  },
  {
    id: "coding", term: "coding region", aliases: ["coding regions", "non-coding", "noncoding", "protein-coding"],
    category: "Genome & data",
    definition: "Coding regions are translated into protein. Non-coding regions include introns, regulatory elements, and everything between genes; variants there act mainly by altering splicing or gene regulation.",
  },
  {
    id: "transcript", term: "transcript", aliases: ["transcripts"],
    category: "Genome & data",
    definition: "One version of a gene's RNA. Most genes have several; a variant's predicted effect can differ between transcripts, which is why one is chosen for primary display.",
  },
  {
    id: "mane", term: "MANE Select", aliases: ["MANE Plus Clinical", "MANE"],
    category: "Genome & data",
    definition: "The single transcript per gene jointly agreed by NCBI and Ensembl as the clinical standard. Some genes also have a MANE Plus Clinical transcript — an additional isoform needed to interpret certain diseases — so one variant can legitimately carry two MANE-designated consequence rows; the one-row-per-variant display shows MANE Select and counts the rest in a +N chip.",
  },
  // ---------------------------------------------------------- Variant effects
  {
    id: "missense", term: "missense",
    category: "Variant effects",
    definition: "Changes one amino acid to another. Effect ranges from harmless to severe; the protein-damage predictors exist mainly to help judge these.",
  },
  {
    id: "synonymous", term: "synonymous",
    category: "Variant effects",
    definition: "Changes the DNA without changing the amino acid. Usually harmless, but can occasionally affect splicing.",
  },
  {
    id: "nonsense", term: "premature stop", aliases: ["nonsense", "stop-gained", "stop gained", "premature termination"],
    category: "Variant effects",
    definition: "Creates an early “stop” signal, truncating the protein.",
  },
  {
    id: "frameshift", term: "frameshift", aliases: ["frameshifts"],
    category: "Variant effects",
    definition: "An insertion or deletion that shifts the three-letter reading frame, garbling the protein from that point and usually creating a premature stop downstream.",
  },
  {
    id: "lof", term: "loss of function", aliases: ["loss-of-function", "pLoF", "LoF"],
    category: "Variant effects",
    definition: "A variant expected to abolish a gene copy's function — premature stops, frameshifts, and canonical splice-site changes. “Predicted LoF” is a claim needing confidence checks (see LOFTEE).",
  },
  {
    id: "splice-site", term: "splice site", aliases: ["splice sites", "splice-site", "canonical splice site", "splice region"],
    category: "Variant effects",
    definition: "The bases where introns are cut out of RNA. The two bases at each end (canonical site) are critical; nearby changes can also alter splicing (see SpliceAI).",
  },
  {
    id: "nmd", term: "nonsense-mediated decay", aliases: ["NMD"],
    category: "Variant effects",
    definition: "The cell's disposal system for RNAs with premature stops. If the damaged RNA is destroyed, little protein is made; variants that escape NMD may instead produce a truncated protein.",
  },
  {
    id: "impact", term: "impact",
    category: "Variant effects",
    definition: "VEP's coarse severity grouping: HIGH ≈ protein-truncating or splice-destroying; MODERATE ≈ protein-altering (mostly missense and in-frame changes); LOW/MODIFIER ≈ unlikely to change the protein.",
  },
  // ----------------------------------------------------- Population frequency
  {
    id: "allele-frequency", term: "allele frequency", aliases: ["allele frequencies", "population frequency"],
    category: "Population frequency",
    definition: "How common a variant is in a reference population. Variants common in healthy people are unlikely to cause severe early-onset disease.",
  },
  {
    id: "popmax", term: "popmax", aliases: ["gnomAD popmax"],
    category: "Population frequency",
    definition: "The variant's highest frequency in any single major population (e.g., East Asian, African). A variant rare worldwide may still be common in one population.",
  },
  {
    id: "gnomad", term: "gnomAD",
    category: "Population frequency",
    definition: "The largest public collection of population sequencing data (~800k individuals), the standard source for allele frequencies. A blank value here means “not seen by this annotation,” not proof the variant is unknown.",
  },
  // ------------------------------------------------------- Predictors & scores
  {
    id: "cadd", term: "CADD",
    category: "Predictors & scores",
    definition: "A general deleteriousness score. Scaled so ≥20 means the top 1% most deleterious of all possible variants, ≥30 the top 0.1%. Higher = more likely damaging.",
  },
  {
    id: "spliceai", term: "SpliceAI",
    category: "Predictors & scores",
    definition: "Predicts splicing disruption, 0–1. ≥0.5 is commonly treated as significant, ≥0.8 as high-confidence; works for variants well outside classic splice positions, including deep intronic variants.",
  },
  {
    id: "alphamissense", term: "AlphaMissense",
    category: "Predictors & scores",
    definition: "DeepMind's missense predictor; score 0–1 with calibrated classes (likely benign / ambiguous / likely pathogenic).",
  },
  {
    id: "loftee", term: "LOFTEE",
    category: "Predictors & scores",
    definition: "Classifies predicted loss-of-function consequences as high confidence (HC) or low confidence (LC) for a particular transcript and gives reasons. This is not functional evidence or a pathogenicity classification.",
  },
  {
    id: "conservation", term: "conservation", aliases: ["conservation scores", "GERP", "phyloP", "phastCons"],
    category: "Predictors & scores",
    definition: "How strongly evolution has preserved a position across species. High conservation implies change is poorly tolerated.",
  },
  {
    id: "constraint", term: "pLI", aliases: ["LOEUF", "constraint"],
    category: "Predictors & scores",
    definition: "Gene-level (not variant-level) intolerance to loss of function, from population data. pLI ≥ 0.9 or LOEUF < 0.6 ≈ genes where losing one copy matters — where dominant LoF disease is plausible.",
  },
  {
    id: "promoterai", term: "PromoterAI",
    category: "Predictors & scores",
    definition: "Predicts whether a variant near a gene's transcription start disrupts its expression; absolute scores ≥0.8 are treated as high-confidence.",
  },
  {
    id: "logofunc", term: "LoGoFunc",
    category: "Predictors & scores",
    definition: "Research predictor of missense mechanism: gain of function, loss of function, or neutral. A hypothesis generator, not a pathogenicity call.",
  },
  // ----------------------------------------------------- Inheritance & family
  {
    id: "de-novo", term: "de novo", aliases: ["de-novo"],
    category: "Inheritance & family",
    definition: "Present in the child but in neither parent — new in this generation. A strong clue in severe early-onset disease, but requires confident parental genotypes.",
  },
  {
    id: "zygosity", term: "heterozygous", aliases: ["homozygous", "zygosity"],
    category: "Inheritance & family",
    definition: "Whether one copy (heterozygous) or both copies (homozygous) of the position carry the variant.",
  },
  {
    id: "hemizygous", term: "hemizygous",
    category: "Inheritance & family",
    definition: "Only one copy exists at all — chiefly the X chromosome in males, where a single variant acts alone.",
  },
  {
    id: "compound-het", term: "compound heterozygous", aliases: ["compound-heterozygous", "compound het"],
    category: "Inheritance & family",
    definition: "Two different variants in the same gene, one on each copy — together disabling both copies, mimicking recessive disease.",
  },
  {
    id: "cis-trans", term: "trans", aliases: ["cis"],
    category: "Inheritance & family",
    definition: "Same copy (cis) or opposite copies (trans) of the gene. Compound heterozygosity requires trans; two variants in cis leave the other copy intact.",
  },
  {
    id: "phasing", term: "phasing", aliases: ["phase set", "phased"],
    category: "Inheritance & family",
    definition: "Determining which parental copy carries each variant. Sequencers phase nearby variants directly (a “phase set”); otherwise parental genotypes settle it.",
  },
  {
    id: "mosaicism", term: "mosaicism", aliases: ["mosaic"],
    category: "Inheritance & family",
    definition: "A variant present in only a fraction of cells — e.g., a parent carrying a low-level variant in blood can transmit “de novo-looking” disease to a child.",
  },
  // ----------------------------------------------------------------- Regulatory
  {
    id: "ccre", term: "cCRE", aliases: ["cCREs", "cis-regulatory element", "cis-regulatory elements"],
    category: "Regulatory",
    definition: "Candidate cis-regulatory element: a region (promoter, enhancer, etc.) that ENCODE identified as likely to control gene activity. Overlap flags a variant as potentially regulatory — it does not name the target gene.",
  },
  {
    id: "promoter-enhancer", term: "promoter", aliases: ["promoters", "enhancer", "enhancers"],
    category: "Regulatory",
    definition: "Regulatory regions: promoters sit at a gene's start and launch transcription; enhancers act from a distance, often on non-nearest genes.",
  },
  // -------------------------------------------------------- Sequencing quality
  {
    id: "read-depth", term: "read depth", exactAliases: ["DP"],
    category: "Sequencing quality",
    definition: "How many sequencing reads covered the position. Low depth (≲10) makes calls unreliable.",
  },
  {
    id: "genotype-quality", term: "genotype quality", exactAliases: ["GQ"],
    category: "Sequencing quality",
    definition: "The caller's confidence in the genotype, capped at 99; ≥20 is a common floor.",
  },
  {
    id: "allele-balance", term: "allele balance", exactAliases: ["AB"],
    category: "Sequencing quality",
    definition: "For heterozygotes, the fraction of reads showing the variant — expect ~0.5. Strong skew suggests an artifact or mosaicism.",
  },
];

export type TextSegment = { text: string; entry?: GlossaryEntry };

type Needle = { needle: string; entry: GlossaryEntry; exact: boolean };

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

let needleCache: Needle[] | null = null;
let patternCache: RegExp | null = null;

function needles(): Needle[] {
  if (needleCache) return needleCache;
  const list: Needle[] = [];
  for (const entry of GLOSSARY) {
    list.push({ needle: entry.term, entry, exact: false });
    for (const alias of entry.aliases ?? []) list.push({ needle: alias, entry, exact: false });
    for (const alias of entry.exactAliases ?? []) list.push({ needle: alias, entry, exact: true });
  }
  // Longest first so "MANE Select" wins over "MANE", "gnomAD popmax" over "gnomAD".
  list.sort((a, b) => b.needle.length - a.needle.length);
  needleCache = list;
  return list;
}

function pattern(): RegExp {
  if (patternCache) return patternCache;
  const alternation = needles().map((item) => escapeRegExp(item.needle)).join("|");
  patternCache = new RegExp(`\\b(?:${alternation})\\b`, "gi");
  return patternCache;
}

/**
 * Split authored copy into plain segments and glossary-term segments.
 * Longest match wins; each entry is marked at most once per text block; the
 * short uppercase abbreviations (DP/GQ/AB/VCF) match case-sensitively so
 * ordinary words never trigger them.
 */
export function segmentText(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  const seen = new Set<string>();
  const matcher = pattern();
  matcher.lastIndex = 0;
  let cursor = 0;
  for (let match = matcher.exec(text); match; match = matcher.exec(text)) {
    const found = match[0];
    const candidates = needles().filter(
      (item) => item.needle.length === found.length
        && (item.exact ? item.needle === found : item.needle.toLowerCase() === found.toLowerCase()),
    );
    const winner = candidates[0];
    if (!winner || seen.has(winner.entry.id)) continue;
    seen.add(winner.entry.id);
    if (match.index > cursor) segments.push({ text: text.slice(cursor, match.index) });
    segments.push({ text: found, entry: winner.entry });
    cursor = match.index + found.length;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor) });
  return segments.length ? segments : [{ text }];
}

export const GLOSSARY_CATEGORIES = [
  "Genome & data", "Variant effects", "Population frequency",
  "Predictors & scores", "Inheritance & family", "Regulatory",
  "Sequencing quality",
];
