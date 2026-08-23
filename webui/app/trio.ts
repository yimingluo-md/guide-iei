import type { GenotypeEvidence, VariantRow } from "./vcf";

export type PedigreeMember = {
  familyId: string;
  sampleId: string;
  fatherId: string;
  motherId: string;
  sex: "male" | "female" | "unknown";
  affected: "affected" | "unaffected" | "unknown";
};

export type TrioDefinition = {
  familyId: string;
  proband: string;
  mother: string;
  father: string;
  probandSex: PedigreeMember["sex"];
  parentalRelationshipsConfirmed: boolean;
};

export type TrioThresholds = {
  childMinDp: number;
  parentMinDp: number;
  minGq: number;
  childAbMin: number;
  childAbMax: number;
  parentAbMax: number;
};

export const DEFAULT_TRIO_THRESHOLDS: TrioThresholds = {
  childMinDp: 10,
  parentMinDp: 10,
  minGq: 20,
  childAbMin: 0.2,
  childAbMax: 0.8,
  parentAbMax: 0.02,
};

export type DeNovoStatus =
  | "high_confidence"
  | "possible"
  | "possible_parental_mosaicism"
  | "likely_artifact"
  | "mendelian_conflict"
  | "inherited"
  | "not_proband";

export type DeNovoAssessment = {
  status: DeNovoStatus;
  reasons: string[];
  child: GenotypeEvidence | null;
  mother: GenotypeEvidence | null;
  father: GenotypeEvidence | null;
};

export type CompoundOrigin = "maternal" | "paternal" | "both" | "de_novo" | "unknown";
export type CompoundPhase =
  | "confirmed_trans_inheritance"
  | "confirmed_trans_phasing"
  | "possible_trans"
  | "phase_unknown"
  | "cis"
  | "excluded_hemizygous";

export type CompoundHetPair = {
  key: string;
  gene: string;
  first: VariantRow;
  second: VariantRow;
  firstOrigin: CompoundOrigin;
  secondOrigin: CompoundOrigin;
  phase: CompoundPhase;
  reason: string;
};

function normalizedParent(value: string) {
  const trimmed = value.trim();
  return trimmed === "0" || trimmed === "." || trimmed === "-9" ? "" : trimmed;
}

export function parsePedigree(
  text: string,
  availableSamples: Set<string> = new Set(),
): { members: PedigreeMember[]; trios: TrioDefinition[]; warnings: string[] } {
  const members: PedigreeMember[] = [];
  const warnings: string[] = [];
  const seen = new Set<string>();

  text.split(/\r?\n/).forEach((rawLine, index) => {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) return;
    const fields = line.split(/\s+/);
    if (fields.length < 6) {
      warnings.push(`Line ${index + 1}: expected at least 6 PED columns.`);
      return;
    }
    const [familyId, sampleId, fatherRaw, motherRaw, sexRaw, affectedRaw] = fields;
    const memberKey = `${familyId}:${sampleId}`;
    if (seen.has(memberKey)) {
      warnings.push(`Duplicate PED member ${sampleId} in family ${familyId}.`);
      return;
    }
    seen.add(memberKey);
    members.push({
      familyId,
      sampleId,
      fatherId: normalizedParent(fatherRaw),
      motherId: normalizedParent(motherRaw),
      sex: sexRaw === "1" ? "male" : sexRaw === "2" ? "female" : "unknown",
      affected: affectedRaw === "2" ? "affected" : affectedRaw === "1" ? "unaffected" : "unknown",
    });
  });

  // Only affected members are offered as probands: an unaffected sibling
  // included for segregation must not silently become a second "proband"
  // whose healthy variants get labelled de novo candidates. When the PED
  // marks nobody affected, fall back to every child with two parents and
  // say so.
  const childCandidates = members.filter((member) => member.fatherId && member.motherId);
  const affectedCandidates = childCandidates.filter(
    (member) => member.affected === "affected",
  );
  const probandCandidates = affectedCandidates.length ? affectedCandidates : childCandidates;
  if (!affectedCandidates.length && childCandidates.length) {
    warnings.push(
      "No affected member has two parents listed; every child in the PED is offered as a proband.",
    );
  } else if (affectedCandidates.length < childCandidates.length) {
    const excluded = childCandidates
      .filter((member) => member.affected !== "affected")
      .map((member) => member.sampleId);
    warnings.push(
      `Unaffected/unknown-status member(s) not offered as probands: ${excluded.join(", ")}.`,
    );
  }

  // Parents named in the PED must exist somewhere — as loaded VCF samples
  // when those are known, otherwise at least as PED members. A trio built
  // around parents that exist nowhere would silently analyse nothing.
  const memberIds = new Set(members.map((member) => member.sampleId));
  const knownSamples = availableSamples.size > 0 ? availableSamples : memberIds;
  const trios = probandCandidates
    .map((member) => ({
      familyId: member.familyId,
      proband: member.sampleId,
      mother: member.motherId,
      father: member.fatherId,
      probandSex: member.sex,
      parentalRelationshipsConfirmed: false,
    } satisfies TrioDefinition))
    .filter((trio) => {
      const missing = [trio.proband, trio.mother, trio.father]
        .filter((sample) => !knownSamples.has(sample));
      if (missing.length) {
        const pool = availableSamples.size > 0 ? "VCF sample(s)" : "PED member(s)";
        warnings.push(`Family ${trio.familyId}: ${pool} not found: ${missing.join(", ")}.`);
        return false;
      }
      return true;
    });

  if (!trios.length && members.length) warnings.push("No complete mother-father-child trio matched the loaded VCF samples.");
  return { members, trios, warnings };
}

function alleles(evidence: GenotypeEvidence | null) {
  return evidence?.gt.split(/[|/]/).filter((value) => /^\d+$/.test(value)).map(Number) ?? [];
}

// GRCh38 pseudoautosomal region bounds on chrX (PAR1 ends at 2,781,479;
// PAR2 starts at 155,701,383). Inside the PARs a male is diploid.
const GRCH38_PAR1_END = 2_781_479;
const GRCH38_PAR2_START = 155_701_383;

function normalizedTrioContig(chrom: string) {
  const trimmed = chrom.replace(/^chr/i, "").toUpperCase();
  return trimmed === "M" ? "MT" : trimmed;
}

function isNonParX(row: VariantRow) {
  return normalizedTrioContig(row.chrom) === "X"
    && row.pos > GRCH38_PAR1_END
    && row.pos < GRCH38_PAR2_START;
}

/**
 * Sites with a single informative transmitting parent. A male proband is
 * hemizygous for non-PAR X (transmitting parent: mother) and for Y
 * (transmitting parent: father); mitochondrial variants are maternally
 * transmitted for probands of any sex. Applying the autosomal diploid
 * model there misclassifies the most common IEI presentation: an X-linked
 * de novo written as 1/1 hits the "hom-alt child" conflict branch, and a
 * true haploid 1 fails the heterozygous allele-balance gate at AB ~1.0.
 */
function uniparentalContextFor(
  row: VariantRow,
  trio: TrioDefinition,
): "x" | "y" | "mt" | null {
  const contig = normalizedTrioContig(row.chrom);
  if (contig === "MT") return "mt";
  if (trio.probandSex !== "male") return null;
  if (contig === "Y") return "y";
  if (isNonParX(row)) return "x";
  return null;
}

function isHomRef(evidence: GenotypeEvidence | null) {
  const values = alleles(evidence);
  return Boolean(evidence?.called && values.length > 0 && values.every((value) => value === 0));
}

function isHeterozygousCarrier(evidence: GenotypeEvidence | null) {
  const values = alleles(evidence);
  return Boolean(evidence?.carrier && values.length === 2 && values[0] !== values[1]);
}

function adequate(
  evidence: GenotypeEvidence | null,
  minDp: number,
  minGq: number,
) {
  return Boolean(
    evidence?.called
    && evidence.dp !== null && evidence.dp >= minDp
    && evidence.gq !== null && evidence.gq >= minGq,
  );
}

export function assessDeNovo(
  row: VariantRow,
  trio: TrioDefinition,
  thresholds: TrioThresholds = DEFAULT_TRIO_THRESHOLDS,
): DeNovoAssessment {
  const evidence = row.sampleGenotypes ?? {};
  const child = evidence[trio.proband] ?? (row.sample === trio.proband ? {
    gt: row.genotype,
    called: row.genotype !== "./." && row.genotype !== ".",
    carrier: true,
    dp: row.dp,
    gq: row.gq,
    adRef: row.adRef ?? null,
    adAlt: row.adAlt ?? null,
    alleleBalance: row.alleleBalance,
    pl: row.pl ?? null,
    phased: row.phase === "phased",
    phaseSet: row.phaseSet ?? "",
    phaseHaplotype: row.phaseHaplotype ?? null,
  } : null);
  const mother = evidence[trio.mother] ?? null;
  const father = evidence[trio.father] ?? null;
  const base = { child, mother, father };

  if (row.sample !== trio.proband) {
    return { ...base, status: "not_proband", reasons: ["Variant row is not for the selected proband."] };
  }
  if (!child?.carrier) {
    return { ...base, status: "not_proband", reasons: ["The proband does not carry this ALT allele."] };
  }

  // In a hemizygous context only the transmitting parent's genotype is
  // informative: a male proband's X comes from the mother, his Y from the
  // father. The other parent's carrier state neither establishes
  // inheritance nor gates the de novo call.
  const hemiContext = uniparentalContextFor(row, trio);
  const relevantParents: Array<["mother" | "father", GenotypeEvidence | null]> =
    hemiContext === "x" || hemiContext === "mt"
      ? [["mother", mother]]
      : hemiContext === "y"
        ? [["father", father]]
        : [["mother", mother], ["father", father]];

  const carrierParents = relevantParents
    .filter(([, evidence]) => evidence?.carrier);
  if (carrierParents.length) {
    // "Inherited" is a positive claim about the parental call, so the call
    // must meet the parental quality thresholds; a DP=1 flicker of ALT
    // evidence downgrades the candidate to "possible" instead of quietly
    // suppressing it as inherited.
    const supported = carrierParents
      .filter(([, evidence]) => adequate(evidence, thresholds.parentMinDp, thresholds.minGq))
      .map(([label]) => label);
    if (supported.length) {
      return { ...base, status: "inherited", reasons: [`ALT allele is present in the ${supported.join(" and ")}.`] };
    }
    const labels = carrierParents.map(([label]) => label);
    return {
      ...base,
      status: "possible",
      reasons: [`ALT evidence in the ${labels.join(" and ")} is below the parental quality thresholds; inheritance cannot be established from this callset.`],
    };
  }
  if (
    !hemiContext
    && alleles(child).length === 2
    && alleles(child).every((value) => value > 0)
  ) {
    // Autosomes only: a hemizygous male X/Y call is routinely written as
    // 1/1 by diploid-model callers and is the expected de novo shape, not
    // a Mendelian conflict.
    return {
      ...base,
      status: "mendelian_conflict",
      reasons: ["A homozygous alternate child with two homozygous-reference parents is not a simple de novo model."],
    };
  }
  if (!adequate(child, thresholds.childMinDp, thresholds.minGq)) {
    return { ...base, status: "likely_artifact", reasons: ["Proband DP or GQ is below the configured threshold."] };
  }
  // A hemizygous call is expected near allele balance 1.0, so only the
  // lower bound applies; the diploid upper bound would misfile every true
  // hemizygous call as an artifact.
  if (
    child.alleleBalance === null
    || child.alleleBalance < thresholds.childAbMin
    || (!hemiContext && child.alleleBalance > thresholds.childAbMax)
  ) {
    return { ...base, status: "likely_artifact", reasons: ["Proband allele balance is missing or outside the configured range."] };
  }

  const missingParents = relevantParents
    .filter(([, evidence]) => !evidence?.called)
    .map(([label]) => label);
  if (missingParents.length) {
    return { ...base, status: "possible", reasons: [`No callable genotype for ${missingParents.join(" and ")} at this site.`] };
  }
  if (relevantParents.some(([, evidence]) => !isHomRef(evidence))) {
    return { ...base, status: "mendelian_conflict", reasons: ["Parental genotypes are not compatible with a simple de novo model."] };
  }

  const mosaicParents = relevantParents
    .filter(([, evidence]) => evidence?.alleleBalance !== null
      && evidence?.alleleBalance !== undefined
      && evidence.alleleBalance > thresholds.parentAbMax)
    .map(([label]) => label);
  if (mosaicParents.length) {
    return {
      ...base,
      status: "possible_parental_mosaicism",
      reasons: [`Low-level ALT evidence exceeds the configured limit in the ${mosaicParents.join(" and ")}.`],
    };
  }

  const lowQualityParents = relevantParents
    .filter(([, evidence]) => !adequate(evidence, thresholds.parentMinDp, thresholds.minGq))
    .map(([label]) => label);
  const missingAlleleDepth = relevantParents
    .filter(([, evidence]) => evidence?.adAlt === null)
    .map(([label]) => label);
  if (lowQualityParents.length || missingAlleleDepth.length) {
    const reasons = [];
    if (lowQualityParents.length) reasons.push(`Parental DP or GQ is insufficient for the ${lowQualityParents.join(" and ")}.`);
    if (missingAlleleDepth.length) reasons.push(`Allele depths are unavailable for the ${missingAlleleDepth.join(" and ")}.`);
    return { ...base, status: "possible", reasons };
  }

  return {
    ...base,
    status: "high_confidence",
    reasons: [
      hemiContext === "mt"
        ? "Proband mitochondrial ALT with a well-supported reference call in the mother, the sole transmitting parent, in the loaded callset."
        : hemiContext
          ? "Proband hemizygous ALT with a well-supported homozygous-reference transmitting parent in the loaded callset."
          : "Proband ALT and two well-supported homozygous-reference parental genotypes are present in the loaded callset.",
    ],
  };
}

function variantIdentity(row: VariantRow) {
  return `${row.chrom}:${row.pos}:${row.ref}:${row.alt}`;
}

function originFor(
  row: VariantRow,
  trio: TrioDefinition,
  thresholds: TrioThresholds = DEFAULT_TRIO_THRESHOLDS,
): CompoundOrigin {
  const evidence = row.sampleGenotypes ?? {};
  const mother = evidence[trio.mother] ?? null;
  const father = evidence[trio.father] ?? null;
  // A parental origin feeds "confirmed trans by inheritance", so both
  // parental genotypes backing it must meet the CONFIGURED quality
  // thresholds — opposing DP=1/GQ=1 calls must not mint a confirmed
  // compound het, and a reviewer who tightened the panel's DP/GQ values
  // must see comphet origins judged by the same rule.
  const solid = (parent: GenotypeEvidence | null) =>
    adequate(parent, thresholds.parentMinDp, thresholds.minGq);
  if (mother?.carrier && father?.carrier) return "both";
  if (mother?.carrier && isHomRef(father)) {
    return solid(mother) && solid(father) ? "maternal" : "unknown";
  }
  if (father?.carrier && isHomRef(mother)) {
    return solid(father) && solid(mother) ? "paternal" : "unknown";
  }
  const deNovo = assessDeNovo(row, trio, thresholds);
  // "possible" covers the missing-parental-genotype case: converting "we do
  // not know the parental genotype" into "this arose de novo" promoted
  // unphaseable pairs to possible_trans. Only a high-confidence call counts.
  if (deNovo.status === "high_confidence") return "de_novo";
  return "unknown";
}

function representativeRows(rows: VariantRow[], trio: TrioDefinition) {
  const unique = new Map<string, VariantRow>();
  rows
    .filter((row) => row.sample === trio.proband)
    .filter((row) => isHeterozygousCarrier(row.sampleGenotypes?.[trio.proband] ?? null)
      || (!row.sampleGenotypes && row.genotype.split(/[|/]/).length === 2 && row.genotype.split(/[|/]/)[0] !== row.genotype.split(/[|/]/)[1]))
    .forEach((row) => {
      const key = `${row.gene}:${variantIdentity(row)}`;
      const previous = unique.get(key);
      if (!previous || (row.mane && !previous.mane) || (row.picked && !previous.mane && !previous.picked)) {
        unique.set(key, row);
      }
    });
  return [...unique.values()];
}

export function compoundHetPairs(
  rows: VariantRow[],
  trio: TrioDefinition,
  thresholds: TrioThresholds = DEFAULT_TRIO_THRESHOLDS,
): CompoundHetPair[] {
  const groups = new Map<string, VariantRow[]>();
  representativeRows(rows, trio).forEach((row) => {
    groups.set(row.gene, [...(groups.get(row.gene) ?? []), row]);
  });
  const pairs: CompoundHetPair[] = [];

  groups.forEach((variants, gene) => {
    for (let firstIndex = 0; firstIndex < variants.length; firstIndex += 1) {
      for (let secondIndex = firstIndex + 1; secondIndex < variants.length; secondIndex += 1) {
        const first = variants[firstIndex];
        const second = variants[secondIndex];
        const firstOrigin = originFor(first, trio, thresholds);
        const secondOrigin = originFor(second, trio, thresholds);
        let phase: CompoundPhase = "phase_unknown";
        let reason = "Both variants qualify, but inheritance and phase do not establish trans configuration.";
        if (trio.probandSex === "male" && (isNonParX(first) || isNonParX(second))) {
          // One X haplotype: two alternate alleles cannot be in trans, and a
          // heterozygous-appearing call here points to a genotyping artifact
          // or an overlapping CNV rather than a compound heterozygote.
          pairs.push({
            key: `${trio.familyId}:${trio.proband}:${gene}:${variantIdentity(first)}:${variantIdentity(second)}`,
            gene, first, second, firstOrigin, secondOrigin,
            phase: "excluded_hemizygous",
            reason: "A male proband is hemizygous for non-pseudoautosomal X: a compound heterozygote is not possible, and heterozygous-appearing calls here suggest artifact or an overlapping CNV.",
          });
          continue;
        }
        const firstChild = first.sampleGenotypes?.[trio.proband];
        const secondChild = second.sampleGenotypes?.[trio.proband];

        if (
          firstChild?.phased && secondChild?.phased
          && firstChild.phaseSet && firstChild.phaseSet === secondChild.phaseSet
          && firstChild.phaseHaplotype !== null && secondChild.phaseHaplotype !== null
        ) {
          if (firstChild.phaseHaplotype !== secondChild.phaseHaplotype) {
            phase = "confirmed_trans_phasing";
            reason = `Opposite haplotypes in phase set ${firstChild.phaseSet}.`;
          } else {
            phase = "cis";
            reason = `Same haplotype in phase set ${firstChild.phaseSet}.`;
          }
        } else if (
          (firstOrigin === "maternal" && secondOrigin === "paternal")
          || (firstOrigin === "paternal" && secondOrigin === "maternal")
        ) {
          phase = "confirmed_trans_inheritance";
          reason = "One qualifying variant is maternally inherited and the other is paternally inherited.";
        } else if (
          (firstOrigin === "maternal" && secondOrigin === "maternal")
          || (firstOrigin === "paternal" && secondOrigin === "paternal")
        ) {
          phase = "cis";
          reason = `Both qualifying variants were transmitted by the same parent (${firstOrigin}).`;
        } else if (
          (firstOrigin === "de_novo" && ["maternal", "paternal"].includes(secondOrigin))
          || (secondOrigin === "de_novo" && ["maternal", "paternal"].includes(firstOrigin))
        ) {
          phase = "possible_trans";
          reason = "One variant is inherited and the other is a de novo candidate; physical phase remains unconfirmed.";
        }

        pairs.push({
          key: `${trio.familyId}:${trio.proband}:${gene}:${variantIdentity(first)}:${variantIdentity(second)}`,
          gene, first, second, firstOrigin, secondOrigin, phase, reason,
        });
      }
    }
  });
  return pairs;
}
