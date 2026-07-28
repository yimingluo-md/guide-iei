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
  | "cis";

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

  const trios = members
    .filter((member) => member.fatherId && member.motherId)
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
        .filter((sample) => availableSamples.size > 0 && !availableSamples.has(sample));
      if (missing.length) {
        warnings.push(`Family ${trio.familyId}: VCF sample(s) not found: ${missing.join(", ")}.`);
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
  if (mother?.carrier || father?.carrier) {
    const carriers = [
      mother?.carrier ? "mother" : "",
      father?.carrier ? "father" : "",
    ].filter(Boolean);
    return { ...base, status: "inherited", reasons: [`ALT allele is present in the ${carriers.join(" and ")}.`] };
  }
  if (alleles(child).length === 2 && alleles(child).every((value) => value > 0)) {
    return {
      ...base,
      status: "mendelian_conflict",
      reasons: ["A homozygous alternate child with two homozygous-reference parents is not a simple de novo model."],
    };
  }
  if (!adequate(child, thresholds.childMinDp, thresholds.minGq)) {
    return { ...base, status: "likely_artifact", reasons: ["Proband DP or GQ is below the configured threshold."] };
  }
  if (
    child.alleleBalance === null
    || child.alleleBalance < thresholds.childAbMin
    || child.alleleBalance > thresholds.childAbMax
  ) {
    return { ...base, status: "likely_artifact", reasons: ["Proband allele balance is missing or outside the configured range."] };
  }

  const missingParents = [
    !mother?.called ? "mother" : "",
    !father?.called ? "father" : "",
  ].filter(Boolean);
  if (missingParents.length) {
    return { ...base, status: "possible", reasons: [`No callable genotype for ${missingParents.join(" and ")} at this site.`] };
  }
  if (!isHomRef(mother) || !isHomRef(father)) {
    return { ...base, status: "mendelian_conflict", reasons: ["Parental genotypes are not compatible with a simple de novo model."] };
  }

  const mosaicParents = [
    mother?.alleleBalance !== null && mother?.alleleBalance !== undefined
      && mother.alleleBalance > thresholds.parentAbMax ? "mother" : "",
    father?.alleleBalance !== null && father?.alleleBalance !== undefined
      && father.alleleBalance > thresholds.parentAbMax ? "father" : "",
  ].filter(Boolean);
  if (mosaicParents.length) {
    return {
      ...base,
      status: "possible_parental_mosaicism",
      reasons: [`Low-level ALT evidence exceeds the configured limit in the ${mosaicParents.join(" and ")}.`],
    };
  }

  const lowQualityParents = [
    !adequate(mother, thresholds.parentMinDp, thresholds.minGq) ? "mother" : "",
    !adequate(father, thresholds.parentMinDp, thresholds.minGq) ? "father" : "",
  ].filter(Boolean);
  const missingAlleleDepth = [
    mother?.adAlt === null ? "mother" : "",
    father?.adAlt === null ? "father" : "",
  ].filter(Boolean);
  if (lowQualityParents.length || missingAlleleDepth.length) {
    const reasons = [];
    if (lowQualityParents.length) reasons.push(`Parental DP or GQ is insufficient for the ${lowQualityParents.join(" and ")}.`);
    if (missingAlleleDepth.length) reasons.push(`Allele depths are unavailable for the ${missingAlleleDepth.join(" and ")}.`);
    return { ...base, status: "possible", reasons };
  }

  return {
    ...base,
    status: "high_confidence",
    reasons: ["Proband ALT and two well-supported homozygous-reference parental genotypes are present in the loaded callset."],
  };
}

function variantIdentity(row: VariantRow) {
  return `${row.chrom}:${row.pos}:${row.ref}:${row.alt}`;
}

function originFor(row: VariantRow, trio: TrioDefinition): CompoundOrigin {
  const evidence = row.sampleGenotypes ?? {};
  const mother = evidence[trio.mother] ?? null;
  const father = evidence[trio.father] ?? null;
  if (mother?.carrier && father?.carrier) return "both";
  if (mother?.carrier && isHomRef(father)) return "maternal";
  if (father?.carrier && isHomRef(mother)) return "paternal";
  const deNovo = assessDeNovo(row, trio);
  if (deNovo.status === "high_confidence" || deNovo.status === "possible") return "de_novo";
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

export function compoundHetPairs(rows: VariantRow[], trio: TrioDefinition): CompoundHetPair[] {
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
        const firstOrigin = originFor(first, trio);
        const secondOrigin = originFor(second, trio);
        let phase: CompoundPhase = "phase_unknown";
        let reason = "Both variants qualify, but inheritance and phase do not establish trans configuration.";
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
