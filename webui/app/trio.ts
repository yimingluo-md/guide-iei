import { isHeterozygousGenotype, transmissionContext, normalizedContig, normalizedVariantKey, type GenotypeEvidence, type VariantRow } from "./vcf";

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
  | "unassessed"
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
  | "excluded_hemizygous"
  | "excluded_ploidy";

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
      if (new Set([trio.proband, trio.mother, trio.father]).size !== 3) {
        warnings.push(`Family ${trio.familyId}: the child, mother, and father must be different samples.`);
        return false;
      }
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

function compatiblePloidy(gt: string, context: ReturnType<typeof transmissionContext>) {
  const tokens = gt.split(/[|/]/);
  if (!tokens.every(value => /^\d+$/.test(value))) return false;
  if (context === "diploid") return tokens.length === 2;
  if (context === "uncertain") return false;
  return tokens.length === 1 || (tokens.length === 2
    && (context === "mitochondrial" || tokens[0] === tokens[1]));
}

function compatibleParent(row: VariantRow, gt: string, parent: "mother" | "father") {
  // A Y-PAR record represents a shared X/Y locus for both parents.
  const context = normalizedContig(row.chrom) === "Y" && transmissionContext(row, "male") === "diploid"
    ? "diploid" : transmissionContext(row, parent === "mother" ? "female" : "male");
  return compatiblePloidy(gt, context);
}

export function assessDeNovo(
  row: VariantRow,
  trio: TrioDefinition,
  thresholds: TrioThresholds = DEFAULT_TRIO_THRESHOLDS,
): DeNovoAssessment {
  const evidence = row.sampleGenotypes ?? {};
  const child = evidence[trio.proband] ?? (row.sample === trio.proband ? {
    gt: row.genotype,
    called: row.genotype.split(/[|/]/).every(value => /^\d+$/.test(value)),
    carrier: row.genotype.split(/[|/]/).some(value => Number(value) > 0),
    genotypeClass: row.genotypeClass ?? "other",
    genotypeFilter: row.genotypeFilter ?? "",
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
  const context = transmissionContext(row, trio.probandSex);
  const tokens = child.gt.split(/[|/]/);
  if (context === "uncertain") {
    return { ...base, status: "unassessed", reasons: ["The recorded proband sex does not establish the inheritance model at this sex-chromosome locus."] };
  }
  if (!child.called || !compatiblePloidy(child.gt, context)) {
    return { ...base, status: "unassessed", reasons: [`Proband GT ${child.gt} is incomplete or inconsistent with the expected ploidy at this locus.`] };
  }
  const relevantParents: Array<["mother" | "father", GenotypeEvidence | null]> =
    context === "maternal" || context === "mitochondrial" ? [["mother", mother]]
      : context === "paternal" ? [["father", father]] : [["mother", mother], ["father", father]];

  for (const [parent, call] of relevantParents) {
    if (call?.called && !compatibleParent(row, call.gt, parent)) {
      return { ...base, status: "unassessed", reasons: [`The ${parent}'s GT ${call.gt} is inconsistent with the expected ploidy at this locus.`] };
    }
  }
  if (context === "diploid" && tokens[0] === tokens[1]) {
    if (!relevantParents.every(([, parent]) => adequate(parent, thresholds.parentMinDp, thresholds.minGq))) {
      return { ...base, status: "possible", reasons: ["Parental calls are insufficient to assess the homozygous ALT genotype."] };
    }
    if (relevantParents.some(([, parent]) => !parent?.carrier)) {
      return { ...base, status: "mendelian_conflict", reasons: ["A homozygous ALT child requires a copy from each parent under the diploid inheritance model."] };
    }
  }
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
  if (!adequate(child, thresholds.childMinDp, thresholds.minGq)) {
    return { ...base, status: "likely_artifact", reasons: ["Proband DP or GQ is below the configured threshold."] };
  }
  // A hemizygous call is expected near allele balance 1.0, so only the
  // lower bound applies; the diploid upper bound would misfile every true
  // hemizygous call as an artifact.
  if (
    child.alleleBalance === null
    || child.alleleBalance < thresholds.childAbMin
    || (context === "diploid" && child.alleleBalance > thresholds.childAbMax)
  ) {
    return { ...base, status: "likely_artifact", reasons: ["Proband allele balance is missing or outside the configured range."] };
  }

  const missingParents = relevantParents
    .filter(([, evidence]) => !evidence?.called)
    .map(([label]) => label);
  if (missingParents.length) {
    return { ...base, status: "possible", reasons: [`No callable genotype for ${missingParents.join(" and ")} at this site.`] };
  }
  // The remaining parents have called genotypes lacking this row's ALT.
  // They may carry another ALT at the same site.
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
    .filter(([, evidence]) => evidence?.adAlt == null
      || evidence.adRef == null || evidence.alleleBalance == null)
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
      context === "mitochondrial"
        ? "The proband carries this mitochondrial ALT. The mother's well-supported genotype lacks it."
        : context !== "diploid"
          ? "The proband carries this hemizygous ALT. The transmitting parent's well-supported genotype lacks it."
          : "The proband carries this ALT. Both parents have well-supported genotypes that lack it.",
    ],
  };
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
  if (mother && father
    && adequate(mother, thresholds.parentMinDp, thresholds.minGq)
    && adequate(father, thresholds.parentMinDp, thresholds.minGq)
    && compatibleParent(row, mother.gt, "mother") && compatibleParent(row, father.gt, "father")) {
    if (mother.carrier && father.carrier) return "both";
    if (mother?.carrier) return "maternal";
    if (father?.carrier) return "paternal";
  }
  const deNovo = assessDeNovo(row, trio, thresholds);
  // "possible" covers the missing-parental-genotype case: converting "we do
  // not know the parental genotype" into "this arose de novo" promoted
  // unphaseable pairs to possible_trans. Only a high-confidence call counts.
  if (deNovo.status === "high_confidence") return "de_novo";
  return "unknown";
}

export function sampleGeneAlleleKey(row: VariantRow) {
  return JSON.stringify([row.sample, row.gene, normalizedVariantKey(row)]);
}

function representativeRows(rows: VariantRow[], trio: TrioDefinition) {
  const unique = new Map<string, VariantRow>();
  rows
    .filter((row) => row.sample === trio.proband)
    .filter((row) => isHeterozygousGenotype(row.genotype))
    .forEach((row) => {
      const key = sampleGeneAlleleKey(row);
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
    if (!row.gene || ["—", ".", "-"].includes(row.gene)) return;
    groups.set(row.gene, [...(groups.get(row.gene) ?? []), row]);
  });
  const pairs: CompoundHetPair[] = [];

  groups.forEach((variants, gene) => {
    for (let firstIndex = 0; firstIndex < variants.length; firstIndex += 1) {
      for (let secondIndex = firstIndex + 1; secondIndex < variants.length; secondIndex += 1) {
        const first = variants[firstIndex];
        const second = variants[secondIndex];
        if (normalizedContig(first.chrom) !== normalizedContig(second.chrom)) continue;
        const firstContext = transmissionContext(first, trio.probandSex);
        const secondContext = transmissionContext(second, trio.probandSex);
        const diploid = firstContext === "diploid" && secondContext === "diploid";
        const firstOrigin = diploid ? originFor(first, trio, thresholds) : "unknown";
        const secondOrigin = diploid ? originFor(second, trio, thresholds) : "unknown";
        let phase: CompoundPhase = "phase_unknown";
        let reason = "Both variants qualify, but inheritance and phase do not establish trans configuration.";
        if (!diploid) {
          const uncertain = firstContext === "uncertain" || secondContext === "uncertain";
          const mt = firstContext === "mitochondrial";
          pairs.push({
            key: `${trio.familyId}:${trio.proband}:${gene}:${normalizedVariantKey(first)}:${normalizedVariantKey(second)}`,
            gene, first, second, firstOrigin, secondOrigin,
            phase: uncertain || mt ? "excluded_ploidy" : "excluded_hemizygous",
            reason: uncertain ? "The recorded proband sex does not establish diploidy at both loci."
              : mt ? "Mitochondrial genotypes do not establish a diploid compound heterozygote."
              : "At least one locus is hemizygous in the proband.",
          });
          continue;
        }
        const firstChild = first.sampleGenotypes?.[trio.proband];
        const secondChild = second.sampleGenotypes?.[trio.proband];

        if (
          firstChild?.phased && secondChild?.phased
          && first.source === second.source
          && firstChild.phaseSet && firstChild.phaseSet !== "."
          && firstChild.phaseSet === secondChild.phaseSet
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
          key: `${trio.familyId}:${trio.proband}:${gene}:${normalizedVariantKey(first)}:${normalizedVariantKey(second)}`,
          gene, first, second, firstOrigin, secondOrigin, phase, reason,
        });
      }
    }
  });
  return pairs;
}

export function isCandidatePair(pair: CompoundHetPair) {
  return pair.phase !== "cis" && pair.phase !== "excluded_hemizygous" && pair.phase !== "excluded_ploidy";
}

export function trioCompoundRowKeys(rows: VariantRow[], candidatePairs: CompoundHetPair[]) {
  // All passing transcripts of the participating record qualify. A duplicate
  // record may carry a different GT or phase for the very same sample and ALT.
  const recordKey = (row: VariantRow) => JSON.stringify([
    row.source, row.sample, row.gene, row.chrom, row.pos, row.ref, row.alt, row.sourceRecordOrdinal,
  ]);
  const records = new Set(candidatePairs.flatMap(pair => [recordKey(pair.first), recordKey(pair.second)]));
  return new Set(rows.filter(row => isHeterozygousGenotype(row.genotype) && records.has(recordKey(row))).map(row => row.key));
}
