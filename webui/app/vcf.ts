export type Impact = "HIGH" | "MODERATE" | "LOW" | "MODIFIER" | "UNKNOWN";

export type GenotypeEvidence = {
  gt: string;
  called: boolean;
  carrier: boolean;
  dp: number | null;
  gq: number | null;
  adRef: number | null;
  adAlt: number | null;
  alleleBalance: number | null;
  pl: number[] | null;
  phased: boolean;
  phaseSet: string;
  phaseHaplotype: 0 | 1 | null;
  genotypeClass: "heterozygous" | "homozygous_alt" | "hemizygous" | "other";
  partialCall?: boolean;
  genotypeFilter: string;
  rawFields?: Record<string, string>;
};

export type RawVcfEvidence = {
  info: Record<string, string>;
  consequence: Record<string, string>;
  format: Record<string, string>;
};

export type ClinGenErepoCompact = {
  uuid: string;
  caid: string;
  assertion: string;
  disease: string;
  mondoId: string;
  modeOfInheritance: string;
  expertPanel: string;
  approvalDate: string;
};

export type VariantQcSettings = {
  minDp: number | null;
  minGq: number | null;
  minAltDepth: number | null;
  minHetRefDepth: number | null;
  hetAbMin: number | null;
  hetAbMax: number | null;
  homAltAbMin: number | null;
  maxDp: number | null;
  genotypeFtMode: "ignore" | "exclude_explicit" | "require_pass";
  minQual: number | null;
  minQd: number | null;
  minMq: number | null;
  maxFs: number | null;
  maxSor: number | null;
  minMqRankSum: number | null;
  minReadPosRankSum: number | null;
  minBaseQRankSum: number | null;
};

export const STANDARD_VARIANT_QC: VariantQcSettings = {
  minDp: 10,
  minGq: 20,
  minAltDepth: 3,
  minHetRefDepth: null,
  hetAbMin: 0.2,
  hetAbMax: 0.8,
  homAltAbMin: 0.8,
  maxDp: null,
  genotypeFtMode: "exclude_explicit",
  minQual: null,
  minQd: null,
  minMq: null,
  maxFs: null,
  maxSor: null,
  minMqRankSum: null,
  minReadPosRankSum: null,
  minBaseQRankSum: null,
};

export const NO_VARIANT_QC: VariantQcSettings = {
  ...STANDARD_VARIANT_QC,
  minDp: null,
  minGq: null,
  minAltDepth: null,
  hetAbMin: null,
  hetAbMax: null,
  homAltAbMin: null,
  genotypeFtMode: "ignore",
};

export type IntakeQcCheck = {
  id: string;
  label: string;
  status: "pass" | "warning";
  detail: string;
};

export type DbnsfpPredictorDefinition = {
  id: string;
  label: string;
  scoreColumn: string;
  predictionColumn?: string;
  damagingDirection?: "higher" | "lower";
  damagingThreshold?: number;
};

export type DbnsfpPredictorValue = {
  score: number | null;
  prediction: string;
};

// Optional dbNSFP 5.3.1a fields exposed by the annotation UI. The core
// diagnostic set (AlphaMissense, CADD, REVEL, MetaRNN, PrimateAI, SIFT,
// PolyPhen HDIV and conservation scores) retains dedicated fields below.
export const ADDITIONAL_DBNSFP_PREDICTORS: DbnsfpPredictorDefinition[] = [
  { id: "sift4g", label: "SIFT4G", scoreColumn: "SIFT4G_score", predictionColumn: "SIFT4G_pred", damagingDirection: "lower", damagingThreshold: 0.05 },
  { id: "polyphen_hvar", label: "PolyPhen HVAR", scoreColumn: "Polyphen2_HVAR_score", predictionColumn: "Polyphen2_HVAR_pred" },
  { id: "mutation_taster", label: "MutationTaster", scoreColumn: "MutationTaster_score", predictionColumn: "MutationTaster_pred" },
  { id: "mutation_assessor", label: "MutationAssessor", scoreColumn: "MutationAssessor_score", predictionColumn: "MutationAssessor_pred" },
  { id: "provean", label: "PROVEAN", scoreColumn: "PROVEAN_score", predictionColumn: "PROVEAN_pred", damagingDirection: "lower", damagingThreshold: -2.5 },
  { id: "vest4", label: "VEST4", scoreColumn: "VEST4_score" },
  { id: "meta_svm", label: "MetaSVM", scoreColumn: "MetaSVM_score", predictionColumn: "MetaSVM_pred" },
  { id: "meta_lr", label: "MetaLR", scoreColumn: "MetaLR_score", predictionColumn: "MetaLR_pred" },
  { id: "m_cap", label: "M-CAP", scoreColumn: "M-CAP_score", predictionColumn: "M-CAP_pred" },
  { id: "mutpred2", label: "MutPred2", scoreColumn: "MutPred2_score", predictionColumn: "MutPred2_pred" },
  { id: "mvp", label: "MVP", scoreColumn: "MVP_score" },
  { id: "gmvp", label: "gMVP", scoreColumn: "gMVP_score" },
  { id: "mpc", label: "MPC", scoreColumn: "MPC_score" },
  { id: "deogen2", label: "DEOGEN2", scoreColumn: "DEOGEN2_score", predictionColumn: "DEOGEN2_pred" },
  { id: "bayesdel_addaf", label: "BayesDel addAF", scoreColumn: "BayesDel_addAF_score", predictionColumn: "BayesDel_addAF_pred" },
  { id: "bayesdel_noaf", label: "BayesDel noAF", scoreColumn: "BayesDel_noAF_score", predictionColumn: "BayesDel_noAF_pred" },
  { id: "clinpred", label: "ClinPred", scoreColumn: "ClinPred_score", predictionColumn: "ClinPred_pred" },
  { id: "list_s2", label: "LIST-S2", scoreColumn: "LIST-S2_score", predictionColumn: "LIST-S2_pred" },
  { id: "varity_r", label: "VARITY R", scoreColumn: "VARITY_R_score" },
  { id: "varity_er", label: "VARITY ER", scoreColumn: "VARITY_ER_score" },
  { id: "esm1b", label: "ESM1b", scoreColumn: "ESM1b_score", predictionColumn: "ESM1b_pred" },
  { id: "phactboost", label: "PHACTboost", scoreColumn: "PHACTboost_score" },
  { id: "mutformer", label: "MutFormer", scoreColumn: "MutFormer_score" },
  { id: "mutscore", label: "MutScore", scoreColumn: "MutScore_score" },
  { id: "popeve", label: "popEVE", scoreColumn: "popEVE_score", predictionColumn: "popEVE_pred" },
];

export type CohortCarrier = { sample: string; evidence: GenotypeEvidence };

export type VariantRow = {
  key: string;
  source: string;
  sample: string;
  /** Cohort review mode: the carrying individuals for this variant. */
  carriers?: CohortCarrier[];
  cohortSampleCount?: number;
  /** True only for the MANE Select transcript (mane also covers Plus Clinical). */
  maneSelect?: boolean;
  /** One-row-per-variant display: the other transcript/gene rows collapsed into this one. */
  collapsedTranscriptRows?: VariantRow[];
  libraryDatasetId?: string;
  librarySampleId?: string;
  libraryIndividualId?: string | null;
  id: string;
  chrom: string;
  pos: number;
  ref: string;
  alt: string;
  liftedFromGrch37?: boolean;
  assemblyAlleleSwap?: boolean;
  unscoredIndelReasons?: string[];
  originalAssembly?: string;
  originalChrom?: string;
  originalPos?: number | null;
  originalRef?: string;
  originalAlt?: string;
  qual?: number | null;
  gene: string;
  geneId?: string;
  transcript?: string;
  biotype?: string;
  exon?: string;
  hgvsC: string;
  hgvsP: string;
  consequence: string;
  impact: Impact;
  gnomadPopmax: number | null;
  cadd: number | null;
  alphaMissense: number | null;
  alphaPrediction: string;
  revel?: number | null;
  metaRnn?: number | null;
  metaRnnPrediction?: string;
  primateAi?: number | null;
  primateAiPrediction?: string;
  sift?: number | null;
  siftPrediction?: string;
  polyPhen?: number | null;
  polyPhenPrediction?: string;
  caddRaw?: number | null;
  gerpRs?: number | null;
  phyloP100way?: number | null;
  phastCons100way?: number | null;
  availableDbnsfpPredictors?: string[];
  dbnsfpPredictors?: Record<string, DbnsfpPredictorValue>;
  gnomadFrequencies?: Record<string, number>;
  gnomadPopmaxPopulation?: string;
  loftee: string;
  lofteeFilter: string;
  lofteeFlags: string;
  loftee50bp: string;
  loftee50bpOriginal: string;
  loftee50bpChanged: boolean;
  ptcDistanceFromLastExon: number | null;
  ptcCalcStatus: string;
  clinvar: string;
  clinvarConflictingEvidence?: string;
  clinvarReviewStatus?: string;
  clinvarDisease?: string;
  clinvarAaMatch?: boolean;
  clinvarAaChangeMatch?: boolean;
  clingenErepo?: ClinGenErepoCompact[];
  haplotypeFrameStatus?: "FRAME_RESTORED_CONFIRMED" | "FRAME_RESTORATION_PARTIAL_CONFIRMED" | "FRAME_RESTORING_POSSIBLE_UNPHASED" | "";
  haplotypeFramePartners?: string[];
  haplotypeProteinChange?: string;
  spliceAI: number | null;
  promoterAI: number | null;
  loGoFuncPrediction: string;
  loGoFuncNeutral: number | null;
  loGoFuncGof: number | null;
  loGoFuncLof: number | null;
  loGoFuncAlleleAvailable: boolean;
  loGoFuncSourceTranscript: string;
  loGoFuncSourceHgvsp: string;
  loGoFuncMatch: string;
  pLi?: number | null;
  loeuf?: number | null;
  missenseZ?: number | null;
  constraintGeneId?: string;
  constraintTranscript?: string;
  constraintRelease?: string;
  constraintFlags?: string[];
  geneFlags?: string[];
  constraintLofOe?: number | null;
  constraintLofObserved?: number | null;
  constraintLofExpected?: number | null;
  constraintExomeAn90?: number | null;
  constraintExomeSegdup?: number | null;
  constraintExomeLcr?: number | null;
  genotype: string;
  dp: number | null;
  gq: number | null;
  adRef?: number | null;
  adAlt?: number | null;
  alleleBalance: number | null;
  pl?: number[] | null;
  phaseSet?: string;
  phaseHaplotype?: 0 | 1 | null;
  genotypeClass?: GenotypeEvidence["genotypeClass"];
  genotypeFilter?: string;
  duplicateRecord?: boolean;
  duplicateRecordCount?: number;
  duplicateRecordKind?: "duplicate_record" | "liftover_collision";
  sourceRecordOrdinal?: number;
  sampleGenotypes?: Record<string, GenotypeEvidence>;
  siteDepth?: number | null;
  qd?: number | null;
  mq?: number | null;
  fs?: number | null;
  sor?: number | null;
  mqRankSum?: number | null;
  readPosRankSum?: number | null;
  baseQRankSum?: number | null;
  mane: boolean;
  picked: boolean;
  repeat: boolean;
  segdup: boolean;
  phase: "phased" | "unknown";
  otherPredictors: string[];
  rawVcfEvidence?: RawVcfEvidence;
};

export type ImportSummary = {
  files: number;
  samples: number;
  cohortMode?: boolean;
  /** Aggregated across separately-called files: carrier counts have no denominator. */
  separatelyCalled?: boolean;
  passRecords: number;
  excludedNonPass: number;
  rows: number;
  warnings: string[];
  intakeQc: IntakeQcCheck[];
};

function qcBelow(
  failures: string[],
  label: string,
  value: number | null | undefined,
  cutoff: number | null,
) {
  if (cutoff === null) return;
  if (value === null || value === undefined) failures.push(`${label} unavailable`);
  else if (value < cutoff) failures.push(`${label} ${value} < ${cutoff}`);
}

function qcAbove(
  failures: string[],
  label: string,
  value: number | null | undefined,
  cutoff: number | null,
) {
  if (cutoff === null) return;
  if (value === null || value === undefined) failures.push(`${label} unavailable`);
  else if (value > cutoff) failures.push(`${label} ${value} > ${cutoff}`);
}

type GenotypeQcView = {
  dp: number | null | undefined;
  gq: number | null | undefined;
  adRef: number | null | undefined;
  adAlt: number | null | undefined;
  alleleBalance: number | null | undefined;
  genotype: string;
  genotypeClass?: GenotypeEvidence["genotypeClass"];
  genotypeFilter?: string;
};

function genotypeQcFailures(
  view: GenotypeQcView,
  row: VariantRow,
  settings: VariantQcSettings,
) {
  const failures: string[] = [];
  qcBelow(failures, "DP", view.dp, settings.minDp);
  qcBelow(failures, "GQ", view.gq, settings.minGq);
  qcBelow(failures, "alternate depth", view.adAlt, settings.minAltDepth);
  qcAbove(failures, "DP", view.dp, settings.maxDp);

  const fallbackTokens = view.genotype.split(/[|/]/);
  const numericTokens = fallbackTokens.filter((allele) => /^\d+$/.test(allele));
  const fallbackFullyCalled = fallbackTokens.length > 0
    && numericTokens.length === fallbackTokens.length;
  // A half-call such as ./1 carries the ALT but was never fully genotyped;
  // that must be visible as a QC condition, not silently shown as a clean
  // carrier in one view and dropped in another.
  if (
    !fallbackFullyCalled
    && numericTokens.some((allele) => Number(allele) > 0)
  ) {
    failures.push(`genotype partially called (${view.genotype})`);
  }
  // Uncalled (./.), partially called, and reference-only (0, 0/0) genotypes
  // must not be routed into allele-balance QC computed for a call that was
  // never made.
  const genotypeClass = view.genotypeClass
    ?? (!fallbackFullyCalled || fallbackTokens.every((allele) => allele === "0")
      ? "other"
      : fallbackTokens.length === 1
        ? "hemizygous"
        : fallbackTokens.every((allele) => allele === fallbackTokens[0])
          ? "homozygous_alt"
          : "heterozygous");
  if (genotypeClass === "heterozygous") {
    qcBelow(failures, "reference depth", view.adRef, settings.minHetRefDepth);
    qcBelow(failures, "allele balance", view.alleleBalance, settings.hetAbMin);
    qcAbove(failures, "allele balance", view.alleleBalance, settings.hetAbMax);
  } else if (
    genotypeClass === "homozygous_alt"
    || genotypeClass === "hemizygous"
  ) {
    qcBelow(
      failures,
      "alternate allele balance",
      view.alleleBalance,
      settings.homAltAbMin,
    );
  }
  const genotypeFilter = (view.genotypeFilter ?? "").trim();
  if (
    settings.genotypeFtMode === "exclude_explicit"
    && genotypeFilter
    && genotypeFilter !== "."
    && genotypeFilter.toUpperCase() !== "PASS"
  ) {
    failures.push(`genotype FT ${genotypeFilter}`);
  }
  if (
    settings.genotypeFtMode === "require_pass"
    && genotypeFilter.toUpperCase() !== "PASS"
  ) {
    failures.push(
      genotypeFilter ? `genotype FT ${genotypeFilter}` : "genotype FT unavailable",
    );
  }

  qcBelow(failures, "QUAL", row.qual, settings.minQual);
  qcBelow(failures, "QD", row.qd, settings.minQd);
  qcBelow(failures, "MQ", row.mq, settings.minMq);
  qcAbove(failures, "FS", row.fs, settings.maxFs);
  qcAbove(failures, "SOR", row.sor, settings.maxSor);
  qcBelow(failures, "MQRankSum", row.mqRankSum, settings.minMqRankSum);
  qcBelow(
    failures,
    "ReadPosRankSum",
    row.readPosRankSum,
    settings.minReadPosRankSum,
  );
  qcBelow(
    failures,
    "BaseQRankSum",
    row.baseQRankSum,
    settings.minBaseQRankSum,
  );
  return failures;
}

export function variantQcFailures(
  row: VariantRow,
  settings: VariantQcSettings,
) {
  const own = genotypeQcFailures({
    dp: row.dp, gq: row.gq, adRef: row.adRef, adAlt: row.adAlt,
    alleleBalance: row.alleleBalance, genotype: row.genotype,
    genotypeClass: row.genotypeClass, genotypeFilter: row.genotypeFilter,
  }, row, settings);
  // A cohort row's flattened evidence fields belong to one representative
  // carrier. QC on the representative alone hid variants whose other
  // carriers pass, and blessed rows whose other carriers fail: the row
  // passes when ANY carrier passes, and fails only when every carrier does.
  if (!row.carriers || row.carriers.length === 0) return own;
  for (const carrier of row.carriers) {
    const evidence = carrier.evidence;
    const failures = genotypeQcFailures({
      dp: evidence.dp, gq: evidence.gq, adRef: evidence.adRef,
      adAlt: evidence.adAlt, alleleBalance: evidence.alleleBalance,
      genotype: evidence.gt, genotypeClass: evidence.genotypeClass,
      genotypeFilter: evidence.genotypeFilter,
    }, row, settings);
    if (failures.length === 0) return [];
  }
  return [
    `no carrier passes QC (${row.carriers.length} carrier${row.carriers.length === 1 ? "" : "s"})`,
    ...own,
  ];
}

export function isHeterozygousGenotype(genotype: string) {
  // Two different called alleles are a heterozygote by definition — that
  // includes 1/2, whose two distinct ALT alleles are necessarily in trans
  // and are the most informative compound-het genotype there is. Requiring
  // a reference allele excluded exactly that case.
  const alleles = genotype.split(/[|/]/);
  return (
    alleles.length === 2
    && alleles[0] !== alleles[1]
    && alleles.every((allele) => /^\d+$/.test(allele))
  );
}

export function candidateCompoundHetKeys(rows: VariantRow[]) {
  const groups = new Map<string, Set<string>>();
  rows.filter((row) => isHeterozygousGenotype(row.genotype)).forEach((row) => {
    const key = `${row.sample}:${row.gene}`;
    if (!groups.has(key)) groups.set(key, new Set());
    groups.get(key)!.add(`${row.chrom}:${row.pos}:${row.ref}:${row.alt}`);
  });
  return new Set(
    [...groups.entries()]
      .filter(([, variants]) => variants.size >= 2)
      .map(([key]) => key),
  );
}

function transcriptGroupKey(row: VariantRow) {
  return [
    row.source, row.chrom, row.pos, row.ref, row.alt, row.gene,
  ].join(":");
}

/**
 * Clinical default transcript set: retain every MANE Select/Plus Clinical
 * consequence. For an allele-gene with no MANE consequence, retain VEP's
 * PICK=1 fallback selected by --flag_pick_allele_gene.
 */
const IMPACT_SEVERITY: Record<string, number> = { HIGH: 0, MODERATE: 1, LOW: 2, MODIFIER: 3 };

/**
 * One-row-per-variant display collapse. Filters run on the full row set
 * first, so a row that qualified through any gene or transcript keeps its
 * variant visible; the representative is then chosen by clinical priority
 * (MANE Select, then MANE Plus Clinical, then consequence severity, then
 * PICK), and the remaining rows ride along for the "+N" indicator. Rows are
 * grouped per sample so family review keeps one row per carrying member.
 */
export function collapseToOneRowPerVariant(rows: VariantRow[]): VariantRow[] {
  const groups = new Map<string, VariantRow[]>();
  for (const row of rows) {
    const key = `${row.sample}:${row.chrom}:${row.pos}:${row.ref}:${row.alt}`;
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  const rank = (row: VariantRow) => [
    row.maneSelect ? 0 : row.mane ? 1 : 2,
    IMPACT_SEVERITY[row.impact] ?? 4,
    row.picked ? 0 : 1,
  ];
  return [...groups.values()].map((group) => {
    if (group.length === 1) return group[0];
    const sorted = [...group].sort((left, right) => {
      const a = rank(left); const b = rank(right);
      return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
    });
    const [representative, ...others] = sorted;
    return { ...representative, collapsedTranscriptRows: others };
  });
}

export function preferredClinicalTranscriptRows(rows: VariantRow[]) {
  const maneGroups = new Set(
    rows.filter((row) => row.mane).map(transcriptGroupKey),
  );
  return rows.filter(
    (row) => row.mane || (row.picked && !maneGroups.has(transcriptGroupKey(row))),
  );
}

const EMPTY = new Set(["", ".", "-"]);

export const COHORT_ROW_SAMPLE = "Cohort";

// In-browser review guards (see parseVcfFiles). A compressed VCF expands
// roughly 10x when decompressed and again per record x sample when parsed;
// past these bounds the tab dies of memory exhaustion. 100 MB compressed
// keeps the decompressed text near 1 GB; the cohort guard catches smaller
// files whose sample count multiplies the per-record cost.
export const EXOME_REVIEW_MAX_COMPRESSED_BYTES = 100 * 1024 * 1024;
export const COHORT_SAMPLE_GUARD = 16;
export const COHORT_SIZE_GUARD_BYTES = 20 * 1024 * 1024;
// One real calibration point: an 88-sample cohort exome review carries ~87k
// rows comfortably. The cap sits ~4x above it; routine loads (single genome
// ~40k rows, genome trio ~120k) stay far below.
export const REVIEW_ROW_CAP = 350_000;

function decode(value: string | undefined) {
  // VEP CSQ fields are percent-encoded only: '+' is a literal character and
  // load-bearing in HGVS (c.730+1G>A). Form-decoding it to a space corrupts
  // every intronic coordinate. A stray '%' that is not a valid escape must
  // degrade to the raw value for this one field, never abort a whole import.
  const raw = value ?? "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function number(value: string | undefined): number | null {
  if (!value || EMPTY.has(value)) return null;
  const parsed = Number(value.split("&")[0]);
  return Number.isFinite(parsed) ? parsed : null;
}

function truthy(value: string | undefined) {
  return Boolean(value && !EMPTY.has(value) && value !== "0");
}

function infoMap(raw: string) {
  const map: Record<string, string> = {};
  for (const item of raw.split(";")) {
    const [key, ...rest] = item.split("=");
    if (key) map[key] = rest.length ? rest.join("=") : "1";
  }
  return map;
}

function alleleInfoReasons(
  info: Record<string, string>, key: string, altIndex: number,
) {
  const value = (info[key] ?? "").split(",")[altIndex] ?? "";
  if (!value || EMPTY.has(value)) return [];
  return value.split("&").map(decode).filter((item) => item && !EMPTY.has(item));
}

function clinGenErepoAssertions(raw: string | undefined, alt: string): ClinGenErepoCompact[] {
  return (raw ?? "").split(",").flatMap((token) => {
    const fields = token.split("|").map((value) => {
      try { return decodeURIComponent(value); } catch { return value; }
    });
    if (fields.length !== 9 || fields[0] !== alt) return [];
    return [{
      uuid: fields[1], caid: fields[2], assertion: fields[3], disease: fields[4],
      mondoId: fields[5], modeOfInheritance: fields[6], expertPanel: fields[7],
      approvalDate: fields[8],
    }];
  });
}

function populatedFields(record: Record<string, string>, exclude: string[] = []) {
  const excluded = new Set(exclude);
  return Object.fromEntries(
    Object.entries(record).filter(
      ([key, value]) => !excluded.has(key) && Boolean(value) && !EMPTY.has(value),
    ),
  );
}

export function haplotypeFrameEvidence(
  raw: string | undefined,
  currentVariant: string,
  sample: string,
  transcript: string,
): {
  status: "FRAME_RESTORED_CONFIRMED" | "FRAME_RESTORATION_PARTIAL_CONFIRMED" | "FRAME_RESTORING_POSSIBLE_UNPHASED" | "";
  partners: string[];
  protein: string;
} {
  const transcriptBase = transcript.split(".")[0];
  const matches = (raw ?? "").split(",").flatMap((entry) => {
    const fields = entry.split("|").map(decode);
    if (fields.length < 6) return [];
    const [variant, eventSample, eventTranscript, status, partners, protein] = fields;
    if (
      variant !== currentVariant
      || eventSample !== sample
      || eventTranscript.split(".")[0] !== transcriptBase
    ) return [];
    if (
      status !== "FRAME_RESTORED_CONFIRMED"
      && status !== "FRAME_RESTORATION_PARTIAL_CONFIRMED"
      && status !== "FRAME_RESTORING_POSSIBLE_UNPHASED"
    ) return [];
    return [{
      status: status as "FRAME_RESTORED_CONFIRMED" | "FRAME_RESTORATION_PARTIAL_CONFIRMED" | "FRAME_RESTORING_POSSIBLE_UNPHASED",
      partners: partners.split("&").filter(Boolean),
      protein,
    }];
  });
  const priority = {
    FRAME_RESTORED_CONFIRMED: 0,
    FRAME_RESTORATION_PARTIAL_CONFIRMED: 1,
    FRAME_RESTORING_POSSIBLE_UNPHASED: 2,
  };
  return matches.sort(
    (left, right) => priority[left.status] - priority[right.status],
  )[0] ?? { status: "" as const, partners: [] as string[], protein: "" };
}

function first(record: Record<string, string>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (value && !EMPTY.has(value)) return value;
  }
  return "";
}

function maximum(record: Record<string, string>, keys: string[]) {
  const values = keys.flatMap((key) =>
    (record[key] ?? "").split(/[,&]/).map(number).filter((v): v is number => v !== null),
  );
  return values.length ? Math.max(...values) : null;
}

function alleleIndexedInfo(
  info: Record<string, string>,
  altIndex: number,
  altCount: number,
) {
  // Record-level per-allele INFO fields (Number=A) carry one comma-separated
  // value per ALT in ALT order. Reducing across that list with max() assigns
  // the highest allele's value (e.g. a common allele's gnomAD AF) to every
  // allele, which then removes the rare candidate via the popmax filter.
  // Select this ALT's token when the comma arity matches the ALT count; any
  // other shape cannot be attributed and is left as-is.
  if (altCount <= 1) return info;
  const indexed: Record<string, string> = {};
  for (const [key, value] of Object.entries(info)) {
    if (key === "CSQ") {
      indexed[key] = value;
      continue;
    }
    const tokens = value.split(",");
    indexed[key] = tokens.length === altCount ? tokens[altIndex] : value;
  }
  return indexed;
}

function preferredMaximum(record: Record<string, string>, keyGroups: string[][]) {
  for (const keys of keyGroups) {
    const value = maximum(record, keys);
    if (value !== null) return value;
  }
  return null;
}

function minimum(record: Record<string, string>, keys: string[]) {
  const values = keys.flatMap((key) =>
    (record[key] ?? "").split(/[,&]/).map(number).filter((v): v is number => v !== null),
  );
  return values.length ? Math.min(...values) : null;
}

function gnomadFrequencies(record: Record<string, string>) {
  const frequencies: Record<string, number> = {};
  for (const key of Object.keys(record)) {
    if (!/^gnomAD[eg]?_.*AF/i.test(key) || /popmax/i.test(key)) continue;
    const value = maximum(record, [key]);
    if (value !== null) frequencies[key] = value;
  }
  return frequencies;
}

function uniqueValues(value: string | undefined) {
  return [...new Set(
    (value ?? "")
      .split(/[,&]/)
      .map((item) => decode(item))
      .filter((item) => !EMPTY.has(item)),
  )];
}

function parseGenotype(
  format: string,
  sampleValue: string,
  altIndex: number,
  retainRawFields = false,
): GenotypeEvidence {
  const keys = format.split(":");
  const values = sampleValue.split(":");
  const fields = Object.fromEntries(keys.map((key, index) => [key, values[index] ?? ""]));
  const gt = fields.GT || "./.";
  const alleleNumber = altIndex + 1;
  const alleleTokens = gt.split(/[|/]/);
  const numericTokens = alleleTokens.filter((allele) => /^\d+$/.test(allele));
  const called = alleleTokens.length > 0 && numericTokens.length === alleleTokens.length;
  // A half-call such as ./1 demonstrably carries the ALT even though the
  // second allele is unresolved; carrier must come from the present numeric
  // tokens. `partialCall` marks these so cohort mode keeps them as carriers
  // and QC flags them explicitly in every view.
  const carrier = numericTokens.some((allele) => Number(allele) === alleleNumber);
  const partialCall = carrier && !called && numericTokens.length > 0;
  const altCopies = numericTokens.filter((allele) => Number(allele) === alleleNumber).length;
  const genotypeClass: GenotypeEvidence["genotypeClass"] = (
    !called
      ? "other"
      : alleleTokens.length === 1 && altCopies === 1
        ? "hemizygous"
        : alleleTokens.length >= 2 && altCopies === alleleTokens.length
          ? "homozygous_alt"
          : altCopies > 0
            ? "heterozygous"
            : "other"
  );
  // "." (or an absent AD) means depth was not measured — it must parse to
  // null, not 0: a fabricated 0 reads as strong hom-alt evidence and fails
  // depth QC as if it were a real measurement. Note Number("") === 0.
  const ad = (fields.AD || "").split(",").map((value) => {
    const trimmed = value.trim();
    if (!trimmed || trimmed === ".") return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  });
  const numericAd = ad.filter((value): value is number => value !== null);
  const totalDepth = numericAd.reduce((sum, value) => sum + value, 0);
  const adRef = ad[0] ?? null;
  const adAlt = ad[alleleNumber] ?? null;
  const plTokens = (fields.PL || "").split(",")
    .map((value) => value.trim())
    .filter((value) => value !== "" && value !== ".");
  const plValues = plTokens.map(Number);
  const pl = plValues.length > 0 && plValues.every(Number.isFinite) ? plValues : null;
  let phaseHaplotype: 0 | 1 | null = null;
  if (gt.includes("|") && alleleTokens.length === 2) {
    if (Number(alleleTokens[0]) === alleleNumber) phaseHaplotype = 0;
    if (Number(alleleTokens[1]) === alleleNumber) phaseHaplotype = 1;
  }
  return {
    gt,
    called,
    carrier,
    dp: number(fields.DP),
    gq: number(fields.GQ),
    adRef,
    adAlt,
    alleleBalance: adAlt !== null && totalDepth > 0 ? adAlt / totalDepth : null,
    pl,
    phased: gt.includes("|"),
    phaseSet: fields.PS || fields.PID || "",
    phaseHaplotype,
    genotypeClass,
    partialCall,
    genotypeFilter: fields.FT || "",
    rawFields: retainRawFields ? populatedFields(fields) : undefined,
  };
}

function transcriptRows(csq: string, fields: string[]) {
  if (!csq || !fields.length) return [{} as Record<string, string>];
  return csq.split(",").map((entry) => {
    const values = entry.split("|");
    return Object.fromEntries(fields.map((field, index) => [field, decode(values[index])]));
  });
}

function assemblyFromHeader(lines: string[]) {
  const candidates = new Set<string>();
  let liftedFromGrch37 = false;
  for (const line of lines) {
    if (line.startsWith("##iei_target_assembly=GRCh38")) candidates.add("GRCh38");
    if (line.startsWith("##iei_liftover=<") && line.includes("SourceAssembly=GRCh37")) {
      liftedFromGrch37 = true;
    }
    if (line.startsWith("##reference=")) {
      const value = line.toLowerCase();
      if (/(grch[\s_.-]*38|hg[\s_.-]*38)/.test(value)) candidates.add("GRCh38");
      if (/(grch[\s_.-]*37|hg[\s_.-]*19|hs37d5)/.test(value)) candidates.add("GRCh37");
    }
    if (line.startsWith("##contig=<") && /ID=(?:chr)?1(?:,|>)/.test(line)) {
      const length = line.match(/length=(\d+)/)?.[1];
      if (length === "248956422") candidates.add("GRCh38");
      if (length === "249250621") candidates.add("GRCh37");
    }
    if (line.startsWith("#CHROM")) break;
  }
  return {
    assembly: candidates.size === 1 ? [...candidates][0] : candidates.size ? "conflict" : "unknown",
    liftedFromGrch37,
  };
}

const GRCH38_CONTIG_LENGTHS: Record<string, number> = {
  "1": 248956422, "2": 242193529, "3": 198295559, "4": 190214555,
  "5": 181538259, "6": 170805979, "7": 159345973, "8": 145138636,
  "9": 138394717, "10": 133797422, "11": 135086622, "12": 133275309,
  "13": 114364328, "14": 107043718, "15": 101991189, "16": 90338345,
  "17": 83257441, "18": 80373285, "19": 58617616, "20": 64444167,
  "21": 46709983, "22": 50818468, X: 156040895, Y: 57227415,
  MT: 16569,
};

function normalizedContig(contig: string) {
  const normalized = contig.replace(/^chr/i, "");
  return normalized === "M" ? "MT" : normalized;
}

function contigDefinitions(lines: string[]) {
  const definitions = new Map<string, { order: number; length: number | null }>();
  for (const line of lines) {
    if (!line.startsWith("##contig=<")) continue;
    const id = line.match(/(?:^|[,<])ID=([^,>]+)/)?.[1];
    if (!id) continue;
    const parsedLength = Number(line.match(/(?:^|,)length=(\d+)/i)?.[1]);
    definitions.set(id, {
      order: definitions.size,
      length: Number.isFinite(parsedLength) ? parsedLength : null,
    });
  }
  return definitions;
}

function validRefAllele(allele: string) {
  return /^[ACGTN]+$/i.test(allele);
}

function validAltAllele(allele: string) {
  return /^[ACGTN]+$/i.test(allele)
    || allele === "*"
    || /^<[^<>\s]+>$/.test(allele)
    || /^[ACGTN.]*[\[\]][^\[\]\s]+:\d+[\[\]][ACGTN.]*$/i.test(allele);
}

function bgzfBlockSize(bytes: Uint8Array, offset = 0): number | null {
  if (
    offset + 18 > bytes.length ||
    bytes[offset] !== 0x1f ||
    bytes[offset + 1] !== 0x8b ||
    bytes[offset + 2] !== 0x08 ||
    !(bytes[offset + 3] & 0x04)
  ) return null;

  const extraLength = bytes[offset + 10] | (bytes[offset + 11] << 8);
  const extraEnd = offset + 12 + extraLength;
  if (extraEnd > bytes.length) return null;
  let cursor = offset + 12;
  while (cursor + 4 <= extraEnd) {
    const subfieldLength = bytes[cursor + 2] | (bytes[cursor + 3] << 8);
    if (
      bytes[cursor] === 0x42 &&
      bytes[cursor + 1] === 0x43 &&
      subfieldLength === 2 &&
      cursor + 6 <= extraEnd
    ) {
      return (bytes[cursor + 4] | (bytes[cursor + 5] << 8)) + 1;
    }
    cursor += 4 + subfieldLength;
  }
  return null;
}

async function decompressMember(bytes: Uint8Array) {
  const buffer = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function* streamChunks(stream: ReadableStream<Uint8Array>) {
  const reader = stream.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value?.byteLength) yield value;
    }
  } finally {
    reader.releaseLock();
  }
}

async function* bgzfChunks(file: File) {
  // Stream compressed bytes in bounded slices instead of materializing the
  // whole file: a single arrayBuffer() call held the entire compressed
  // whole-genome VCF (alongside the inflated chunks and parsed rows) in one
  // tab, turning a slow import into an out-of-memory crash that surfaced as
  // the import "hanging". The carry buffer holds at most one slice plus one
  // incomplete trailing block.
  const SLICE_BYTES = 8 * 1024 * 1024;
  let filePosition = 0;
  let carry = new Uint8Array(0);

  for (;;) {
    let offset = 0;
    const batch: Uint8Array[] = [];
    while (batch.length < 32) {
      const blockSize = bgzfBlockSize(carry, offset);
      if (blockSize === null || offset + blockSize > carry.length) break;
      batch.push(carry.subarray(offset, offset + blockSize));
      offset += blockSize;
    }
    if (batch.length) {
      const inflated = await Promise.all(batch.map(decompressMember));
      for (const chunk of inflated) yield chunk;
      carry = carry.slice(offset); // copy: don't pin the consumed buffer
      continue;
    }
    if (filePosition >= file.size) {
      if (carry.length) {
        throw new Error(
          `invalid or truncated BGZF block at byte ${file.size - carry.length}`,
        );
      }
      return;
    }
    const slice = new Uint8Array(
      await file.slice(filePosition, filePosition + SLICE_BYTES).arrayBuffer(),
    );
    filePosition += slice.length;
    const merged = new Uint8Array(carry.length + slice.length);
    merged.set(carry, 0);
    merged.set(slice, carry.length);
    carry = merged;
  }
}

async function* decodedLines(chunks: AsyncIterable<Uint8Array>) {
  const decoder = new TextDecoder();
  let pending = "";
  let firstLine = true;
  for await (const chunk of chunks) {
    pending += decoder.decode(chunk, { stream: true });
    let newline = pending.indexOf("\n");
    while (newline >= 0) {
      let line = pending.slice(0, newline).replace(/\r$/, "");
      pending = pending.slice(newline + 1);
      if (firstLine) {
        line = line.replace(/^\uFEFF/, "");
        firstLine = false;
      }
      yield line;
      newline = pending.indexOf("\n");
    }
  }
  pending += decoder.decode();
  if (pending) {
    yield firstLine ? pending.replace(/^\uFEFF/, "") : pending;
  }
}

async function* fileLines(file: File) {
  const signature = new Uint8Array(await file.slice(0, 64).arrayBuffer());
  const hasGzipMagic = signature[0] === 0x1f && signature[1] === 0x8b;
  const isBgzf = bgzfBlockSize(signature) !== null;
  const namedAsGzip = file.name.toLowerCase().endsWith(".gz");

  try {
    if (hasGzipMagic || namedAsGzip) {
      if (!("DecompressionStream" in globalThis)) {
        throw new Error("this browser does not provide DecompressionStream");
      }
      if (isBgzf) {
        yield* decodedLines(bgzfChunks(file));
      } else {
        const stream = file.stream().pipeThrough(new DecompressionStream("gzip"));
        yield* decodedLines(streamChunks(stream));
      }
    } else {
      yield* decodedLines(streamChunks(file.stream()));
    }
  } catch (error) {
    const detail = error instanceof Error && error.message ? `: ${error.message}` : "";
    throw new Error(`${file.name}: gzip/BGZF decompression failed${detail}`);
  }
}

/** Cheap header peek: sample-column count without reading the body. */
export async function vcfSampleCount(file: File): Promise<number> {
  try {
    const lines = await vcfHeaderLines(file);
    const chrom = lines.find((line) => line.startsWith("#CHROM\t"));
    if (!chrom) return 0;
    return Math.max(0, chrom.split("\t").length - 9);
  } catch {
    return 0;
  }
}

async function vcfHeaderLines(file: File) {
  const lines: string[] = [];
  for await (const line of fileLines(file)) {
    if (!lines.length && !line.startsWith("##fileformat=VCF")) {
      throw new Error(
        `${file.name}: decompressed content does not begin with a VCF fileformat header`,
      );
    }
    if (!line.startsWith("#")) {
      throw new Error(`${file.name}: VCF header is incomplete`);
    }
    lines.push(line);
    if (line.startsWith("#CHROM\t")) return lines;
  }
  throw new Error(`${file.name}: VCF header is incomplete`);
}

export async function parseVcfFiles(
  files: File[],
  options: { retainRawAnnotations?: boolean; intake?: "user" | "prepared-review" | "server-records"; carrierEntryCap?: number; aggregateMaxPopmax?: number | null; rowCap?: number } = {},
): Promise<{ rows: VariantRow[]; summary: ImportSummary }> {
  const rows: VariantRow[] = [];
  let importCohortMode = false;
  let cohortCarrierEntries = 0;
  // Separately-called aggregation: several files whose sample columns sum to
  // cohort scale are reviewed as one variant-centric list. Carriers are
  // aggregated per variant across files; individuals without a record at a
  // site are NOT confirmed reference, so these rows carry no denominator.
  const headerSampleCounts = await Promise.all(files.map((file) => vcfSampleCount(file)));
  const totalHeaderSamples = headerSampleCounts.reduce((sum, count) => sum + count, 0);
  const aggregatedCohort = files.length > 1 && totalHeaderSamples >= COHORT_SAMPLE_GUARD;
  const aggregatedVariants = new Map<string, { carriers: CohortCarrier[]; rowEmitted: boolean } | { dropped: true }>();
  const clinvarReleaseByFile = new Map<string, string>();
  const carrierEntryCap = options.carrierEntryCap ?? 3_000_000;
  const rowCap = options.rowCap ?? REVIEW_ROW_CAP;
  const evidenceByVariant = new Map<string, Record<string, GenotypeEvidence>>();
  const sampleNames = new Set<string>();
  const warnings: string[] = [];
  const unavailableFormatFields = new Set<string>();
  let passRecords = 0;
  let excludedNonPass = 0;
  let multiallelicRecords = 0;
  let filesWithContigDefinitions = 0;
  let duplicateRecordOccurrences = 0;

  for (const file of files) {
    // In-browser parsing materializes the decompressed VCF (roughly 10x the
    // compressed size) plus one object per record x sample. It is designed
    // for single-patient exomes; beyond these bounds the tab dies slowly of
    // memory exhaustion, so refuse with directions instead of crashing.
    const intake = options.intake ?? "user";
    if (intake === "user" && file.size > EXOME_REVIEW_MAX_COMPRESSED_BYTES) {
      throw new Error(
        `${file.name} is ${Math.round(file.size / 1024 / 1024)} MB — too large `
        + "for in-browser exome review, which is designed for single-patient "
        + "files. Import it with the Whole genome analysis scope instead: the "
        + "file is then prepared on the local service and never loaded whole "
        + "into the browser. (With the coding route active, every rare coding "
        + "variant is retained; clear the popmax field to keep common "
        + "variants too.)",
      );
    }
    const lines = await vcfHeaderLines(file);
    const assembly = assemblyFromHeader(lines);
    if (assembly.assembly === "GRCh37") {
      throw new Error(
        `${file.name}: annotated VCF is GRCh37/hg19; convert and annotate it through the GRCh37 intake path first`,
      );
    }
    if (assembly.assembly === "conflict") {
      throw new Error(`${file.name}: VCF header contains conflicting GRCh37/GRCh38 evidence`);
    }
    if (assembly.assembly === "unknown") {
      throw new Error(
        `${file.name}: GRCh38 cannot be confirmed from ##reference, pipeline provenance, or canonical contig lengths`,
      );
    }
    let csqFields: string[] = [];
    let samples: string[] = [];
    const chromosomeHeaders = lines.filter((line) => line.startsWith("#CHROM"));
    if (chromosomeHeaders.length !== 1) {
      throw new Error(`${file.name}: expected exactly one #CHROM header line`);
    }
    const headerColumns = chromosomeHeaders[0].split("\t");
    if (
      headerColumns.length < 10
      || headerColumns.slice(0, 9).join("\t") !== "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT"
    ) {
      throw new Error(`${file.name}: VCF header must include FORMAT and at least one sample column`);
    }
    samples = headerColumns.slice(9);
    // Cohort review mode: at 16+ sample columns the per-sample row model
    // (one row per carrier, an all-samples genotype map on every row) would
    // exhaust browser memory, so the parser switches to one row per variant
    // with a compact carrier list. Family-scale imports (2-15 samples total)
    // keep the per-sample model that trio analysis and the phenotype tab are
    // built on. A single jointly-called file keeps its carrier denominator;
    // separately-called aggregation does not.
    const jointCohortFile = samples.length >= COHORT_SAMPLE_GUARD && files.length === 1;
    const cohortMode = jointCohortFile || aggregatedCohort;
    if (cohortMode) importCohortMode = true;
    const clinvarHeader = lines.find((line) => /clinvar/i.test(line) && /20\d{2}[-_.]?\d{2}/.test(line));
    const clinvarRelease = clinvarHeader?.match(/(20\d{2}[-_.]?\d{2}(?:[-_.]?\d{2})?)/)?.[1];
    if (clinvarRelease) clinvarReleaseByFile.set(file.name, clinvarRelease);
    const duplicateSamples = samples.filter(
      (sample, index) => !sample || samples.indexOf(sample) !== index || sampleNames.has(sample),
    );
    if (duplicateSamples.length) {
      throw new Error(
        `${file.name}: duplicate or empty sample name${duplicateSamples.length > 1 ? "s" : ""}: ${[...new Set(duplicateSamples)].join(", ") || "(empty)"}`,
      );
    }
    samples.forEach((sample) => sampleNames.add(sample));

    const contigs = contigDefinitions(lines);
    if (contigs.size) filesWithContigDefinitions += 1;
    for (const [contig, definition] of contigs) {
      const expected = GRCH38_CONTIG_LENGTHS[normalizedContig(contig)];
      if (expected && definition.length !== null && definition.length !== expected) {
        throw new Error(
          `${file.name}: contig ${contig} length ${definition.length} is incompatible with GRCh38 (${expected})`,
        );
      }
    }
    const seenRecords = new Map<string, number>();
    const completedContigs = new Set<string>();
    let previousChrom = "";
    let previousPosition = -1;

    for await (const line of fileLines(file)) {
      if (line.startsWith("##INFO=<ID=CSQ")) {
        const match = line.match(/Format:\s*([^">]+)/i);
        if (match) csqFields = match[1].trim().split("|");
        continue;
      }
      if (line.startsWith("#CHROM")) {
        continue;
      }
      if (!line || line.startsWith("#")) continue;

      const columns = line.split("\t");
      if (columns.length < 9 + samples.length) {
        throw new Error(
          `${file.name}: record has ${columns.length} columns but ${9 + samples.length} are required`,
        );
      }
      const [chrom, posRaw, id, ref, altRaw, qualRaw, filter, rawInfo, format = ""] = columns;
      const pos = Number(posRaw);
      if (!Number.isInteger(pos) || pos < 1) {
        throw new Error(`${file.name}: invalid POS '${posRaw}' on contig ${chrom}`);
      }
      if (contigs.size && !contigs.has(chrom)) {
        throw new Error(`${file.name}: record contig ${chrom} is absent from the VCF contig headers`);
      }
      const declaredLength = contigs.get(chrom)?.length;
      if (declaredLength !== null && declaredLength !== undefined && pos > declaredLength) {
        throw new Error(
          `${file.name}: position ${chrom}:${pos} exceeds the declared contig length ${declaredLength}`,
        );
      }
      if (chrom !== previousChrom) {
        if (completedContigs.has(chrom)) {
          throw new Error(
            `${file.name}: contig ${chrom} reappears after another contig at ${chrom}:${pos}`,
          );
        }
        if (previousChrom) completedContigs.add(previousChrom);
        previousChrom = chrom;
        previousPosition = -1;
      }
      if (pos < previousPosition) {
        throw new Error(`${file.name}: records are not sorted at ${chrom}:${pos}`);
      }
      previousPosition = pos;

      if (!validRefAllele(ref)) {
        throw new Error(`${file.name}: invalid REF allele '${ref}' at ${chrom}:${pos}`);
      }
      const alts = altRaw.split(",");
      if (!alts.length || alts.some((alt) => !validAltAllele(alt))) {
        throw new Error(`${file.name}: invalid ALT allele '${altRaw}' at ${chrom}:${pos}`);
      }
      if (new Set(alts).size !== alts.length) {
        throw new Error(`${file.name}: duplicate ALT allele in '${altRaw}' at ${chrom}:${pos}`);
      }
      if (alts.length > 1) multiallelicRecords += 1;
      const recordKey = `${chrom}:${pos}:${ref}:${altRaw}`;
      const recordOccurrence = (seenRecords.get(recordKey) ?? 0) + 1;
      seenRecords.set(recordKey, recordOccurrence);
      if (recordOccurrence > 1) duplicateRecordOccurrences += 1;

      const formatFields = format.split(":");
      if (!formatFields.includes("GT")) {
        throw new Error(`${file.name}: FORMAT lacks GT at ${chrom}:${pos}`);
      }
      ["DP", "GQ", "AD"].forEach((field) => {
        if (!formatFields.includes(field)) unavailableFormatFields.add(field);
      });
      for (const sampleValue of columns.slice(9, 9 + samples.length)) {
        const gt = sampleValue.split(":")[formatFields.indexOf("GT")] ?? "";
        const calledAlleles = gt.split(/[|/]/).filter((allele) => allele !== ".");
        if (
          calledAlleles.some(
            (allele) => !/^\d+$/.test(allele) || Number(allele) > alts.length,
          )
        ) {
          throw new Error(`${file.name}: invalid GT '${gt}' for ${altRaw} at ${chrom}:${pos}`);
        }
      }
      // FILTER "." means no filtering was applied (VCFv4.x), not a failed
      // filter — unfiltered callsets and liftover intermediates must not be
      // silently emptied.
      if (filter !== "PASS" && filter !== ".") {
        excludedNonPass += 1;
        continue;
      }
      passRecords += 1;
      const info = infoMap(rawInfo);
      const consequences = transcriptRows(info.CSQ, csqFields);
      const fallbackSamples = samples;
      const sampleValues = columns.slice(9);

      alts.forEach((alt, altIndex) => {
        // Contig naming must be normalized in the merge key, or a trio split
        // across a chr-prefixed and an unprefixed VCF never merges evidence.
        const variantEvidenceKey = `${normalizedContig(chrom)}:${posRaw}:${ref}:${alt}`;
        const alleleInfo = alleleIndexedInfo(info, altIndex, alts.length);
        const sampleGenotypes = Object.fromEntries(fallbackSamples.map((sample, sampleIndex) => [
          sample,
          samples.length
            ? parseGenotype(
                format,
                sampleValues[sampleIndex] ?? "",
                altIndex,
                Boolean(options.retainRawAnnotations),
              )
            : {
                gt: "./.", called: false, carrier: true, dp: null, gq: null,
                adRef: null, adAlt: null, alleleBalance: null, pl: null,
                phased: false, phaseSet: "", phaseHaplotype: null,
                genotypeClass: "other", genotypeFilter: "",
              } satisfies GenotypeEvidence,
        ]));
        // Cohort mode: keep only the carriers per variant — an all-samples
        // map on every row is exactly the memory shape that cannot scale.
        const cohortCarriers = cohortMode
          ? fallbackSamples
              .map((sampleName) => ({ sample: sampleName, evidence: sampleGenotypes[sampleName] }))
              // A half-call (./1) is a demonstrated carrier and must not
              // vanish in cohort mode; only the no-genotype-data stub
              // (called false, no numeric allele) is excluded.
              .filter((entry) => entry.evidence.carrier
                && (entry.evidence.called || entry.evidence.partialCall))
          : null;
        if (cohortCarriers) {
          cohortCarrierEntries += cohortCarriers.length;
          if (cohortCarrierEntries > carrierEntryCap) {
            throw new Error(
              `${file.name}: this cohort import exceeds ${carrierEntryCap.toLocaleString()} `
              + "carrier genotypes — likely an unfiltered callset where common "
              + "variants are carried by most individuals. Import it through the "
              + "Whole genome analysis scope with the population-frequency "
              + "prefilter (gnomAD popmax) enabled, then review the prepared "
              + "result.",
            );
          }
        }
        const mergedEvidence = evidenceByVariant.get(variantEvidenceKey) ?? {};
        Object.entries(sampleGenotypes).forEach(([sample, evidence]) => {
          const previous = mergedEvidence[sample];
          // The GQ tiebreak must never let an uncalled genotype (./., which
          // joint callers routinely emit with a non-trivial GQ) overwrite a
          // real called genotype.
          if (
            !previous
            || (!previous.called && evidence.called)
            || (evidence.called === previous.called && (evidence.gq ?? -1) > (previous.gq ?? -1))
          ) {
            mergedEvidence[sample] = evidence;
          }
        });
        if (!cohortMode) evidenceByVariant.set(variantEvidenceKey, mergedEvidence);

        // Separately-called aggregation: the first file to present a variant
        // emits its rows; later files only append their carriers to the
        // shared array those rows reference. The popmax decision is made
        // once per variant, at parse time, so the union of many exomes
        // stays bounded (an unavailable popmax always retains the variant).
        let effectiveCarriers = cohortCarriers;
        if (aggregatedCohort && cohortCarriers) {
          const existing = aggregatedVariants.get(variantEvidenceKey);
          if (existing) {
            if ("dropped" in existing) return;
            existing.carriers.push(...cohortCarriers);
            // A variant first seen in a file where nobody carries it has no
            // row yet; the first occurrence that brings carriers emits it,
            // over the shared array — otherwise later files' carriers pile
            // into an orphaned list and the allele silently disappears.
            if (existing.rowEmitted || existing.carriers.length === 0) return;
            existing.rowEmitted = true;
            effectiveCarriers = existing.carriers;
          } else {
            const popmaxThreshold = options.aggregateMaxPopmax ?? null;
            if (popmaxThreshold !== null) {
              const probe = { ...alleleIndexedInfo(info, altIndex, alts.length), ...(consequences[0] ?? {}) };
              const variantPopmax = maximum(probe, [
                "gnomADg_AF_popmax", "gnomADe_AF_popmax", "gnomAD_AF_popmax", "gnomAD_popmax_AF",
                "MAX_AF", "gnomADg_AF", "gnomADe_AF", "gnomAD_AF",
              ]);
              if (variantPopmax !== null && variantPopmax > popmaxThreshold) {
                aggregatedVariants.set(variantEvidenceKey, { dropped: true });
                return;
              }
            }
            aggregatedVariants.set(variantEvidenceKey, {
              carriers: cohortCarriers,
              rowEmitted: cohortCarriers.length > 0,
            });
          }
        }
        // Cohort mode emits ONE row set per variant (sample = the cohort),
        // carrying the compact carrier list; the representative genotype
        // fields come from the best-supported carrier. Per-sample mode is
        // unchanged: one row set per carrying sample.
        const carrierEvidenceMap = effectiveCarriers
          ? Object.fromEntries(effectiveCarriers.map((entry) => [entry.sample, entry.evidence]))
          : null;
        const cohortRepresentative = effectiveCarriers && effectiveCarriers.length
          ? effectiveCarriers.reduce((best, entry) =>
              (entry.evidence.gq ?? -1) > (best.evidence.gq ?? -1) ? entry : best)
          : null;
        const rowSamples = cohortMode
          ? (cohortRepresentative ? [COHORT_ROW_SAMPLE] : [])
          : fallbackSamples;
        if (rows.length > rowCap) {
          throw new Error(
            `This import exceeds ${rowCap.toLocaleString()} review rows — beyond `
            + "what a browser review stays responsive at. "
            + (aggregatedCohort
              ? "Open fewer individuals at once, or index these files in Cohort "
                + "search and query carriers by gene list — only matched records "
                + "are ever opened."
              : "Open fewer files or samples at once, or use Cohort search for "
                + "cross-sample questions."),
          );
        }
        rowSamples.forEach((sample) => {
          const genotype = cohortMode
            ? cohortRepresentative!.evidence
            : sampleGenotypes[sample];
          if (!genotype.carrier) return;
          const unscoredIndelReasons = alleleInfoReasons(
            info, "IEI_UNSCORED_INDEL", altIndex,
          );
          const matching = consequences.filter((csq) => {
            const alleleNum = Number(csq.ALLELE_NUM || 0);
            return alleleNum ? alleleNum === altIndex + 1 : !csq.Allele || csq.Allele === alt;
          });
          // A single-ALT record's consequences necessarily describe this ALT
          // even when VEP's minimised Allele string (e.g. "-" for indels)
          // does not equal the raw ALT. On a multi-allelic record with no
          // allele match, falling back to ALL consequences would attach the
          // other alleles' genes, HGVS, and scores to this allele — emit one
          // unannotated row instead so the carrier stays visible.
          const selected = matching.length
            ? matching
            : alts.length === 1
              ? consequences
              : [{} as Record<string, string>];
          const legacyFallbackGenes = new Set<string>();
          if (!csqFields.includes("PICK")) {
            const byGene = new Map<string, Record<string, string>[]>();
            selected.forEach((csq) => {
              const combined = { ...alleleInfo, ...csq };
              const gene = first(combined, ["SYMBOL", "Gene", "HGNC"]) || "—";
              byGene.set(gene, [...(byGene.get(gene) ?? []), combined]);
            });
            byGene.forEach((entries, gene) => {
              if (
                entries.length === 1
                && !truthy(first(entries[0], ["MANE_SELECT", "MANE_PLUS_CLINICAL"]))
              ) {
                legacyFallbackGenes.add(gene);
              }
            });
          }
          selected.forEach((csq, transcriptIndex) => {
            const combined = { ...alleleInfo, ...csq };
            const currentVariant = `${chrom}:${posRaw}:${ref}:${alt}`;
            const haplotypeFrame = haplotypeFrameEvidence(
              info.IEI_HAPLOTYPE_FRAME,
              currentVariant,
              cohortMode ? cohortRepresentative!.sample : sample,
              first(combined, ["Feature"]),
            );
            const popmax = maximum(combined, [
              "gnomADg_AF_popmax", "gnomADe_AF_popmax", "gnomAD_AF_popmax", "gnomAD_popmax_AF",
              "MAX_AF", "gnomADg_AF", "gnomADe_AF", "gnomAD_AF",
            ]);
            const splice = maximum(combined, [
              "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL", "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL",
              "DS_AG", "DS_AL", "DS_DG", "DS_DL", "SpliceAI",
            ]);
            const gene = first(combined, ["SYMBOL", "Gene", "HGNC"]);
            const mane = truthy(first(combined, ["MANE_SELECT", "MANE_PLUS_CLINICAL"]));
            const picked = truthy(first(combined, ["PICK"]))
              || legacyFallbackGenes.has(gene || "—");
            const revel = maximum(combined, ["REVEL_score"]);
            const metaRnn = maximum(combined, ["MetaRNN_score"]);
            const primateAi = maximum(combined, ["PrimateAI_score"]);
            const sift = minimum(combined, ["SIFT_score"]);
            const polyPhen = maximum(combined, ["Polyphen2_HDIV_score"]);
            const populationFrequencies = gnomadFrequencies(combined);
            const availableDbnsfpPredictors = ADDITIONAL_DBNSFP_PREDICTORS
              .filter((definition) => csqFields.includes(definition.scoreColumn)
                || Boolean(definition.predictionColumn && csqFields.includes(definition.predictionColumn)))
              .map((definition) => definition.id);
            const dbnsfpPredictors = Object.fromEntries(
              ADDITIONAL_DBNSFP_PREDICTORS
                .filter((definition) => availableDbnsfpPredictors.includes(definition.id))
                .map((definition) => [
                  definition.id,
                  {
                    score: definition.damagingDirection === "lower"
                      ? minimum(combined, [definition.scoreColumn])
                      : maximum(combined, [definition.scoreColumn]),
                    prediction: definition.predictionColumn
                      ? uniqueValues(first(combined, [definition.predictionColumn])).join(" / ")
                      : "",
                  },
                ]),
            );
            const otherPredictors = [
              ["REVEL", revel],
              ["MetaRNN", metaRnn],
              ["PrimateAI", primateAi],
              ["SIFT", sift],
              ["PolyPhen", polyPhen],
            ].filter(([, value]) => value !== null && value !== undefined).map(([label, value]) => `${label} ${value}`);

            rows.push({
              key: `${file.name}:${chrom}:${posRaw}:${ref}:${alt}:${sample}:${recordOccurrence}:${transcriptIndex}`,
              source: file.name,
              sample,
              id: id === "." ? `${chrom}-${posRaw}-${ref}-${alt}` : id,
              chrom,
              pos: Number(posRaw),
              ref,
              alt,
              liftedFromGrch37: assembly.liftedFromGrch37
                || first(info, ["IEI_ORIGINAL_ASSEMBLY"]) === "GRCh37",
              assemblyAlleleSwap: truthy(info.IEI_ASSEMBLY_ALLELE_SWAP),
              unscoredIndelReasons,
              originalAssembly: first(info, ["IEI_ORIGINAL_ASSEMBLY"]),
              originalChrom: first(info, ["IEI_ORIGINAL_CHROM"]),
              originalPos: number(first(info, ["IEI_ORIGINAL_POS"])),
              originalRef: first(info, ["IEI_ORIGINAL_REF"]),
              originalAlt: (info.IEI_ORIGINAL_ALT ?? "").split(",")[altIndex]
                ? decode((info.IEI_ORIGINAL_ALT ?? "").split(",")[altIndex])
                : first(info, ["IEI_ORIGINAL_ALT"]),
              qual: number(qualRaw),
              gene: gene || "—",
              geneId: first(combined, ["Gene"]),
              transcript: first(combined, ["Feature"]),
              biotype: first(combined, ["BIOTYPE"]),
              exon: first(combined, ["EXON"]),
              hgvsC: first(combined, ["HGVSc"]),
              hgvsP: first(combined, ["HGVSp"]),
              consequence: first(combined, ["Consequence"]) || "unannotated",
              impact: (first(combined, ["IMPACT"]) || "UNKNOWN") as Impact,
              gnomadPopmax: popmax,
              gnomadPopmaxPopulation: first(combined, ["MAX_AF_POPS", "gnomAD_AF_popmax_population"]),
              gnomadFrequencies: populationFrequencies,
              cadd: preferredMaximum(combined, [
                ["CADD_PHRED", "CADD_WGS_CADD_PHRED", "CADD_WGS_PHRED"],
                ["CADD_phred"],
              ]),
              caddRaw: preferredMaximum(combined, [
                ["CADD_RAW", "CADD_WGS_CADD_RAW", "CADD_WGS_RAW"],
                ["CADD_raw"],
              ]),
              alphaMissense: maximum(combined, ["AlphaMissense_score", "am_pathogenicity"]),
              alphaPrediction: uniqueValues(first(combined, ["AlphaMissense_pred", "am_class"])).join(" / "),
              revel,
              metaRnn,
              metaRnnPrediction: uniqueValues(first(combined, ["MetaRNN_pred"])).join(" / "),
              primateAi,
              primateAiPrediction: uniqueValues(first(combined, ["PrimateAI_pred"])).join(" / "),
              sift,
              siftPrediction: uniqueValues(first(combined, ["SIFT_pred"])).join(" / "),
              polyPhen,
              polyPhenPrediction: uniqueValues(first(combined, ["Polyphen2_HDIV_pred"])).join(" / "),
              gerpRs: maximum(combined, ["GERP++_RS"]),
              phyloP100way: maximum(combined, ["phyloP100way_vertebrate"]),
              phastCons100way: maximum(combined, ["phastCons100way_vertebrate"]),
              availableDbnsfpPredictors,
              dbnsfpPredictors,
              loftee: first(combined, ["LoF", "LOFTEE"]),
              lofteeFilter: first(combined, ["LoF_filter"]),
              lofteeFlags: first(combined, ["LoF_flags"]),
              loftee50bp: first(combined, [
                "LoF_50_BP_RULE_PTC", "50_BP_RULE_recomputed",
              ]),
              loftee50bpOriginal: first(combined, [
                "LoF_50_BP_RULE_original", "50_BP_RULE_original",
              ]),
              loftee50bpChanged: truthy(first(combined, [
                "LoF_50_BP_RULE_changed", "50_BP_RULE_changed",
              ])),
              ptcDistanceFromLastExon: maximum(combined, [
                "PTC_dist_from_last_exon",
              ]),
              ptcCalcStatus: first(combined, ["PTC_calc_status"]),
              clinvar: first(combined, ["ClinVar_CLNSIG", "CLNSIG"]),
              clinvarConflictingEvidence: first(combined, [
                "ClinVar_CLNSIGCONF", "CLNSIGCONF",
              ]),
              clinvarReviewStatus: first(combined, ["ClinVar_CLNREVSTAT", "CLNREVSTAT"]),
              clinvarDisease: first(combined, ["ClinVar_CLNDN", "CLNDN"]),
              clinvarAaMatch: truthy(first(combined, ["ClinVar_path_aa_match"])),
              clinvarAaChangeMatch: truthy(first(combined, ["ClinVar_path_aa_change_match"])),
              clingenErepo: clinGenErepoAssertions(info.ClinGen_ERepo, alt),
              haplotypeFrameStatus: haplotypeFrame.status,
              haplotypeFramePartners: haplotypeFrame.partners,
              haplotypeProteinChange: haplotypeFrame.protein,
              spliceAI: splice,
              promoterAI: maximum(combined, [
                "PromoterAI_score", "promoterAI_score",
                "promoterAI_promoterAI", "PromoterAI_promoterAI",
                "promoterAI", "PromoterAI",
              ]),
              loGoFuncPrediction: first(combined, ["LoGoFunc_prediction"]),
              loGoFuncNeutral: maximum(combined, ["LoGoFunc_neutral"]),
              loGoFuncGof: maximum(combined, ["LoGoFunc_GOF"]),
              loGoFuncLof: maximum(combined, ["LoGoFunc_LOF"]),
              loGoFuncAlleleAvailable: truthy(first(combined, ["LoGoFunc_allele_available"])),
              loGoFuncSourceTranscript: first(combined, ["LoGoFunc_source_transcript"]),
              loGoFuncSourceHgvsp: first(combined, ["LoGoFunc_source_HGVSp"]),
              loGoFuncMatch: first(combined, ["LoGoFunc_match"]),
              pLi: maximum(combined, ["pLI", "gnomAD_pLI", "ExAC_pLI"]),
              loeuf: maximum(combined, ["LOEUF", "loeuf", "oe_lof_upper", "gnomAD_LOEUF"]),
              missenseZ: maximum(combined, ["mis_z", "missense_z", "gnomAD_mis_z"]),
              carriers: effectiveCarriers ?? undefined,
              cohortSampleCount: jointCohortFile ? samples.length : undefined,
              genotype: !cohortMode
                ? genotype.gt
                : jointCohortFile
                  ? `${cohortCarriers!.length}/${samples.length} carry`
                  : `${cohortCarriers!.length} carry`,
              dp: genotype.dp,
              gq: genotype.gq,
              adRef: genotype.adRef,
              adAlt: genotype.adAlt,
              alleleBalance: genotype.alleleBalance,
              pl: genotype.pl,
              phaseSet: genotype.phaseSet,
              phaseHaplotype: genotype.phaseHaplotype,
              genotypeClass: genotype.genotypeClass,
              genotypeFilter: genotype.genotypeFilter,
              sourceRecordOrdinal: recordOccurrence,
              sampleGenotypes: carrierEvidenceMap ?? sampleGenotypes,
              siteDepth: maximum(info, ["DP"]),
              qd: maximum(info, ["QD"]),
              mq: maximum(info, ["MQ"]),
              fs: maximum(info, ["FS"]),
              sor: maximum(info, ["SOR"]),
              mqRankSum: maximum(info, ["MQRankSum"]),
              readPosRankSum: maximum(info, ["ReadPosRankSum"]),
              baseQRankSum: maximum(info, ["BaseQRankSum"]),
              mane,
              maneSelect: truthy(first(combined, ["MANE_SELECT"])),
              picked,
              repeat: truthy(first(combined, ["RepeatMasker", "REPEATMASKER"])),
              segdup: truthy(first(combined, ["SegDup", "SEGDUP"])),
              phase: genotype.phased ? "phased" : "unknown",
              otherPredictors,
              rawVcfEvidence: options.retainRawAnnotations ? {
                info: populatedFields(info, ["CSQ"]),
                consequence: populatedFields(csq),
                format: genotype.rawFields ?? {},
              } : undefined,
            });
          });
        });
      });
    }
    if (!csqFields.length) {
      throw new Error(`${file.name}: no readable VEP CSQ Format schema was found`);
    }
  }

  rows.forEach((row) => {
    row.sampleGenotypes = {
      ...(row.sampleGenotypes ?? {}),
      ...(evidenceByVariant.get(
        `${normalizedContig(row.chrom)}:${row.pos}:${row.ref}:${row.alt}`,
      ) ?? {}),
    };
  });

  const duplicateGroups = new Map<string, VariantRow[]>();
  rows.forEach((row) => {
    const key = `${row.source}:${row.chrom}:${row.pos}:${row.ref}:${row.alt}`;
    duplicateGroups.set(key, [...(duplicateGroups.get(key) ?? []), row]);
  });
  duplicateGroups.forEach((group) => {
    const sourceRecords = new Set(group.map((row) => row.sourceRecordOrdinal ?? 1));
    if (sourceRecords.size < 2) return;
    const originalLoci = new Set(group.map((row) => (
      row.originalChrom && row.originalPos
        ? `${row.originalChrom}:${row.originalPos}:${row.originalRef}:${row.originalAlt}`
        : ""
    )).filter(Boolean));
    const kind = originalLoci.size > 1 ? "liftover_collision" : "duplicate_record";
    group.forEach((row) => {
      row.duplicateRecord = true;
      row.duplicateRecordCount = sourceRecords.size;
      row.duplicateRecordKind = kind;
    });
  });

  // LoGoFunc is intentionally attached only to its exact source transcript by
  // VEP. The review UI defaults to MANE, which may differ from that canonical
  // source transcript, so carry the strict result to sibling UI rows for the
  // same sample/allele/gene while retaining the source transcript explicitly.
  // This changes only the review model; raw CSQ evidence remains transcript-local.
  const loGoFuncEvidence = new Map<string, VariantRow>();
  const loGoFuncKey = (row: VariantRow) => [
    row.source, row.sample, row.chrom, row.pos, row.ref, row.alt, row.gene,
  ].join(":");
  rows.forEach((row) => {
    if (row.loGoFuncMatch === "allele_transcript_protein") {
      loGoFuncEvidence.set(loGoFuncKey(row), row);
    }
  });
  rows.forEach((row) => {
    const evidence = loGoFuncEvidence.get(loGoFuncKey(row));
    if (!evidence || row === evidence) return;
    row.loGoFuncPrediction = evidence.loGoFuncPrediction;
    row.loGoFuncNeutral = evidence.loGoFuncNeutral;
    row.loGoFuncGof = evidence.loGoFuncGof;
    row.loGoFuncLof = evidence.loGoFuncLof;
    row.loGoFuncAlleleAvailable = true;
    row.loGoFuncSourceTranscript = evidence.loGoFuncSourceTranscript;
    row.loGoFuncSourceHgvsp = evidence.loGoFuncSourceHgvsp;
    row.loGoFuncMatch = row.transcript === evidence.loGoFuncSourceTranscript
      ? "allele_transcript_protein"
      : "source_transcript_match_elsewhere";
  });

  const intakeQc: IntakeQcCheck[] = [
    {
      id: "readable",
      label: "Readable VCF / BGZF",
      status: "pass",
      detail: `${files.length} file${files.length === 1 ? "" : "s"} decompressed and parsed`,
    },
    {
      id: "header-samples",
      label: "Header and samples",
      status: "pass",
      detail: `${sampleNames.size} unique sample column${sampleNames.size === 1 ? "" : "s"}`,
    },
    {
      id: "assembly",
      label: "GRCh38 assembly",
      status: "pass",
      detail: "Confirmed from VCF provenance, reference, or canonical contig length",
    },
    {
      id: "reference-contigs",
      label: "Reference and contigs",
      status: filesWithContigDefinitions === files.length ? "pass" : "warning",
      detail: filesWithContigDefinitions === files.length
        ? "REF syntax and declared canonical contig lengths are GRCh38-compatible"
        : "REF syntax is valid; one or more files do not declare contig lengths",
    },
    {
      id: "sorted",
      label: "Sorted records",
      status: "pass",
      detail: "Each contig is contiguous and positions increase within it",
    },
    {
      id: "alleles",
      label: "Valid REF / ALT",
      status: "pass",
      detail: "Alleles and genotype allele indexes are syntactically valid",
    },
    {
      id: "duplicates",
      label: "Duplicate records",
      status: duplicateRecordOccurrences ? "warning" : "pass",
      detail: duplicateRecordOccurrences
        ? `${duplicateRecordOccurrences} repeated target record${duplicateRecordOccurrences === 1 ? "" : "s"} retained and flagged for review`
        : "No duplicate records or sample names were detected",
    },
    {
      id: "multiallelic",
      label: "Multiallelic representation",
      status: "pass",
      detail: `${multiallelicRecords} multiallelic record${multiallelicRecords === 1 ? "" : "s"} represented allele-by-allele`,
    },
    {
      id: "format",
      label: "GT / DP / GQ / AD",
      status: unavailableFormatFields.size ? "warning" : "pass",
      detail: unavailableFormatFields.size
        ? `${[...unavailableFormatFields].sort().join(", ")} absent in one or more records; active QC will hide affected calls`
        : "All required genotype evidence fields are declared",
    },
    {
      id: "csq",
      label: "VEP CSQ schema",
      status: "pass",
      detail: "A readable CSQ Format definition was found in every file",
    },
  ];

  if (aggregatedCohort) {
    // Later files appended carriers to the shared arrays after the rows were
    // emitted; refresh the labels and carrier-keyed evidence maps once.
    for (const row of rows) {
      if (!row.carriers) continue;
      row.genotype = `${row.carriers.length} carry`;
      row.sampleGenotypes = Object.fromEntries(
        row.carriers.map((entry) => [entry.sample, entry.evidence]),
      );
    }
    const releases = [...new Set(clinvarReleaseByFile.values())];
    if (releases.length > 1) {
      warnings.push(
        "These files were annotated against different ClinVar releases ("
        + [...clinvarReleaseByFile.entries()].map(([name, release]) => `${name}: ${release}`).join("; ")
        + "). ClinVar classifications may differ between files annotated at different times.",
      );
    }
    warnings.push(
      "Separately called files: carrier counts have no denominator — an individual "
      + "without a record at a site is not confirmed reference.",
    );
  }
  return {
    rows,
    summary: {
      files: files.length,
      samples: sampleNames.size,
      cohortMode: importCohortMode,
      separatelyCalled: aggregatedCohort,
      passRecords,
      excludedNonPass,
      rows: rows.length,
      warnings,
      intakeQc,
    },
  };
}
