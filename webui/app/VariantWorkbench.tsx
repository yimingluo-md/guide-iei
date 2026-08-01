"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  cancelJob,
  getCapabilities,
  getCcreContext,
  getCohortStats,
  getCohortSamples,
  getCohortReviewRecords,
  getCohortVariantDetail,
  getJobLog,
  getJobs,
  getResourceDownloads,
  getWgsReviewJob,
  getPhenotypeIndividuals,
  getPhenotypeProfiles,
  getPhenotypeStats,
  getPhenotypesBySample,
  getCohortImportJob,
  importPhenotypeInput,
  openJobReviewFile,
  openWgsReviewFile,
  prefilterWgsReview,
  previewPhenotypeInput,
  queryCohort,
  removeCohortSamples,
  savePhenotypeIndividual,
  stageAnnotationFile,
  startResourceDownload,
  startLoGoFuncPreparation,
  startPromoterAiPreparation,
  startCohortImport,
  submitJob,
  validatePhenotypeInput,
  type AnnotationJob,
  type CohortImportResult,
  type CohortImportJob,
  type CohortQueryResult,
  type CohortQueryRow,
  type CohortSample,
  type CohortStats,
  type CohortVariantDetail,
  type CcreContext,
  type PhenotypeField,
  type PhenotypeIndividual,
  type PhenotypePreview,
  type PhenotypeProfile,
  type PhenotypeStats,
  type PhenotypeValidation,
  type ResourceDownloadJob,
  type ServiceCapabilities,
  type StagedAnnotationFile,
  type WgsPrefilterOptions,
  type WgsReviewJob,
} from "./local-service";
import {
  ADDITIONAL_DBNSFP_PREDICTORS,
  candidateCompoundHetKeys,
  NO_VARIANT_QC,
  parseVcfFiles,
  preferredClinicalTranscriptRows,
  STANDARD_VARIANT_QC,
  variantQcFailures,
  type ImportSummary,
  type VariantQcSettings,
  type VariantRow,
} from "./vcf";
import {
  DEFAULT_TRIO_THRESHOLDS,
  assessDeNovo,
  compoundHetPairs,
  parsePedigree,
  type CompoundHetPair,
  type DeNovoAssessment,
  type DeNovoStatus,
  type TrioDefinition,
  type TrioThresholds,
} from "./trio";
import {
  attachGeneConstraints,
  deriveLofConstrainedGenes,
  loadBundledReferences,
  parseGeneList,
  type GeneConstraint,
  type ReferenceManifest,
} from "./reference-data";

type View = "variants" | "genes" | "compound" | "saved" | "family" | "cohort" | "phenotypes" | "gene_lists" | "import";
type AnalysisScope = "exome" | "whole_genome";
type Zygosity = "all" | "hom" | "compound" | "de_novo";
type DisplayItem =
  | "quality" | "population" | "gnomadPopulations" | "clinvar" | "transcript" | "geneConstraint"
  | "alphaMissense" | "cadd" | "spliceAI" | "promoterAI" | "loGoFunc"
  | "revel" | "metaRnn" | "primateAi" | "sift" | "polyPhen"
  | "caddRaw" | "gerp" | "phyloP" | "phastCons" | "loftee";

const STARTER_IEI = new Set(["NFKB1", "NOD2", "IL10RA", "CYBB", "CTLA4", "IL23R", "PIK3CD"]);
const IMPACTS = ["HIGH", "MODERATE", "LOW", "MODIFIER"] as const;
const DEFAULT_DISPLAY = new Set<DisplayItem>([
  "quality", "population", "clinvar", "transcript", "geneConstraint",
  "alphaMissense", "cadd", "spliceAI", "promoterAI", "loGoFunc", "loftee",
]);
const DISPLAY_STORAGE_KEY = "iei-review-visible-evidence-v1";
const DBNSFP_DISPLAY_STORAGE_KEY = "iei-review-visible-dbnsfp-predictors-v1";
const GENE_SET_STORAGE_KEYS = {
  iei: "iei-review-gene-set-iei-v1",
  hi: "iei-review-gene-set-haploinsufficiency-v1",
  dominant: "iei-review-gene-set-dominant-v1",
} as const;
const LEGACY_CUSTOM_GENE_SET_STORAGE_KEY = "iei-review-gene-set-custom-v1";
const CUSTOM_GENE_LISTS_STORAGE_KEY = "iei-review-custom-gene-lists-v2";
type BuiltInGeneSetKey = keyof typeof GENE_SET_STORAGE_KEYS;
type BundledGeneSets = Record<BuiltInGeneSetKey, Set<string>>;
type CustomGeneList = {
  id: string;
  name: string;
  genes: Set<string>;
};
// macOS file dialogs classify a compound `.vcf.gz` filename by its final
// `.gz` suffix. Keep `.vcf.gz` for descriptive browsers, but include `.gz`
// and gzip MIME types so the native chooser does not disable valid VCFs.
const VCF_FILE_ACCEPT = ".vcf,.vcf.gz,.gz,application/gzip,application/x-gzip,application/bgzip,application/vnd.1000genomes.vcf";
const DEFAULT_WGS_PREFILTER: WgsPrefilterOptions = {
  max_gnomad_popmax: 0.01,
  min_spliceai: 0.5,
  min_promoterai_abs: 0.8,
  noncoding_mode: "ccre",
};

function Icon({ name }: { name: "dna" | "upload" | "search" | "filter" | "star" | "chevron" | "file" }) {
  const paths: Record<typeof name, React.ReactNode> = {
    dna: <><path d="M7 3c6 4 4 14 10 18M17 3C11 7 13 17 7 21"/><path d="M8 7h8M7 12h10M8 17h8"/></>,
    upload: <><path d="M12 16V4m0 0L7 9m5-5 5 5"/><path d="M5 15v4h14v-4"/></>,
    search: <><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></>,
    filter: <path d="M4 5h16l-6 7v6l-4 2v-8z"/>,
    star: <path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>,
    chevron: <path d="m9 6 6 6-6 6"/>,
    file: <><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></>,
  };
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function isPathogenic(clinvar: string) {
  const normalized = clinvar.toLowerCase();
  return (normalized.includes("pathogenic") || normalized.includes("likely_pathogenic")) && !normalized.includes("conflict");
}

export function isClinvarConflictWithPathogenic(
  clinvar: string,
  conflictingEvidence = "",
) {
  if (!clinvar.toLowerCase().includes("conflict")) return false;
  const normalized = conflictingEvidence
    .replaceAll(" ", "_")
    .toLowerCase()
    .split(/[|,&/]/)
    .map((value) => value.replace(/\(\d+\)$/g, "").trim());
  return normalized.some(
    (value) => value === "pathogenic" || value === "likely_pathogenic",
  );
}

function haplotypeFrameLabel(status = "") {
  if (status === "FRAME_RESTORED_CONFIRMED") return "Frame restoration confirmed";
  if (status === "FRAME_RESTORATION_PARTIAL_CONFIRMED") {
    return "Partial frame restoration confirmed; another allele copy remains disrupted";
  }
  if (status === "FRAME_RESTORING_POSSIBLE_UNPHASED") {
    return "Frame restoration possible; phase unresolved";
  }
  return "";
}

function duplicateRecordLabel(row: VariantRow) {
  if (row.duplicateRecordKind === "liftover_collision") {
    return `Lift-over collision · ${row.duplicateRecordCount ?? 2} source records share this GRCh38 allele`;
  }
  if (row.duplicateRecord) {
    return `Repeated VCF record · ${row.duplicateRecordCount ?? 2} records retained`;
  }
  return "";
}

function unscoredIndelReasonLabel(reason: string) {
  if (reason === "SpliceAI_intronic") {
    return "Intronic indel: SpliceAI score unavailable";
  }
  if (reason === "PromoterAI_promoter") {
    return "Promoter indel: promoterAI score unavailable";
  }
  return cleanLabel(reason);
}

function isHom(gt: string) {
  const alleles = gt.split(/[|/]/);
  return alleles.length === 2 && alleles[0] === alleles[1] && alleles[0] !== "0" && alleles[0] !== ".";
}

function compactNumber(value: number | null, digits = 3) {
  if (value === null) return "—";
  if (value > 0 && value < 0.001) return value.toExponential(1);
  return value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
}

function compactFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function gnomadFrequencyLabel(key: string) {
  return key
    .replace(/^gnomADe_/, "Exomes · ")
    .replace(/^gnomADg_/, "Genomes · ")
    .replace(/^gnomAD_/, "Combined · ")
    .replace(/_AF$/i, "")
    .replaceAll("_", " ");
}

function readGeneList(file: File, setter: (genes: Set<string>) => void) {
  file.text().then((text) => setter(parseGeneList(text)));
}

type VariantCoordinates = {
  chrom: string;
  pos: number;
  ref: string;
  alt: string;
};

function fullVariantId(row: VariantCoordinates) {
  return `${row.chrom}:${row.pos}:${row.ref}:${row.alt}`;
}

function compactAllele(allele: string, limit = 13) {
  if (allele.length <= limit) return allele;
  const left = Math.ceil((limit - 1) / 2);
  const right = Math.floor((limit - 1) / 2);
  return `${allele.slice(0, left)}…${allele.slice(-right)}`;
}

function compactVariantId(row: VariantCoordinates) {
  return `${row.chrom}:${row.pos}:${compactAllele(row.ref)}:${compactAllele(row.alt)}`;
}

function VariantIdentifier({ row, full = false }: { row: VariantCoordinates; full?: boolean }) {
  const variantId = fullVariantId(row);
  return <span className="variant-id mono" title={variantId} aria-label={variantId}>{full ? variantId : compactVariantId(row)}</span>;
}

function storedGeneSet(key: BuiltInGeneSetKey, fallback: Set<string>) {
  try {
    const stored = JSON.parse(localStorage.getItem(GENE_SET_STORAGE_KEYS[key]) ?? "null");
    return Array.isArray(stored)
      ? new Set(stored.map((gene) => String(gene).toUpperCase()))
      : new Set(fallback);
  } catch {
    return new Set(fallback);
  }
}

function customGeneListId() {
  return globalThis.crypto?.randomUUID?.()
    ?? `gene-list-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function storedCustomGeneLists(): CustomGeneList[] {
  try {
    const stored = JSON.parse(localStorage.getItem(CUSTOM_GENE_LISTS_STORAGE_KEY) ?? "null");
    if (Array.isArray(stored)) {
      return stored
        .filter((item) => item && typeof item.name === "string" && Array.isArray(item.genes))
        .map((item) => ({
          id: typeof item.id === "string" ? item.id : customGeneListId(),
          name: item.name.trim() || "Custom list",
          genes: new Set(item.genes.map((gene: unknown) => String(gene).toUpperCase())),
        }));
    }
    const legacy = JSON.parse(localStorage.getItem(LEGACY_CUSTOM_GENE_SET_STORAGE_KEY) ?? "null");
    if (Array.isArray(legacy) && legacy.length) {
      return [{
        id: customGeneListId(),
        name: "Custom list",
        genes: new Set(legacy.map((gene) => String(gene).toUpperCase())),
      }];
    }
  } catch {
    // Ignore malformed workstation preferences.
  }
  return [];
}

function storeCustomGeneLists(lists: CustomGeneList[]) {
  localStorage.setItem(
    CUSTOM_GENE_LISTS_STORAGE_KEY,
    JSON.stringify(
      lists.map((list) => ({
        id: list.id,
        name: list.name,
        genes: [...list.genes].sort(),
      })),
    ),
  );
  localStorage.removeItem(LEGACY_CUSTOM_GENE_SET_STORAGE_KEY);
}

export default function VariantWorkbench() {
  const [rows, setRows] = useState<VariantRow[]>([]);
  const [view, setView] = useState<View>("import");
  const [query, setQuery] = useState("");
  const [samples, setSamples] = useState<Set<string>>(new Set());
  const [impacts, setImpacts] = useState<Set<string>>(new Set(["HIGH", "MODERATE"]));
  const [popmax, setPopmax] = useState(0.01);
  const [maneOnly, setManeOnly] = useState(true);
  const [excludeRepeat, setExcludeRepeat] = useState(true);
  const [excludeSegdup, setExcludeSegdup] = useState(true);
  const [clinvarOnly, setClinvarOnly] = useState(false);
  const [clinvarConflictOnly, setClinvarConflictOnly] = useState(false);
  const [excludeConfirmedFrameRestored, setExcludeConfirmedFrameRestored] = useState(false);
  const [qcSettings, setQcSettings] = useState<VariantQcSettings>({ ...STANDARD_VARIANT_QC });
  const [qcPreset, setQcPreset] = useState<"standard" | "none" | "custom">("standard");
  const [includeQcFailing, setIncludeQcFailing] = useState(false);
  const [ieiOnly, setIeiOnly] = useState(false);
  const [hiOnly, setHiOnly] = useState(false);
  const [dominantOnly, setDominantOnly] = useState(false);
  const [lofConstrainedOnly, setLofConstrainedOnly] = useState(false);
  const [selectedCustomListIds, setSelectedCustomListIds] = useState<Set<string>>(new Set());
  const [zygosity, setZygosity] = useState<Zygosity>("all");
  const [alphaMin, setAlphaMin] = useState<number | null>(null);
  const [caddMin, setCaddMin] = useState<number | null>(null);
  const [spliceMin, setSpliceMin] = useState<number | null>(null);
  const [promoterAbsMin, setPromoterAbsMin] = useState<number | null>(null);
  const [loGoFuncClass, setLoGoFuncClass] = useState<"all" | "GOF" | "LOF" | "Neutral">("all");
  const [loGoFuncMin, setLoGoFuncMin] = useState<number | null>(null);
  const [constraints, setConstraints] = useState<Map<string, GeneConstraint>>(new Map());
  const [referenceManifest, setReferenceManifest] = useState<ReferenceManifest | null>(null);
  const [referenceError, setReferenceError] = useState("");
  const [visibleInfo, setVisibleInfo] = useState<Set<DisplayItem>>(DEFAULT_DISPLAY);
  const [visibleDbnsfpPredictors, setVisibleDbnsfpPredictors] = useState<Set<string>>(new Set());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<VariantRow | null>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [importing, setImporting] = useState(false);
  const [importProgress, setImportProgress] = useState("");
  const [wgsImportJob, setWgsImportJob] = useState<WgsReviewJob | null>(null);
  const [importError, setImportError] = useState("");
  const [pendingReviewFiles, setPendingReviewFiles] = useState<File[]>([]);
  const [ieiGenes, setIeiGenes] = useState<Set<string>>(STARTER_IEI);
  const [hiGenes, setHiGenes] = useState<Set<string>>(new Set());
  const [dominantGenes, setDominantGenes] = useState<Set<string>>(new Set());
  const [customGeneLists, setCustomGeneLists] = useState<CustomGeneList[]>([]);
  const [bundledGeneSets, setBundledGeneSets] = useState<BundledGeneSets>({
    iei: STARTER_IEI,
    hi: new Set(),
    dominant: new Set(),
  });
  const [trio, setTrio] = useState<TrioDefinition | null>(null);
  const [pedigreeTrios, setPedigreeTrios] = useState<TrioDefinition[]>([]);
  const [pedigreeWarnings, setPedigreeWarnings] = useState<string[]>([]);
  const [trioThresholds, setTrioThresholds] = useState<TrioThresholds>({ ...DEFAULT_TRIO_THRESHOLDS });

  useEffect(() => {
    let active = true;
    loadBundledReferences().then((references) => {
      if (!active) return;
      setConstraints(references.constraints);
      const bundled = {
        iei: references.ieiGenes,
        hi: references.hiGenes,
        dominant: references.dominantGenes,
      };
      setBundledGeneSets(bundled);
      setIeiGenes(storedGeneSet("iei", bundled.iei));
      setHiGenes(storedGeneSet("hi", bundled.hi));
      setDominantGenes(storedGeneSet("dominant", bundled.dominant));
      setCustomGeneLists(storedCustomGeneLists());
      setReferenceManifest(references.manifest);
      setReferenceError("");
    }).catch((error) => {
      if (active) setReferenceError(error instanceof Error ? error.message : "Bundled reference data could not be loaded.");
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      try {
        const stored = JSON.parse(localStorage.getItem(DISPLAY_STORAGE_KEY) ?? "null");
        if (Array.isArray(stored)) setVisibleInfo(new Set(stored as DisplayItem[]));
        const storedDbnsfp = JSON.parse(localStorage.getItem(DBNSFP_DISPLAY_STORAGE_KEY) ?? "null");
        if (Array.isArray(storedDbnsfp)) setVisibleDbnsfpPredictors(new Set(storedDbnsfp as string[]));
      } catch {
        // Ignore malformed local preferences and retain the clinical defaults.
      }
      setSettingsLoaded(true);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    if (settingsLoaded) {
      localStorage.setItem(DISPLAY_STORAGE_KEY, JSON.stringify([...visibleInfo]));
      localStorage.setItem(DBNSFP_DISPLAY_STORAGE_KEY, JSON.stringify([...visibleDbnsfpPredictors]));
    }
  }, [visibleInfo, visibleDbnsfpPredictors, settingsLoaded]);

  const referencedRows = useMemo(
    () => attachGeneConstraints(rows, constraints, referenceManifest?.gnomad.release),
    [rows, constraints, referenceManifest],
  );
  const availableDbnsfpPredictors = useMemo(
    () => new Set(referencedRows.flatMap((row) => row.availableDbnsfpPredictors ?? [])),
    [referencedRows],
  );
  const hasPromoterAi = useMemo(
    () => referencedRows.some((row) => row.promoterAI !== null),
    [referencedRows],
  );
  const hasLoGoFunc = useMemo(
    () => referencedRows.some((row) => row.loGoFuncAlleleAvailable),
    [referencedRows],
  );
  const preferredClinicalKeys = useMemo(
    () => new Set(preferredClinicalTranscriptRows(referencedRows).map((row) => row.key)),
    [referencedRows],
  );
  const lofConstrainedGenes = useMemo(
    () => deriveLofConstrainedGenes(constraints),
    [constraints],
  );
  const selectedCustomGenes = useMemo(() => {
    const genes = new Set<string>();
    customGeneLists
      .filter((list) => selectedCustomListIds.has(list.id))
      .forEach((list) => list.genes.forEach((gene) => genes.add(gene)));
    return genes;
  }, [customGeneLists, selectedCustomListIds]);

  const eligibleRows = useMemo(() => referencedRows.filter((row) => {
    if (!includeQcFailing && variantQcFailures(row, qcSettings).length) return false;
    const q = query.trim().toLowerCase();
    if (q && ![row.gene, row.id, row.hgvsC, row.hgvsP, row.sample, `${row.chrom}:${row.pos}`, fullVariantId(row)].some((value) => value.toLowerCase().includes(q))) return false;
    if (samples.size && !samples.has(row.sample)) return false;
    if (impacts.size && !impacts.has(row.impact)) return false;
    if (row.gnomadPopmax !== null && row.gnomadPopmax > popmax) return false;
    if (maneOnly && !preferredClinicalKeys.has(row.key)) return false;
    if (excludeRepeat && row.repeat) return false;
    if (excludeSegdup && row.segdup) return false;
    if (clinvarOnly && !isPathogenic(row.clinvar)) return false;
    if (
      clinvarConflictOnly
      && !isClinvarConflictWithPathogenic(
        row.clinvar,
        row.clinvarConflictingEvidence,
      )
    ) return false;
    if (
      excludeConfirmedFrameRestored
      && row.haplotypeFrameStatus === "FRAME_RESTORED_CONFIRMED"
    ) return false;
    if (ieiOnly && !ieiGenes.has(row.gene.toUpperCase())) return false;
    if (hiOnly && !hiGenes.has(row.gene.toUpperCase())) return false;
    if (dominantOnly && !dominantGenes.has(row.gene.toUpperCase())) return false;
    if (lofConstrainedOnly && !lofConstrainedGenes.has(row.gene.toUpperCase())) return false;
    if (selectedCustomListIds.size && !selectedCustomGenes.has(row.gene.toUpperCase())) return false;
    if (alphaMin !== null && (row.alphaMissense === null || row.alphaMissense < alphaMin)) return false;
    if (caddMin !== null && (row.cadd === null || row.cadd < caddMin)) return false;
    if (spliceMin !== null && (row.spliceAI === null || row.spliceAI < spliceMin)) return false;
    if (promoterAbsMin !== null && (row.promoterAI === null || Math.abs(row.promoterAI) < promoterAbsMin)) return false;
    if (loGoFuncClass !== "all") {
      if (row.loGoFuncPrediction !== loGoFuncClass) return false;
      const score = loGoFuncClass === "GOF"
        ? row.loGoFuncGof
        : loGoFuncClass === "LOF" ? row.loGoFuncLof : row.loGoFuncNeutral;
      if (loGoFuncMin !== null && (score === null || score < loGoFuncMin)) return false;
    }
    return true;
  }), [referencedRows, preferredClinicalKeys, query, samples, impacts, popmax, maneOnly, excludeRepeat, excludeSegdup, clinvarOnly, clinvarConflictOnly, excludeConfirmedFrameRestored, ieiOnly, hiOnly, dominantOnly, lofConstrainedOnly, selectedCustomListIds, selectedCustomGenes, alphaMin, caddMin, spliceMin, promoterAbsMin, loGoFuncClass, loGoFuncMin, ieiGenes, hiGenes, dominantGenes, lofConstrainedGenes, qcSettings, includeQcFailing]);

  const qcFailingCalls = useMemo(
    () => new Set(
      referencedRows
        .filter((row) => variantQcFailures(row, qcSettings).length)
        .map((row) => `${row.source}:${row.chrom}:${row.pos}:${row.ref}:${row.alt}:${row.sample}`),
    ).size,
    [referencedRows, qcSettings],
  );

  const compoundKeys = useMemo(
    () => candidateCompoundHetKeys(eligibleRows),
    [eligibleRows],
  );
  const trioPairs = useMemo(
    () => trio ? compoundHetPairs(eligibleRows, trio) : [],
    [eligibleRows, trio],
  );
  const trioCandidatePairs = useMemo(
    () => trioPairs.filter((pair) => pair.phase !== "cis"),
    [trioPairs],
  );
  const trioCompoundVariantKeys = useMemo(
    () => new Set(trioCandidatePairs.flatMap((pair) => [pair.first.key, pair.second.key])),
    [trioCandidatePairs],
  );
  const deNovoAssessments = useMemo(() => {
    const assessments = new Map<string, DeNovoAssessment>();
    if (trio) eligibleRows.forEach((row) => assessments.set(row.key, assessDeNovo(row, trio, trioThresholds)));
    return assessments;
  }, [eligibleRows, trio, trioThresholds]);
  const deNovoCandidateKeys = useMemo(() => new Set(
    [...deNovoAssessments.entries()]
      .filter(([, assessment]) => ["high_confidence", "possible", "possible_parental_mosaicism"].includes(assessment.status))
      .map(([key]) => key),
  ), [deNovoAssessments]);

  const filtered = useMemo(() => eligibleRows.filter((row) => {
    if (zygosity === "hom" && !isHom(row.genotype)) return false;
    if (zygosity === "compound" && !(trio ? trioCompoundVariantKeys.has(row.key) : compoundKeys.has(`${row.sample}:${row.gene}`))) return false;
    if (zygosity === "de_novo" && !deNovoCandidateKeys.has(row.key)) return false;
    if (view === "saved" && !saved.has(row.key)) return false;
    if (view === "compound" && !(trio ? trioCompoundVariantKeys.has(row.key) : compoundKeys.has(`${row.sample}:${row.gene}`))) return false;
    return true;
  }), [eligibleRows, zygosity, trio, trioCompoundVariantKeys, compoundKeys, deNovoCandidateKeys, view, saved]);

  const uniqueSamples = [...new Set(referencedRows.map((row) => row.sample))];
  const geneCounts = useMemo(() => {
    const counts = new Map<string, number>();
    filtered.forEach((row) => counts.set(row.gene, (counts.get(row.gene) ?? 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [filtered]);

  async function importFiles(
    list: FileList | File[] | null,
    analysisScope: AnalysisScope = "exome",
    wgsFilters?: WgsPrefilterOptions,
    workstationPaths: string[] = [],
  ) {
    if (!list?.length && !workstationPaths.length) return;
    setImporting(true);
    setImportError("");
    setWgsImportJob(null);
    try {
      let reviewFiles = Array.from(list ?? []);
      const wgsMessages: string[] = [];
      if (analysisScope === "whole_genome") {
        if (!wgsFilters) throw new Error("Whole-genome prefilter settings are required.");
        const batch = `wgs-review-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        const sources = workstationPaths.map((path) => ({ path, name: fileName(path) }));
        for (let index = 0; index < reviewFiles.length; index += 1) {
          const file = reviewFiles[index];
          setImportProgress(`Staging WGS ${index + 1} of ${reviewFiles.length}: ${file.name}`);
          const staged = await stageAnnotationFile(file, batch);
          sources.push({ path: staged.path, name: file.name });
        }
        const filteredFiles: File[] = [];
        for (let index = 0; index < sources.length; index += 1) {
          const source = sources[index];
          setImportProgress(`Indexing and prefiltering WGS ${index + 1} of ${sources.length} with four readers…`);
          let job = await prefilterWgsReview(source.path, wgsFilters);
          setWgsImportJob(job);
          while (job.status === "queued" || job.status === "running") {
            setImportProgress(`WGS ${index + 1} of ${sources.length}: ${job.message}`);
            await new Promise((resolve) => window.setTimeout(resolve, 500));
            job = await getWgsReviewJob(job.id);
            setWgsImportJob(job);
          }
          if (job.status === "failed") {
            throw new Error(job.error || "Whole-genome indexing or prefiltering failed.");
          }
          if (!job.result) {
            throw new Error("Whole-genome prefilter completed without a review file.");
          }
          const filtered = job.result;
          setImportProgress(`Opening ${filtered.records_retained.toLocaleString()} retained records…`);
          filteredFiles.push(await openWgsReviewFile(filtered.id, filtered.filename));
          wgsMessages.push(
            `${source.name}: WGS prefilter retained ${filtered.records_retained.toLocaleString()} of ${filtered.records_scanned.toLocaleString()} records using ${filtered.reader_count} reader${filtered.reader_count === 1 ? "" : "s"}${filtered.cache_hit ? " (cache reused)" : ""}.`,
          );
          wgsMessages.push(`${source.name}: retained population-eligible variants matching coding/essential-splice, SpliceAI, promoterAI, or the ${filtered.noncoding_mode === "ccre" ? "ENCODE cCRE" : filtered.noncoding_mode === "all" ? "all-noncoding" : "no-additional-noncoding"} route.`);
          if (filtered.annotations_scanned > filtered.annotations_retained) {
            wgsMessages.push(
              `${source.name}: compacted ${filtered.annotations_scanned.toLocaleString()} transcript annotations to ${filtered.annotations_retained.toLocaleString()} MANE, PICK, or per-gene fallback rows without removing retained variant sites.`,
            );
          }
        }
        reviewFiles = filteredFiles;
      }
      setImportProgress("Parsing retained annotations…");
      const result = await parseVcfFiles(reviewFiles);
      result.summary.warnings.unshift(...wgsMessages);
      setRows(result.rows);
      setSummary(result.summary);
      if (analysisScope === "whole_genome") {
        setImpacts(new Set(IMPACTS));
        setManeOnly(false);
      }
      setView("variants");
      setSelected(null);
      setSamples(new Set());
      setPendingReviewFiles([]);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "Could not parse the selected VCF files.");
    } finally {
      setImporting(false);
      setImportProgress("");
      setWgsImportJob(null);
    }
  }

  function stageReviewFiles(files: File[]) {
    setPendingReviewFiles(vcfFiles(files));
    setImportError("");
    setView("import");
    setSelected(null);
  }

  function setBuiltInGeneSet(key: BuiltInGeneSetKey, genes: Set<string>) {
    ({ iei: setIeiGenes, hi: setHiGenes, dominant: setDominantGenes }[key])(genes);
    localStorage.setItem(GENE_SET_STORAGE_KEYS[key], JSON.stringify([...genes].sort()));
  }

  function resetBuiltInGeneSet(key: BuiltInGeneSetKey) {
    ({ iei: setIeiGenes, hi: setHiGenes, dominant: setDominantGenes }[key])(
      new Set(bundledGeneSets[key]),
    );
    localStorage.removeItem(GENE_SET_STORAGE_KEYS[key]);
  }

  function saveCustomLists(lists: CustomGeneList[]) {
    setCustomGeneLists(lists);
    storeCustomGeneLists(lists);
    const retainedIds = new Set(lists.map((list) => list.id));
    setSelectedCustomListIds(
      (current) => new Set([...current].filter((id) => retainedIds.has(id))),
    );
  }

  function resetFilters() {
    setQuery(""); setSamples(new Set()); setImpacts(new Set(["HIGH", "MODERATE"]));
    setPopmax(0.01); setManeOnly(true); setExcludeRepeat(true); setExcludeSegdup(true);
    setClinvarOnly(false); setClinvarConflictOnly(false);
    setExcludeConfirmedFrameRestored(false);
    setIncludeQcFailing(false);
    setIeiOnly(false); setHiOnly(false); setDominantOnly(false); setLofConstrainedOnly(false);
    setSelectedCustomListIds(new Set());
    setZygosity("all"); setAlphaMin(null); setCaddMin(null); setSpliceMin(null); setPromoterAbsMin(null); setLoGoFuncClass("all"); setLoGoFuncMin(null);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark"><Icon name="dna" /></span><span>IEI Variant Review</span><span className="version">MVP 0.5</span></div>
        <div className="top-actions">
          <span className="privacy"><span className="status-dot" />Local analysis session</span>
          <button className="primary-button" onClick={() => { setView("import"); setSelected(null); }}><Icon name="upload" />Import VCF</button>
        </div>
      </header>

      <div className={`workspace ${selected ? "review-mode" : ""} ${view === "cohort" ? "cohort-mode" : ""} ${view === "phenotypes" ? "phenotype-mode" : ""} ${view === "family" ? "family-mode" : ""} ${view === "gene_lists" ? "gene-lists-mode" : ""} ${view === "import" ? "import-mode" : ""}`}>
        <nav className="rail" aria-label="Primary navigation">
          <div className="nav-group-label">Review</div>
          {([
            ["variants", "Variants", filtered.length], ["genes", "Genes", geneCounts.length],
            ["compound", "Comp het", trio ? trioCandidatePairs.length : compoundKeys.size], ["saved", "Saved", saved.size],
          ] as const).map(([id, label, count]) => (
            <button key={id} className={`nav-item ${view === id ? "active" : ""}`} onClick={() => setView(id)}><span>{label}</span><span>{count}</span></button>
          ))}
          <button className={`nav-item ${view === "family" ? "active" : ""}`} onClick={() => { setView("family"); setSelected(null); }}><span>Family analysis</span><span>{trio ? deNovoCandidateKeys.size + trioCandidatePairs.length : "—"}</span></button>
          <div className="nav-group-label secondary">Data</div>
          <button className={`nav-item ${view === "cohort" ? "active" : ""}`} onClick={() => { setView("cohort"); setSelected(null); }}><span>Cohort search</span><Icon name="search" /></button>
          <button className={`nav-item ${view === "phenotypes" ? "active" : ""}`} onClick={() => { setView("phenotypes"); setSelected(null); }}><span>Phenotypes</span><Icon name="file" /></button>
          <button className={`nav-item ${view === "gene_lists" ? "active" : ""}`} onClick={() => { setView("gene_lists"); setSelected(null); }}><span>Gene lists</span><span>{4 + customGeneLists.length}</span></button>
          <button className={`nav-item ${view === "import" ? "active" : ""}`} onClick={() => setView("import")}><span>Import & QC</span><Icon name="chevron" /></button>
          <div className="rail-note"><strong>Defaults active</strong><span>PASS upstream</span><span>MANE transcripts</span><span>Repeat/SegDup excluded</span></div>
        </nav>

        <aside className="filters">
          <div className="filter-heading"><span><Icon name="filter" />Filters</span><button onClick={resetFilters}>Reset</button></div>
          <label className="search"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Gene, HGVS, ID, locus…" /></label>

          <FilterSection title="Samples" count={samples.size}>
            {uniqueSamples.map((sample) => <Check key={sample} label={sample} checked={samples.has(sample)} onChange={(checked) => setSamples((current) => toggleSet(current, sample, checked))} />)}
          </FilterSection>
          <FilterSection title="Gene sets" count={Number(ieiOnly) + Number(hiOnly) + Number(dominantOnly) + Number(lofConstrainedOnly) + selectedCustomListIds.size}>
            <Check label="IUIS 2024 IEI" checked={ieiOnly} onChange={setIeiOnly} note={`${ieiGenes.size} genes · ${referenceManifest ? "bundled" : "loading"}`} />
            <Check label="Haploinsufficiency" checked={hiOnly} onChange={setHiOnly} note={hiGenes.size ? `${hiGenes.size} genes · curated` : "loading"} />
            <Check label="Dominant IEI" checked={dominantOnly} onChange={setDominantOnly} note={dominantGenes.size ? `${dominantGenes.size} genes · bundled` : "loading"} />
            <Check label="LoF constrained" checked={lofConstrainedOnly} onChange={setLofConstrainedOnly} note={lofConstrainedGenes.size ? `${lofConstrainedGenes.size} genes · pLI ≥0.9 or LOEUF <0.6` : "loading"} />
            {customGeneLists.map((list) => <Check key={list.id} label={list.name} checked={selectedCustomListIds.has(list.id)} onChange={(checked) => setSelectedCustomListIds((current) => toggleSet(current, list.id, checked))} note={`${list.genes.size} custom genes`} />)}
            {!customGeneLists.length && <button className="filter-link" onClick={() => { setView("gene_lists"); setSelected(null); }}>Create a custom list</button>}
            {selectedCustomListIds.size > 1 && <p className="microcopy">Selected custom lists are combined as a union.</p>}
          </FilterSection>
          <FilterSection title="Impact" count={impacts.size}>
            <div className="chip-grid">{IMPACTS.map((impact) => <button key={impact} className={`impact-chip ${impact.toLowerCase()} ${impacts.has(impact) ? "selected" : ""}`} onClick={() => setImpacts((current) => toggleSet(current, impact, !current.has(impact)))}>{impact}</button>)}</div>
          </FilterSection>
          <FilterSection title="Population frequency">
            <label className="field-label">gnomAD popmax ≤ <strong>{popmax}</strong></label>
            <input className="range" type="range" min="0" max="0.05" step="0.0005" value={popmax} onChange={(event) => setPopmax(Number(event.target.value))} />
            <div className="range-labels"><span>0</span><span>0.01</span><span>0.05</span></div>
          </FilterSection>
          <FilterSection title="Clinical evidence">
            <Check label="ClinVar P / LP only" checked={clinvarOnly} onChange={(checked) => { setClinvarOnly(checked); if (checked) setClinvarConflictOnly(false); }} />
            <Check label="ClinVar conflict with ≥1 P / LP" checked={clinvarConflictOnly} onChange={(checked) => { setClinvarConflictOnly(checked); if (checked) setClinvarOnly(false); }} />
            <Threshold label="AlphaMissense ≥" value={alphaMin} placeholder="optional" onChange={setAlphaMin} />
            <Threshold label="CADD phred ≥" value={caddMin} placeholder="optional" onChange={setCaddMin} />
            <Threshold label="SpliceAI max ≥" value={spliceMin} placeholder="optional" onChange={setSpliceMin} />
            <Threshold label="PromoterAI |score| ≥" value={promoterAbsMin} placeholder={hasPromoterAi ? "e.g. 0.8" : "not annotated"} onChange={setPromoterAbsMin} disabled={!hasPromoterAi} />
            <p className="microcopy">{hasPromoterAi ? "Uses the absolute score, so both strong positive and negative effects qualify." : "PromoterAI annotations are unavailable in the imported VCF."}</p>
            {hasLoGoFunc && <><label className="field-label">LoGoFunc predicted class</label><select value={loGoFuncClass} onChange={(event) => setLoGoFuncClass(event.target.value as typeof loGoFuncClass)}><option value="all">All classes</option><option value="GOF">GOF</option><option value="LOF">LOF</option><option value="Neutral">Neutral</option></select>{loGoFuncClass !== "all" && <Threshold label={`${loGoFuncClass} probability ≥`} value={loGoFuncMin} placeholder="optional" onChange={setLoGoFuncMin} />}<p className="microcopy">Missense mechanism prediction; missing is not neutral.</p></>}
          </FilterSection>
          <FilterSection title="Call QC" count={qcFailingCalls}>
            <Check
              label="Include calls failing QC"
              checked={includeQcFailing}
              onChange={setIncludeQcFailing}
              note={`${qcFailingCalls} call${qcFailingCalls === 1 ? "" : "s"} ${includeQcFailing ? "shown with flags" : "hidden, not deleted"}`}
            />
            <p className="microcopy">{qcPreset === "standard" ? "Standard / gnomAD-like preset" : qcPreset === "none" ? "No call thresholds" : "Custom thresholds"} · edit under Import &amp; QC</p>
          </FilterSection>
          <FilterSection title="Genotype">
            <Check label="Hide confirmed frame-restored events" checked={excludeConfirmedFrameRestored} onChange={setExcludeConfirmedFrameRestored} note="Visible by default; possible/unphased events always remain visible" />
            <select value={zygosity} onChange={(event) => setZygosity(event.target.value as Zygosity)}>
              <option value="all">All non-reference</option><option value="hom">Homozygous</option><option value="compound">Candidate compound het</option><option value="de_novo">Trio de novo candidate</option>
            </select>
            {zygosity === "compound" && <p className="microcopy">{trio ? "Uses parental origin or available phase; cis pairs are excluded." : "Same sample + gene; configure a trio to determine parental origin."}</p>}
            {zygosity === "de_novo" && <p className="microcopy">{trio ? "High-confidence, possible, and possible parental-mosaic candidates." : "Configure a trio in Family analysis first."}</p>}
          </FilterSection>
          <FilterSection title="Transcript & regions">
            <Check label="Clinical transcripts (MANE + PICK fallback)" checked={maneOnly} onChange={setManeOnly} />
            <Check label="Exclude RepeatMasker" checked={excludeRepeat} onChange={setExcludeRepeat} />
            <Check label="Exclude SegDup" checked={excludeSegdup} onChange={setExcludeSegdup} />
          </FilterSection>
        </aside>

        <section className="content">
          {view === "cohort" ? (
            <CohortPanel onReview={(reviewRows, reviewSummary) => {
              setRows(reviewRows);
              setSummary(reviewSummary);
              setQuery("");
              setImpacts(new Set());
              setPopmax(1);
              setManeOnly(false);
              setExcludeRepeat(false);
              setExcludeSegdup(false);
              setClinvarOnly(false);
              setClinvarConflictOnly(false);
              setExcludeConfirmedFrameRestored(false);
              setIeiOnly(false);
              setHiOnly(false);
              setDominantOnly(false);
              setLofConstrainedOnly(false);
              setSelectedCustomListIds(new Set());
              setAlphaMin(null);
              setCaddMin(null);
              setSpliceMin(null);
              setPromoterAbsMin(null);
              setZygosity("all");
              setIncludeQcFailing(true);
              setSamples(new Set());
              setView("variants");
              setSelected(reviewRows[0] ?? null);
            }} />
          ) : view === "phenotypes" ? (
            <PhenotypePanel />
          ) : view === "gene_lists" ? (
            <GeneListsPanel
              geneSets={{ iei: ieiGenes, hi: hiGenes, dominant: dominantGenes }}
              bundledGeneSets={bundledGeneSets}
              setGeneSet={setBuiltInGeneSet}
              resetGeneSet={resetBuiltInGeneSet}
              lofConstrainedGenes={lofConstrainedGenes}
              customLists={customGeneLists}
              setCustomLists={saveCustomLists}
              referenceManifest={referenceManifest}
              referenceError={referenceError}
            />
          ) : view === "import" ? (
            <ImportPanel importing={importing} importProgress={importProgress} wgsImportJob={wgsImportJob} error={importError} summary={summary} pendingFiles={pendingReviewFiles} onStageFiles={stageReviewFiles} onImportFiles={importFiles} qcSettings={qcSettings} setQcSettings={setQcSettings} qcPreset={qcPreset} setQcPreset={setQcPreset} includeQcFailing={includeQcFailing} setIncludeQcFailing={setIncludeQcFailing} />
          ) : view === "family" ? (
            <FamilyPanel
              rows={eligibleRows}
              samples={uniqueSamples}
              trio={trio}
              setTrio={setTrio}
              pedigreeTrios={pedigreeTrios}
              setPedigreeTrios={setPedigreeTrios}
              warnings={pedigreeWarnings}
              setWarnings={setPedigreeWarnings}
              thresholds={trioThresholds}
              setThresholds={setTrioThresholds}
              pairs={trioPairs}
              assessments={deNovoAssessments}
              onSelect={(row) => { setSelected(row); setView("variants"); }}
            />
          ) : view === "genes" ? (
            <GenePanel genes={geneCounts} rows={filtered} onSelect={(gene) => { setQuery(gene); setView("variants"); }} />
          ) : selected ? (
            <VariantReviewWorkspace
              rows={filtered}
              selected={selected}
              setSelected={setSelected}
              saved={saved}
              setSaved={setSaved}
              compoundKeys={compoundKeys}
              visibleInfo={visibleInfo}
              settingsOpen={settingsOpen}
              setSettingsOpen={setSettingsOpen}
              setVisibleInfo={setVisibleInfo}
              availableDbnsfpPredictors={availableDbnsfpPredictors}
              visibleDbnsfpPredictors={visibleDbnsfpPredictors}
              setVisibleDbnsfpPredictors={setVisibleDbnsfpPredictors}
              trio={trio}
              trioThresholds={trioThresholds}
              qcSettings={qcSettings}
            />
          ) : (
            <>
              <div className="content-header">
                <div><p className="eyebrow">{summary ? `${summary.files} imported file${summary.files === 1 ? "" : "s"}` : "No VCF imported"}</p><h1>{view === "compound" ? "Candidate compound heterozygotes" : view === "saved" ? "Saved candidates" : "Prioritized variants"}</h1><p className="subtitle">{filtered.length} transcript-level rows · {new Set(filtered.map((row) => row.gene)).size} genes · {new Set(filtered.map((row) => row.sample)).size} samples</p></div>
                <div className="header-controls"><DisplaySettingsButton open={settingsOpen} setOpen={setSettingsOpen} visibleInfo={visibleInfo} setVisibleInfo={setVisibleInfo} availableDbnsfpPredictors={availableDbnsfpPredictors} visibleDbnsfpPredictors={visibleDbnsfpPredictors} setVisibleDbnsfpPredictors={setVisibleDbnsfpPredictors} /><button className="secondary-button" onClick={() => downloadTsv(filtered)}>Export TSV</button></div>
              </div>
              {summary && <div className="qc-strip"><span><strong>{summary.samples}</strong> samples</span><span><strong>{summary.intakeQc.filter((check) => check.status === "pass").length}</strong> intake checks passed</span><span><strong>{qcFailingCalls}</strong> calls {includeQcFailing ? "flagged" : "hidden by QC"}</span>{(summary.warnings.length > 0 || summary.intakeQc.some((check) => check.status === "warning")) && <button onClick={() => setView("import")}>Review intake QC</button>}</div>}
              <VariantTable rows={filtered} hasImportedData={Boolean(summary)} saved={saved} setSaved={setSaved} setSelected={setSelected} compoundKeys={compoundKeys} visibleInfo={visibleInfo} trio={trio} trioThresholds={trioThresholds} trioCompoundVariantKeys={trioCompoundVariantKeys} qcSettings={qcSettings} />
            </>
          )}
        </section>
      </div>
    </main>
  );
}

function FilterSection({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return <section className="filter-section"><h2>{title}{count ? <span>{count}</span> : null}</h2><div className="filter-body">{children}</div></section>;
}

function Check({ label, checked, onChange, note }: { label: string; checked: boolean; onChange: (value: boolean) => void; note?: string }) {
  return <label className="check-row"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="custom-check" /><span>{label}{note && <small>{note}</small>}</span></label>;
}

function Threshold({ label, value, placeholder, onChange, disabled = false }: { label: string; value: number | null; placeholder: string; onChange: (value: number | null) => void; disabled?: boolean }) {
  return <label className="threshold"><span>{label}</span><input type="number" step="0.01" value={value ?? ""} placeholder={placeholder} disabled={disabled} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} /></label>;
}

function toggleSet<T>(current: Set<T>, item: T, checked: boolean) {
  const next = new Set(current);
  if (checked) next.add(item);
  else next.delete(item);
  return next;
}

function VariantTable({ rows, hasImportedData, saved, setSaved, setSelected, compoundKeys, visibleInfo, trio, trioThresholds, trioCompoundVariantKeys, qcSettings }: { rows: VariantRow[]; hasImportedData: boolean; saved: Set<string>; setSaved: React.Dispatch<React.SetStateAction<Set<string>>>; setSelected: (row: VariantRow | null) => void; compoundKeys: Set<string>; visibleInfo: Set<DisplayItem>; trio: TrioDefinition | null; trioThresholds: TrioThresholds; trioCompoundVariantKeys: Set<string>; qcSettings: VariantQcSettings }) {
  if (!rows.length) return <div className="empty-state"><span className="empty-icon"><Icon name={hasImportedData ? "filter" : "upload"} /></span><h2>{hasImportedData ? "No variants match these filters" : "No variants loaded"}</h2><p>{hasImportedData ? "Relax one or more filters, or import another VEP-annotated VCF." : "Use Import & QC to annotate a VCF or review an existing VEP-annotated file."}</p></div>;
  return <div className="table-frame"><div className="table-scroll"><table><thead><tr><th className="save-col" /><th>Variant / sample</th><th>Gene</th><th>HGVS</th><th>Consequence</th><th>gnomAD<br/>popmax</th><th>Predictors</th><th>LOFTEE</th><th>ClinVar</th><th>SpliceAI</th><th>Flags</th></tr></thead><tbody>{rows.map((row) => {
    const isSaved = saved.has(row.key);
    const isCompound = trio ? trioCompoundVariantKeys.has(row.key) : compoundKeys.has(`${row.sample}:${row.gene}`);
    const deNovo = trio ? assessDeNovo(row, trio, trioThresholds) : null;
    const qcFailures = variantQcFailures(row, qcSettings);
    return <tr key={row.key} onClick={() => setSelected(row)}>
      <td><button className={`star-button ${isSaved ? "saved" : ""}`} aria-label={isSaved ? "Remove saved candidate" : "Save candidate"} onClick={(event) => { event.stopPropagation(); setSaved((current) => toggleSet(current, row.key, !isSaved)); }}><Icon name="star" /></button></td>
      <td><VariantIdentifier row={row}/><span className="cell-sub">{row.sample} · {row.genotype}</span></td>
      <td><strong className="gene">{row.gene}</strong>{isCompound && <span className="mini-badge amber">{trio ? "comp het candidate" : "comp het?"}</span>}</td>
      <td><span className="truncate-hgvs">{row.hgvsP || row.hgvsC || "—"}</span><span className="cell-sub truncate-hgvs">{row.hgvsP ? row.hgvsC : ""}</span></td>
      <td><span className={`impact-pill ${row.impact.toLowerCase()}`}>{row.impact}</span><span className="cell-sub consequence">{row.consequence.replaceAll("_", " ")}</span></td>
      <td className={row.gnomadPopmax !== null && row.gnomadPopmax > 0.001 ? "muted-value" : ""}>{compactNumber(row.gnomadPopmax)}</td>
      <td><div className="predictor-pair">{visibleInfo.has("alphaMissense") && <Score label="AM" value={row.alphaMissense} strong={(row.alphaMissense ?? 0) >= 0.564} />}{visibleInfo.has("cadd") && <Score label="CADD" value={row.cadd} strong={(row.cadd ?? 0) >= 20} />}</div></td>
      <td>{visibleInfo.has("loftee") && row.haplotypeFrameStatus === "FRAME_RESTORED_CONFIRMED" ? <><span className="mini-badge amber">Not LoF after haplotype</span><span className="cell-sub">per-variant LOFTEE {row.loftee || "—"}</span></> : visibleInfo.has("loftee") && row.loftee ? <><span className={`mini-badge ${row.loftee === "HC" ? "teal" : ""}`}>{row.loftee}</span>{row.loftee50bp && <span className="cell-sub">PTC 50-bp: {row.loftee50bp}</span>}{row.haplotypeFrameStatus && <span className="cell-sub">{haplotypeFrameLabel(row.haplotypeFrameStatus)}</span>}</> : "—"}</td>
      <td>{row.clinvar ? <><span className={`clinvar ${isPathogenic(row.clinvar) || isClinvarConflictWithPathogenic(row.clinvar, row.clinvarConflictingEvidence) ? "pathogenic" : ""}`}>{row.clinvar.replaceAll("_", " ")}</span>{isClinvarConflictWithPathogenic(row.clinvar, row.clinvarConflictingEvidence) && <span className="cell-sub">includes ≥1 P / LP submission</span>}</> : "—"}</td>
      <td>{visibleInfo.has("spliceAI") ? <Score label="" value={row.spliceAI} strong={(row.spliceAI ?? 0) >= 0.2} /> : "—"}</td>
      <td><div className="flags">{qcFailures.length > 0 && <span className="qc-fail-flag" title={qcFailures.join("; ")}>QC fail</span>}{row.unscoredIndelReasons?.length ? <span className="qc-warn-flag" title={row.unscoredIndelReasons.map(unscoredIndelReasonLabel).join("; ")}>Unscored indel</span> : null}{row.duplicateRecord && <span className="qc-warn-flag" title={duplicateRecordLabel(row)}>{row.duplicateRecordKind === "liftover_collision" ? "Lift-over collision" : "Duplicate record"}</span>}{deNovo && ["high_confidence", "possible", "possible_parental_mosaicism"].includes(deNovo.status) && <span className={`family-flag ${deNovo.status}`}>{deNovoLabel(deNovo.status)}</span>}{row.haplotypeFrameStatus && <span>{haplotypeFrameLabel(row.haplotypeFrameStatus)}</span>}{row.liftedFromGrch37 && <span>Lifted from GRCh37</span>}{row.assemblyAlleleSwap && <span title="A source allele became the GRCh38 reference; genotype and allele-indexed annotations were remapped">Assembly allele swap</span>}{row.mane && <span>MANE</span>}{row.picked && !row.mane && <span>PICK fallback</span>}{visibleInfo.has("loGoFunc") && row.loGoFuncPrediction && <span title={`Source ${row.loGoFuncSourceTranscript}`}>LoGoFunc {row.loGoFuncPrediction}</span>}{row.promoterAI !== null && <span>promoterAI</span>}{row.repeat && <span>Repeat</span>}{row.segdup && <span>SegDup</span>}</div></td>
    </tr>;
  })}</tbody></table></div></div>;
}

function Score({ label, value, strong }: { label: string; value: number | null; strong: boolean }) {
  return <span className={`score ${strong ? "strong" : ""}`}>{label && <small>{label}</small>}{compactNumber(value, 2)}</span>;
}

function DisplaySettingsButton({
  open,
  setOpen,
  visibleInfo,
  setVisibleInfo,
  availableDbnsfpPredictors,
  visibleDbnsfpPredictors,
  setVisibleDbnsfpPredictors,
}: {
  open: boolean;
  setOpen: (value: boolean) => void;
  visibleInfo: Set<DisplayItem>;
  setVisibleInfo: React.Dispatch<React.SetStateAction<Set<DisplayItem>>>;
  availableDbnsfpPredictors: Set<string>;
  visibleDbnsfpPredictors: Set<string>;
  setVisibleDbnsfpPredictors: React.Dispatch<React.SetStateAction<Set<string>>>;
}) {
  const toggle = (item: DisplayItem, checked: boolean) => setVisibleInfo((current) => toggleSet(current, item, checked));
  const toggleDbnsfp = (id: string, checked: boolean) => setVisibleDbnsfpPredictors((current) => toggleSet(current, id, checked));
  const predictors: [DisplayItem, string][] = [
    ["alphaMissense", "AlphaMissense"], ["cadd", "CADD"], ["spliceAI", "SpliceAI"], ["promoterAI", "promoterAI"], ["loGoFunc", "LoGoFunc mechanism"],
    ["revel", "REVEL"], ["metaRnn", "MetaRNN"], ["primateAi", "PrimateAI"], ["sift", "SIFT"], ["polyPhen", "PolyPhen"], ["loftee", "LOFTEE"],
    ["caddRaw", "CADD raw"], ["gerp", "GERP++ RS"], ["phyloP", "phyloP 100-way"], ["phastCons", "phastCons 100-way"],
  ];
  const sections: [DisplayItem, string][] = [
    ["quality", "Call quality"], ["population", "Population & regions"], ["gnomadPopulations", "All gnomAD population frequencies"], ["clinvar", "ClinVar"], ["transcript", "Transcript"], ["geneConstraint", "Gene constraint"],
  ];
  const detectedDbnsfp = ADDITIONAL_DBNSFP_PREDICTORS.filter((item) => availableDbnsfpPredictors.has(item.id));
  return <div className="display-settings"><button className={`secondary-button ${open ? "active" : ""}`} onClick={() => setOpen(!open)}>Display settings</button>{open && <div className="settings-popover"><div className="settings-head"><div><strong>Information shown</strong><span>Saved on this workstation</span></div><button onClick={() => setOpen(false)}>×</button></div><h3>Evidence sections</h3><div className="settings-grid">{sections.map(([key, label]) => <Check key={key} label={label} checked={visibleInfo.has(key)} onChange={(checked) => toggle(key, checked)} />)}</div><h3>Core predictors</h3><div className="settings-grid">{predictors.map(([key, label]) => <Check key={key} label={label} checked={visibleInfo.has(key)} onChange={(checked) => toggle(key, checked)} />)}</div><h3>Additional dbNSFP predictors <span className="detected-count">{detectedDbnsfp.length} detected</span></h3>{detectedDbnsfp.length ? <div className="settings-grid">{detectedDbnsfp.map((item) => <Check key={item.id} label={item.label} checked={visibleDbnsfpPredictors.has(item.id)} onChange={(checked) => toggleDbnsfp(item.id, checked)} />)}</div> : <p className="settings-empty">None were present in this VCF’s CSQ schema.</p>}<button className="settings-reset" onClick={() => { setVisibleInfo(new Set(DEFAULT_DISPLAY)); setVisibleDbnsfpPredictors(new Set()); }}>Restore defaults</button></div>}</div>;
}

function ccreDistanceLabel(distance: number) {
  if (distance === 0) return "0 bp · TSS overlaps cCRE";
  const direction = distance > 0 ? "downstream" : "upstream";
  return `${distance > 0 ? "+" : "−"}${Math.abs(distance).toLocaleString()} bp · ${direction}`;
}

function isProteinCodingBiotype(biotype: string) {
  return biotype.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_") === "protein_coding";
}

function CcreContextPanel({
  chrom, pos, ref, alt, compact = false,
}: {
  chrom: string;
  pos: number;
  ref: string;
  alt: string;
  compact?: boolean;
}) {
  const [context, setContext] = useState<CcreContext | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [includeNoncodingGenes, setIncludeNoncodingGenes] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setContext(null);
    getCcreContext({ chrom, pos, ref, alt }).then((value) => {
      if (active) setContext(value);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "cCRE context lookup failed");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [chrom, pos, ref, alt]);

  return <section className={`ccre-context ${compact ? "compact" : ""}`}>
    <div className="ccre-context-head"><div><p className="eyebrow">Regulatory-region context</p><h2>ENCODE SCREEN cCRE</h2></div>{context && <span className={`ccre-status ${context.status}`}>{context.status === "overlap" ? `${context.overlaps.length} overlap${context.overlaps.length === 1 ? "" : "s"}` : context.status === "no_overlap" ? "No overlap" : "Resource unavailable"}</span>}</div>
    {loading && <div className="ccre-loading">Checking the local SCREEN Registry and Ensembl gene TSS table…</div>}
    {error && <div className="ccre-unavailable"><strong>cCRE context unavailable</strong><span>{error}</span></div>}
    {context?.status === "resource_unavailable" && <div className="ccre-unavailable"><strong>SCREEN cCRE resource is not installed</strong><span>This is not evidence that the variant lacks a cCRE overlap. Install the native SCREEN Registry resource from Annotation datasets.</span></div>}
    {context?.status === "no_overlap" && <div className="ccre-no-overlap"><strong>Does not overlap a {context.resource_version} cCRE</strong><span>The full GRCh38 REF span was checked against the installed local resource.</span></div>}
    {context?.status === "overlap" && <>
      <div className="ccre-caveat"><strong>Important: proximity is not a target-gene assignment.</strong><span>The VEP or closest gene shown for this variant is not necessarily regulated by the cCRE. Every gene below is listed only because its gene-level TSS lies within ±{(context.gene_window_bp / 1000).toLocaleString()} kb of that cCRE; experimental or cell-specific regulatory evidence is needed to infer a target.</span></div>
      {context.gene_resource_available && <label className="ccre-gene-toggle"><input type="checkbox" checked={includeNoncodingGenes} onChange={(event) => setIncludeNoncodingGenes(event.target.checked)} /><span>Include non-protein-coding genes</span><small>Protein-coding genes are shown by default; the complete context remains available.</small></label>}
      <div className="ccre-overlap-list">{context.overlaps.map((overlap) => {
        const proteinCodingGenes = overlap.nearby_genes.filter((gene) => isProteinCodingBiotype(gene.biotype));
        const otherGenes = overlap.nearby_genes.filter((gene) => !isProteinCodingBiotype(gene.biotype));
        const displayedGenes = includeNoncodingGenes ? [...proteinCodingGenes, ...otherGenes] : proteinCodingGenes;
        return <article key={`${overlap.accession}:${overlap.start}:${overlap.end}`}>
          <header><div><strong>{overlap.accession}</strong><span>{overlap.class} · {overlap.class_label}</span></div><code>{overlap.chrom}:{overlap.start}-{overlap.end}</code></header>
          {context.gene_resource_available ? <><div className="ccre-gene-caption"><span>{includeNoncodingGenes ? "All" : "Protein-coding"} gene TSSs within ±{(context.gene_window_bp / 1000).toLocaleString()} kb</span><small>{displayedGenes.length.toLocaleString()} shown · {overlap.nearby_genes.length.toLocaleString()} total ({proteinCodingGenes.length.toLocaleString()} protein-coding, {otherGenes.length.toLocaleString()} other) · {context.gene_source} · signed by transcriptional direction</small></div>{displayedGenes.length ? <div className="ccre-gene-table-wrap"><table className="ccre-gene-table"><thead><tr><th>Gene</th><th>Ensembl ID</th><th>TSS / strand</th><th>Distance from cCRE to TSS</th><th>Biotype</th></tr></thead><tbody>{displayedGenes.map((gene) => <tr key={gene.gene_id}><td><strong>{gene.symbol}</strong></td><td className="mono">{gene.gene_id}</td><td className="mono">{overlap.chrom}:{gene.tss.toLocaleString()} · {gene.strand}</td><td className="mono">{ccreDistanceLabel(gene.distance_bp)}</td><td>{cleanLabel(gene.biotype)}</td></tr>)}</tbody></table></div> : <div className="ccre-no-coding-genes"><strong>No protein-coding gene TSSs in this window</strong><span>{otherGenes.length.toLocaleString()} non-protein-coding gene{otherGenes.length === 1 ? " is" : "s are"} available using the option above.</span></div>}</> : <div className="ccre-unavailable"><strong>Nearby-gene context unavailable</strong><span>The cCRE overlap is valid, but the release-matched Ensembl gene TSS table is not installed.</span></div>}
        </article>;
      })}</div>
    </>}
  </section>;
}

function VariantReviewWorkspace({ rows, selected, setSelected, saved, setSaved, compoundKeys, visibleInfo, settingsOpen, setSettingsOpen, setVisibleInfo, availableDbnsfpPredictors, visibleDbnsfpPredictors, setVisibleDbnsfpPredictors, trio, trioThresholds, qcSettings }: { rows: VariantRow[]; selected: VariantRow; setSelected: (row: VariantRow | null) => void; saved: Set<string>; setSaved: React.Dispatch<React.SetStateAction<Set<string>>>; compoundKeys: Set<string>; visibleInfo: Set<DisplayItem>; settingsOpen: boolean; setSettingsOpen: (value: boolean) => void; setVisibleInfo: React.Dispatch<React.SetStateAction<Set<DisplayItem>>>; availableDbnsfpPredictors: Set<string>; visibleDbnsfpPredictors: Set<string>; setVisibleDbnsfpPredictors: React.Dispatch<React.SetStateAction<Set<string>>>; trio: TrioDefinition | null; trioThresholds: TrioThresholds; qcSettings: VariantQcSettings }) {
  const detailRef = useRef<HTMLElement>(null);
  const isSaved = saved.has(selected.key);
  const isCompound = compoundKeys.has(`${selected.sample}:${selected.gene}`);
  const deNovo = trio ? assessDeNovo(selected, trio, trioThresholds) : null;
  const selectedQcFailures = variantQcFailures(selected, qcSettings);
  const selectedLoGoFuncScore = selected.loGoFuncPrediction === "GOF"
    ? selected.loGoFuncGof
    : selected.loGoFuncPrediction === "LOF"
      ? selected.loGoFuncLof
      : selected.loGoFuncPrediction === "Neutral" ? selected.loGoFuncNeutral : null;
  const predictorOptions: { key: DisplayItem; label: string; value: number | null | undefined; note?: string; strong?: boolean }[] = [
    { key: "alphaMissense", label: "AlphaMissense", value: selected.alphaMissense, note: selected.alphaPrediction, strong: (selected.alphaMissense ?? 0) >= 0.564 },
    { key: "cadd", label: "CADD phred", value: selected.cadd, strong: (selected.cadd ?? 0) >= 20 },
    { key: "spliceAI", label: "SpliceAI max", value: selected.spliceAI, strong: (selected.spliceAI ?? 0) >= 0.2 },
    { key: "promoterAI", label: "PromoterAI", value: selected.promoterAI, note: "signed promoter-effect score", strong: selected.promoterAI !== null && selected.promoterAI !== undefined && Math.abs(selected.promoterAI) >= 0.8 },
    { key: "revel", label: "REVEL", value: selected.revel, strong: (selected.revel ?? 0) >= 0.5 },
    { key: "metaRnn", label: "MetaRNN", value: selected.metaRnn, note: selected.metaRnnPrediction },
    { key: "primateAi", label: "PrimateAI", value: selected.primateAi, note: selected.primateAiPrediction },
    { key: "sift", label: "SIFT", value: selected.sift, note: selected.siftPrediction || (selected.sift !== null && selected.sift !== undefined ? "lower is more deleterious" : undefined), strong: selected.sift !== null && selected.sift !== undefined && selected.sift <= 0.05 },
    { key: "polyPhen", label: "PolyPhen HDIV", value: selected.polyPhen, note: selected.polyPhenPrediction, strong: (selected.polyPhen ?? 0) >= 0.957 },
    { key: "caddRaw", label: "CADD raw", value: selected.caddRaw },
    { key: "gerp", label: "GERP++ RS", value: selected.gerpRs },
    { key: "phyloP", label: "phyloP 100-way", value: selected.phyloP100way },
    { key: "phastCons", label: "phastCons 100-way", value: selected.phastCons100way },
  ];
  const predictorCards = predictorOptions.filter((item) => visibleInfo.has(item.key));
  const additionalPredictorCards = ADDITIONAL_DBNSFP_PREDICTORS
    .filter((definition) => visibleDbnsfpPredictors.has(definition.id)
      && selected.availableDbnsfpPredictors?.includes(definition.id))
    .map((definition) => {
      const value = selected.dbnsfpPredictors?.[definition.id];
      const strong = value?.score !== null && value?.score !== undefined
        && definition.damagingThreshold !== undefined
        && (definition.damagingDirection === "lower"
          ? value.score <= definition.damagingThreshold
          : value.score >= definition.damagingThreshold);
      return {
        key: definition.id,
        label: definition.label,
        value: value?.score ?? null,
        note: value?.prediction || "Available in VCF",
        strong,
      };
    });
  const gnomadPopulationItems: [string, React.ReactNode][] = Object.entries(selected.gnomadFrequencies ?? {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => [gnomadFrequencyLabel(key), compactNumber(value, 6)]);
  useEffect(() => {
    detailRef.current?.scrollTo({ top: 0 });
  }, [selected.key]);

  return <div className="review-workspace">
    <aside className="review-list"><div className="review-list-head"><button onClick={() => setSelected(null)}>← Back to results</button><span>{rows.length} variants</span></div><div className="review-list-scroll">{rows.map((row) => <button key={row.key} className={`review-list-item ${row.key === selected.key ? "active" : ""}`} onClick={() => setSelected(row)}><div><strong>{row.gene}</strong><span className={`impact-pill ${row.impact.toLowerCase()}`}>{row.impact}</span></div><VariantIdentifier row={row}/><small>{row.hgvsP || row.hgvsC || row.consequence.replaceAll("_", " ")}</small><small>{row.sample} · {row.genotype}</small></button>)}</div></aside>
    <article className="review-detail" ref={detailRef}>
      <div className="review-toolbar"><button className="back-button" onClick={() => setSelected(null)}>← Results</button><div><DisplaySettingsButton open={settingsOpen} setOpen={setSettingsOpen} visibleInfo={visibleInfo} setVisibleInfo={setVisibleInfo} availableDbnsfpPredictors={availableDbnsfpPredictors} visibleDbnsfpPredictors={visibleDbnsfpPredictors} setVisibleDbnsfpPredictors={setVisibleDbnsfpPredictors} /><button className={`secondary-button save-candidate ${isSaved ? "active" : ""}`} onClick={() => setSaved((current) => toggleSet(current, selected.key, !isSaved))}><Icon name="star" />{isSaved ? "Saved" : "Save candidate"}</button></div></div>
      <header className="review-hero"><div><div className="review-kickers"><span className={`impact-pill ${selected.impact.toLowerCase()}`}>{selected.impact}</span>{selectedQcFailures.length > 0 && <span className="review-badge qc-fail-flag">QC fail</span>}{selected.duplicateRecord && <span className="review-badge amber">{selected.duplicateRecordKind === "liftover_collision" ? "Lift-over collision" : "Duplicate record"}</span>}{deNovo && ["high_confidence", "possible", "possible_parental_mosaicism"].includes(deNovo.status) && <span className={`review-badge family-status ${deNovo.status}`}>{deNovoLabel(deNovo.status)}</span>}{selected.liftedFromGrch37 && <span className="review-badge amber">Lifted from GRCh37</span>}{selected.assemblyAlleleSwap && <span className="review-badge amber" title="A source allele became the GRCh38 reference; genotype and allele-indexed annotations were remapped">Assembly allele swap</span>}{selected.mane && <span className="review-badge">MANE</span>}{selected.picked && !selected.mane && <span className="review-badge">PICK fallback</span>}{isCompound && <span className="review-badge amber">candidate comp het</span>}</div><h1>{selected.gene}</h1><p>{selected.hgvsP || selected.hgvsC || fullVariantId(selected)}</p><div className="review-hero-locus"><VariantIdentifier row={selected}/><span>· {selected.sample} · {selected.genotype}</span></div>{selected.liftedFromGrch37 && selected.originalChrom && <span className="cell-sub mono">Original GRCh37: {selected.originalChrom}:{selected.originalPos} {selected.originalRef}›{selected.originalAlt}</span>}</div><div className="hero-score"><span>gnomAD popmax</span><strong>{compactNumber(selected.gnomadPopmax)}</strong></div></header>

      <section className="review-section"><div className="section-title"><div><p className="eyebrow">Computational evidence</p><h2>Predictors</h2></div><span>{predictorCards.length + additionalPredictorCards.length} shown</span></div><div className="predictor-card-grid">{[...predictorCards, ...additionalPredictorCards].map((item) => <div className={`predictor-card ${item.strong ? "strong" : ""}`} key={item.key}><span>{item.label}</span><strong>{compactNumber(item.value ?? null, item.label.includes("CADD") ? 1 : 3)}</strong><small>{item.value === null || item.value === undefined ? "No score for this variant" : item.note || "Available"}</small></div>)}</div>{visibleInfo.has("loGoFunc") && selected.loGoFuncAlleleAvailable && <div className="logofunc-evidence"><div><span>LoGoFunc missense mechanism</span><strong>{selected.loGoFuncPrediction || "Allele available; transcript/protein mismatch"}{selectedLoGoFuncScore !== null ? ` · ${compactNumber(selectedLoGoFuncScore, 3)}` : ""}</strong><small>Research prediction; not a clinical classification or LOFTEE result.</small></div><dl><div><dt>Neutral</dt><dd>{compactNumber(selected.loGoFuncNeutral, 3)}</dd></div><div><dt>GOF</dt><dd>{compactNumber(selected.loGoFuncGof, 3)}</dd></div><div><dt>LOF</dt><dd>{compactNumber(selected.loGoFuncLof, 3)}</dd></div></dl><p>Source {selected.loGoFuncSourceTranscript || "—"} · {selected.loGoFuncSourceHgvsp || "—"} · {cleanLabel(selected.loGoFuncMatch)}</p></div>}{visibleInfo.has("loftee") && <div className="loftee-line"><span>LOFTEE</span><strong>{selected.haplotypeFrameStatus === "FRAME_RESTORED_CONFIRMED" ? `Not LoF after confirmed haplotype reconstruction · per-variant ${selected.loftee || "not annotated"}` : selected.loftee || "Not applicable / not annotated"}{selected.loftee50bp ? ` · PTC 50-bp ${selected.loftee50bp}` : ""}</strong></div>}{visibleInfo.has("loftee") && selected.ptcCalcStatus && <div className="loftee-line"><span>Frameshift PTC calculation</span><strong>{selected.ptcCalcStatus.replaceAll("_", " ")}{selected.ptcDistanceFromLastExon !== null ? ` · ${selected.ptcDistanceFromLastExon} bp from final exon junction` : ""}{selected.loftee50bpChanged ? ` · replaced ${selected.loftee50bpOriginal}` : ""}</strong></div>}{selected.haplotypeFrameStatus && <div className="loftee-line"><span>Sample haplotype</span><strong>{haplotypeFrameLabel(selected.haplotypeFrameStatus)}{selected.haplotypeFramePartners?.length ? ` · partner ${selected.haplotypeFramePartners.join(", ")}` : ""}{selected.haplotypeProteinChange ? ` · ${selected.haplotypeProteinChange}` : ""}</strong></div>}</section>

      <CcreContextPanel chrom={selected.chrom} pos={selected.pos} ref={selected.ref} alt={selected.alt}/>

      <div className="evidence-layout">
        {trio && <TrioGenotypeEvidence row={selected} trio={trio} assessment={deNovo}/>}
        {visibleInfo.has("quality") && <EvidenceSection eyebrow="Sample evidence" title="Call quality"><EvidenceGrid items={[
          ["QC status", selectedQcFailures.length ? `Fail: ${selectedQcFailures.join("; ")}` : "Pass"], ["Record warning", duplicateRecordLabel(selected) || "None"], ["QUAL", compactNumber(selected.qual ?? null, 1)], ["Site depth", selected.siteDepth ?? "—"], ["Depth (DP)", selected.dp ?? "—"], ["Genotype quality", selected.gq ?? "—"], ["Allele depths", selected.adRef !== undefined || selected.adAlt !== undefined ? `${selected.adRef ?? "—"}, ${selected.adAlt ?? "—"}` : "—"], ["Allele balance", compactNumber(selected.alleleBalance, 3)], ["Genotype FT", selected.genotypeFilter || "—"], ["Genotype", selected.genotype], ["Phase", selected.phaseSet ? `${selected.phase} · PS ${selected.phaseSet}` : selected.phase],
        ]} /></EvidenceSection>}
        <PhenotypeSummary sample={selected.sample}/>
        {visibleInfo.has("clinvar") && <EvidenceSection eyebrow="Clinical evidence" title="ClinVar"><EvidenceGrid items={[
          ["Significance", cleanLabel(selected.clinvar)], ["Conflicting submissions", cleanLabel(selected.clinvarConflictingEvidence)], ["Conflict includes P / LP", isClinvarConflictWithPathogenic(selected.clinvar, selected.clinvarConflictingEvidence) ? "Yes" : "No"], ["Review status", cleanLabel(selected.clinvarReviewStatus)], ["Condition", cleanLabel(selected.clinvarDisease)], ["Same pathogenic residue", selected.clinvarAaMatch ? "Yes" : "No / not annotated"],
        ]} /></EvidenceSection>}
        {visibleInfo.has("transcript") && <EvidenceSection eyebrow="Molecular consequence" title="Transcript"><EvidenceGrid items={[
          ["HGVSc", selected.hgvsC || "—"], ["HGVSp", selected.hgvsP || "—"], ["Transcript", selected.transcript || "—"], ["Gene ID", selected.geneId || "—"], ["Biotype", cleanLabel(selected.biotype)], ["Exon", selected.exon || "—"], ["Consequence", cleanLabel(selected.consequence)], ["MANE", selected.mane ? "Yes" : "No"], ["VEP PICK", selected.picked ? "Yes" : "No"],
        ]} /></EvidenceSection>}
        {visibleInfo.has("population") && <EvidenceSection eyebrow="Population & regions" title="Frequency context"><EvidenceGrid items={[
          ["gnomAD popmax", compactNumber(selected.gnomadPopmax)], ["Popmax population", cleanLabel(selected.gnomadPopmaxPopulation)], ["RepeatMasker", selected.repeat ? "Overlap" : "No overlap"], ["Segmental duplication", selected.segdup ? "Overlap" : "No overlap"], ["Unscored indel flag", selected.unscoredIndelReasons?.map(unscoredIndelReasonLabel).join("; ") || "None"], ["Variant ID", fullVariantId(selected)], ["Source VCF", selected.source],
          ["Input assembly", selected.liftedFromGrch37 ? "GRCh37 (lifted to GRCh38)" : "GRCh38"], ["Original locus", selected.liftedFromGrch37 && selected.originalChrom ? `${selected.originalChrom}:${selected.originalPos} ${selected.originalRef}›${selected.originalAlt}` : "—"],
        ]} /></EvidenceSection>}
        {visibleInfo.has("gnomadPopulations") && <EvidenceSection eyebrow="Optional population evidence" title="gnomAD frequencies"><EvidenceGrid items={gnomadPopulationItems.length ? gnomadPopulationItems : [["Available fields", selected.key.startsWith("cohort:") ? "Population-specific fields are unavailable in the compact cohort fallback" : "No populated population-specific gnomAD fields were available for this variant"]]} /></EvidenceSection>}
        {visibleInfo.has("geneConstraint") && <EvidenceSection eyebrow="Gene-level evidence" title={selected.gene}><EvidenceGrid items={[
          ["pLI", compactNumber(selected.pLi ?? null)], ["LOEUF", compactNumber(selected.loeuf ?? null)], ["Missense Z", compactNumber(selected.missenseZ ?? null)], ["LoF observed / expected", selected.constraintLofObserved !== null && selected.constraintLofObserved !== undefined ? `${compactNumber(selected.constraintLofObserved)} / ${compactNumber(selected.constraintLofExpected ?? null)}` : "—"],
          ["LoF o/e", compactNumber(selected.constraintLofOe ?? null)], ["Constraint transcript", selected.constraintTranscript || "—"], ["Stable gene ID", selected.constraintGeneId || selected.geneId || "—"], ["gnomAD release", selected.constraintRelease || "—"],
          ["Constraint flags", selected.constraintFlags?.join(", ") || "None"], ["Gene quality flags", selected.geneFlags?.join(", ") || "None"], ["Exome bases at AN90", compactPercent(selected.constraintExomeAn90)], ["Exome SegDup / LCR", `${compactPercent(selected.constraintExomeSegdup)} / ${compactPercent(selected.constraintExomeLcr)}`],
        ]} /><p className="constraint-note">{selected.constraintRelease ? `Loaded automatically from bundled gnomAD v${selected.constraintRelease} gene constraint data using its selected MANE/canonical transcript. gnomAD recommends LOEUF over pLI for current interpretation.` : "No matching gene was found in the bundled gnomAD constraint table. Constraint metrics remain unavailable for this gene."}</p></EvidenceSection>}
      </div>
      {selected.rawVcfEvidence && <RawVcfEvidencePanel evidence={selected.rawVcfEvidence}/>}
      <div className="interpretation-banner"><strong>Review aid, not a classification</strong><span>This workspace organizes evidence; it does not assign ACMG/AMP criteria or replace clinical interpretation.</span></div>
    </article>
  </div>;
}

function deNovoLabel(status: DeNovoStatus) {
  const labels: Record<DeNovoStatus, string> = {
    high_confidence: "High-confidence de novo",
    possible: "Possible de novo",
    possible_parental_mosaicism: "Possible parental mosaicism",
    likely_artifact: "Likely artifact",
    mendelian_conflict: "Mendelian conflict",
    inherited: "Inherited",
    not_proband: "Not proband",
  };
  return labels[status];
}

function compoundPhaseLabel(phase: CompoundHetPair["phase"]) {
  return {
    confirmed_trans_inheritance: "Confirmed trans · inheritance",
    confirmed_trans_phasing: "Confirmed trans · phasing",
    possible_trans: "Possible trans",
    phase_unknown: "Phase unknown",
    cis: "Cis · excluded",
  }[phase];
}

function originLabel(origin: CompoundHetPair["firstOrigin"]) {
  return {
    maternal: "maternal",
    paternal: "paternal",
    both: "both parents",
    de_novo: "de novo candidate",
    unknown: "unknown origin",
  }[origin];
}

function variantIdentity(row: VariantRow) {
  return fullVariantId(row);
}

function FamilyPanel({
  rows, samples, trio, setTrio, pedigreeTrios, setPedigreeTrios, warnings,
  setWarnings, thresholds, setThresholds, pairs, assessments, onSelect,
}: {
  rows: VariantRow[];
  samples: string[];
  trio: TrioDefinition | null;
  setTrio: (trio: TrioDefinition | null) => void;
  pedigreeTrios: TrioDefinition[];
  setPedigreeTrios: (trios: TrioDefinition[]) => void;
  warnings: string[];
  setWarnings: (warnings: string[]) => void;
  thresholds: TrioThresholds;
  setThresholds: React.Dispatch<React.SetStateAction<TrioThresholds>>;
  pairs: CompoundHetPair[];
  assessments: Map<string, DeNovoAssessment>;
  onSelect: (row: VariantRow) => void;
}) {
  const [tab, setTab] = useState<"de_novo" | "compound">("de_novo");
  const [includeReview, setIncludeReview] = useState(false);
  const [includeCis, setIncludeCis] = useState(false);
  const pedInput = useRef<HTMLInputElement>(null);
  const configured = Boolean(
    trio?.proband && trio.mother && trio.father
    && new Set([trio.proband, trio.mother, trio.father]).size === 3,
  );
  const updateTrio = (patch: Partial<TrioDefinition>) => setTrio({
    familyId: trio?.familyId || "manual-family",
    proband: trio?.proband || "",
    mother: trio?.mother || "",
    father: trio?.father || "",
    probandSex: trio?.probandSex || "unknown",
    parentalRelationshipsConfirmed: trio?.parentalRelationshipsConfirmed || false,
    ...patch,
  });

  async function loadPed(file: File) {
    const parsed = parsePedigree(await file.text(), new Set(samples));
    setPedigreeTrios(parsed.trios);
    setWarnings(parsed.warnings);
    if (parsed.trios.length) setTrio(parsed.trios[0]);
    if (pedInput.current) pedInput.current.value = "";
  }

  const uniqueDeNovo = new Map<string, { row: VariantRow; assessment: DeNovoAssessment }>();
  if (trio) rows.filter((row) => row.sample === trio.proband).forEach((row) => {
    const assessment = assessments.get(row.key);
    if (!assessment) return;
    const candidate = ["high_confidence", "possible", "possible_parental_mosaicism"].includes(assessment.status);
    const review = ["likely_artifact", "mendelian_conflict"].includes(assessment.status);
    if (!candidate && !(includeReview && review)) return;
    const key = `${row.gene}:${variantIdentity(row)}`;
    const previous = uniqueDeNovo.get(key);
    if (!previous || (row.mane && !previous.row.mane)) uniqueDeNovo.set(key, { row, assessment });
  });
  const deNovoRows = [...uniqueDeNovo.values()].sort((a, b) => {
    const order: DeNovoStatus[] = ["high_confidence", "possible_parental_mosaicism", "possible", "mendelian_conflict", "likely_artifact", "inherited", "not_proband"];
    return order.indexOf(a.assessment.status) - order.indexOf(b.assessment.status);
  });
  const candidatePairs = pairs.filter((pair) => pair.phase !== "cis");
  const shownPairs = includeCis ? pairs : candidatePairs;
  const completeEvidence = trio ? new Set(rows
    .filter((row) => row.sample === trio.proband)
    .filter((row) => [trio.proband, trio.mother, trio.father].every((sample) => row.sampleGenotypes?.[sample]?.called))
    .map(variantIdentity)).size : 0;
  const probandVariants = trio ? new Set(rows.filter((row) => row.sample === trio.proband).map(variantIdentity)).size : 0;
  const highCount = deNovoRows.filter(({ assessment }) => assessment.status === "high_confidence").length;
  const possibleCount = deNovoRows.filter(({ assessment }) => ["possible", "possible_parental_mosaicism"].includes(assessment.status)).length;

  return <div className="family-page">
    <div className="content-header"><div><p className="eyebrow">Pedigree-aware review</p><h1>Family analysis</h1><p className="subtitle">Identify de novo candidates and resolve qualifying compound-heterozygous pairs using parental genotypes and phase.</p></div><button className="secondary-button" onClick={() => pedInput.current?.click()}>Upload PED</button><input ref={pedInput} className="sr-only" type="file" accept=".ped,.fam,text/plain" onChange={(event) => event.target.files?.[0] && loadPed(event.target.files[0])}/></div>

    <section className="family-config-card">
      <div className="family-config-head"><div><p className="eyebrow">Family configuration</p><h2>{configured ? `${trio?.familyId}: ${trio?.proband}` : "Map a mother-father-child trio"}</h2></div>{configured && <span className="local-only-badge">Session only</span>}</div>
      <p>Use a standard six-column PED file or map the loaded VCF sample names manually. High-confidence de novo calls require callable genotypes and allele depths for all three samples at the same site.</p>
      {pedigreeTrios.length > 1 && <label className="form-field"><span>Trio from PED</span><select value={trio ? `${trio.familyId}:${trio.proband}` : ""} onChange={(event) => setTrio(pedigreeTrios.find((candidate) => `${candidate.familyId}:${candidate.proband}` === event.target.value) ?? null)}>{pedigreeTrios.map((candidate) => <option key={`${candidate.familyId}:${candidate.proband}`} value={`${candidate.familyId}:${candidate.proband}`}>{candidate.familyId} · {candidate.proband}</option>)}</select></label>}
      <div className="family-mapping-grid">
        <label><span>Family ID</span><input value={trio?.familyId ?? ""} placeholder="FAMILY_1" onChange={(event) => updateTrio({ familyId: event.target.value })}/></label>
        <label><span>Proband</span><select value={trio?.proband ?? ""} onChange={(event) => updateTrio({ proband: event.target.value })}><option value="">Select sample</option>{samples.map((sample) => <option key={sample}>{sample}</option>)}</select></label>
        <label><span>Mother</span><select value={trio?.mother ?? ""} onChange={(event) => updateTrio({ mother: event.target.value })}><option value="">Select sample</option>{samples.map((sample) => <option key={sample}>{sample}</option>)}</select></label>
        <label><span>Father</span><select value={trio?.father ?? ""} onChange={(event) => updateTrio({ father: event.target.value })}><option value="">Select sample</option>{samples.map((sample) => <option key={sample}>{sample}</option>)}</select></label>
        <label><span>Proband sex</span><select value={trio?.probandSex ?? "unknown"} onChange={(event) => updateTrio({ probandSex: event.target.value as TrioDefinition["probandSex"] })}><option value="unknown">Unknown</option><option value="female">Female</option><option value="male">Male</option></select></label>
        <Check label="Parental relationships confirmed" checked={trio?.parentalRelationshipsConfirmed ?? false} onChange={(checked) => updateTrio({ parentalRelationshipsConfirmed: checked })} note="Recorded for review only; the software does not assign ACMG PS2/PM6."/>
      </div>
      {warnings.map((warning) => <div className="alert" key={warning}>{warning}</div>)}
      {trio && !configured && <div className="alert">Choose three different samples before running family analysis.</div>}
    </section>

    {configured && trio ? <>
      <div className="family-stats">
        <Stat value={highCount} label="high-confidence de novo"/>
        <Stat value={possibleCount} label="possible / mosaic"/>
        <Stat value={candidatePairs.length} label="candidate comp-het pairs"/>
        <Stat value={`${completeEvidence}/${probandVariants}`} label="variants with complete trio calls"/>
      </div>
      {completeEvidence < probandVariants && <div className="alert family-callability-warning">Some proband variants do not contain callable genotypes for both parents. Those variants can be labelled possible, but not high-confidence de novo. Absence from a separate single-sample VCF is not treated as homozygous reference.</div>}
      <section className="family-thresholds">
        <div><p className="eyebrow">Configurable screening thresholds</p><h2>Genotype evidence</h2><p>These thresholds prioritize candidates; they are not variant-classification criteria.</p></div>
        <div className="family-threshold-grid">
          <FamilyThreshold label="Proband min DP" value={thresholds.childMinDp} step={1} onChange={(value) => setThresholds((current) => ({ ...current, childMinDp: value }))}/>
          <FamilyThreshold label="Parent min DP" value={thresholds.parentMinDp} step={1} onChange={(value) => setThresholds((current) => ({ ...current, parentMinDp: value }))}/>
          <FamilyThreshold label="Minimum GQ" value={thresholds.minGq} step={1} onChange={(value) => setThresholds((current) => ({ ...current, minGq: value }))}/>
          <FamilyThreshold label="Proband AB min" value={thresholds.childAbMin} step={0.01} onChange={(value) => setThresholds((current) => ({ ...current, childAbMin: value }))}/>
          <FamilyThreshold label="Proband AB max" value={thresholds.childAbMax} step={0.01} onChange={(value) => setThresholds((current) => ({ ...current, childAbMax: value }))}/>
          <FamilyThreshold label="Parent ALT AB max" value={thresholds.parentAbMax} step={0.01} onChange={(value) => setThresholds((current) => ({ ...current, parentAbMax: value }))}/>
        </div>
      </section>
      <div className="family-tabs" role="group" aria-label="Family analysis type"><button className={tab === "de_novo" ? "active" : ""} onClick={() => setTab("de_novo")}>De novo candidates <span>{highCount + possibleCount}</span></button><button className={tab === "compound" ? "active" : ""} onClick={() => setTab("compound")}>Compound heterozygotes <span>{candidatePairs.length}</span></button></div>

      {tab === "de_novo" ? <section className="family-results">
        <div className="family-results-head"><div><p className="eyebrow">Proband: {trio.proband}</p><h2>De novo review</h2></div><Check label="Include artifacts and conflicts" checked={includeReview} onChange={setIncludeReview}/></div>
        {deNovoRows.length ? <div className="family-candidate-list">{deNovoRows.map(({ row, assessment }) => <button className="family-candidate-row" key={`${variantIdentity(row)}:${row.gene}`} onClick={() => onSelect(row)}><div><span className={`family-status ${assessment.status}`}>{deNovoLabel(assessment.status)}</span><strong>{row.gene}</strong><span>{row.hgvsP || row.hgvsC || cleanLabel(row.consequence)}</span></div><div className="family-locus"><VariantIdentifier row={row}/><span>popmax {compactNumber(row.gnomadPopmax)} · {row.impact}</span></div><TrioGenotypeSummary assessment={assessment}/><p>{assessment.reasons[0]}</p></button>)}</div> : <div className="empty-state compact"><span className="empty-icon"><Icon name="dna"/></span><h2>No de novo candidates</h2><p>Current variant and genotype thresholds produced no candidates.</p></div>}
      </section> : <section className="family-results">
        <div className="family-results-head"><div><p className="eyebrow">Both variants pass active filters</p><h2>Compound-heterozygous pairs</h2></div><Check label="Show cis pairs" checked={includeCis} onChange={setIncludeCis}/></div>
        {shownPairs.length ? <div className="compound-pair-list">{shownPairs.map((pair) => <article className={`compound-pair ${pair.phase}`} key={pair.key}><header><div><span className={`family-status ${pair.phase}`}>{compoundPhaseLabel(pair.phase)}</span><h3>{pair.gene}</h3></div><p>{pair.reason}</p></header><div className="compound-variants"><button onClick={() => onSelect(pair.first)}><strong>{pair.first.hgvsP || pair.first.hgvsC || variantIdentity(pair.first)}</strong><VariantIdentifier row={pair.first}/><small>{originLabel(pair.firstOrigin)} · {pair.first.impact} · popmax {compactNumber(pair.first.gnomadPopmax)}</small></button><span className="compound-link">+</span><button onClick={() => onSelect(pair.second)}><strong>{pair.second.hgvsP || pair.second.hgvsC || variantIdentity(pair.second)}</strong><VariantIdentifier row={pair.second}/><small>{originLabel(pair.secondOrigin)} · {pair.second.impact} · popmax {compactNumber(pair.second.gnomadPopmax)}</small></button></div></article>)}</div> : <div className="empty-state compact"><span className="empty-icon"><Icon name="dna"/></span><h2>No qualifying pairs</h2><p>Each member of a pair must independently pass the currently active variant filters.</p></div>}
      </section>}
      <div className="interpretation-banner"><strong>Candidate discovery only</strong><span>Family analysis does not confirm biological parentage, replace read review, or assign ACMG/AMP evidence codes.</span></div>
    </> : <div className="empty-state family-empty"><span className="empty-icon"><Icon name="dna"/></span><h2>Configure a trio to begin</h2><p>A jointly genotyped multi-sample VCF provides the strongest evidence. Separate ordinary VCFs may lack parental reference calls.</p></div>}
  </div>;
}

function FamilyThreshold({ label, value, step, onChange }: { label: string; value: number; step: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input type="number" min="0" step={step} value={value} onChange={(event) => Number.isFinite(Number(event.target.value)) && onChange(Number(event.target.value))}/></label>;
}

function TrioGenotypeSummary({ assessment }: { assessment: DeNovoAssessment }) {
  return <div className="trio-summary">{([
    ["Child", assessment.child],
    ["Mother", assessment.mother],
    ["Father", assessment.father],
  ] as const).map(([role, evidence]) => <span key={role}><small>{role}</small><strong className="mono">{evidence?.gt ?? "missing"}</strong><small>DP {evidence?.dp ?? "—"} · GQ {evidence?.gq ?? "—"} · AB {compactNumber(evidence?.alleleBalance ?? null)}</small></span>)}</div>;
}

function TrioGenotypeTable({ row, trio }: { row: VariantRow; trio: TrioDefinition }) {
  const genotypes = row.sampleGenotypes ?? {};
  const family = [
    ["Proband", trio.proband, genotypes[trio.proband]],
    ["Mother", trio.mother, genotypes[trio.mother]],
    ["Father", trio.father, genotypes[trio.father]],
  ] as const;
  return <div className="trio-table-wrap"><table className="trio-table"><thead><tr><th>Role / sample</th><th>GT</th><th>DP</th><th>GQ</th><th>REF, ALT depth</th><th>ALT balance</th><th>Phase set</th></tr></thead><tbody>{family.map(([role, sample, evidence]) => <tr key={role}><td><strong>{role}</strong><span>{sample}</span></td><td className="mono">{evidence?.gt ?? "missing"}</td><td>{evidence?.dp ?? "—"}</td><td>{evidence?.gq ?? "—"}</td><td>{evidence ? `${evidence.adRef ?? "—"}, ${evidence.adAlt ?? "—"}` : "—"}</td><td>{compactNumber(evidence?.alleleBalance ?? null)}</td><td>{evidence?.phaseSet || "—"}</td></tr>)}</tbody></table></div>;
}

function TrioGenotypeEvidence({ row, trio, assessment }: { row: VariantRow; trio: TrioDefinition; assessment: DeNovoAssessment | null }) {
  return <div className="trio-evidence-span"><EvidenceSection eyebrow="Family evidence" title="Trio genotypes"><TrioGenotypeTable row={row} trio={trio}/>{assessment && <div className={`trio-assessment ${assessment.status}`}><strong>{deNovoLabel(assessment.status)}</strong><span>{assessment.reasons.join(" ")}</span></div>}<p className="constraint-note">{trio.parentalRelationshipsConfirmed ? "Parental relationships are recorded as confirmed by the reviewer." : "Parental relationships are not recorded as confirmed."} This is a candidate-analysis aid and does not assign PS2/PM6.</p></EvidenceSection></div>;
}

function PhenotypeSummary({ sample }: { sample: string }) {
  const [records, setRecords] = useState<PhenotypeIndividual[]>([]);
  useEffect(() => {
    let active = true;
    getPhenotypesBySample(sample)
      .then((value) => { if (active) setRecords(value); })
      .catch(() => { if (active) setRecords([]); });
    return () => { active = false; };
  }, [sample]);
  if (!records.length) return null;
  return <EvidenceSection eyebrow="Individual context" title={records.map((record) => record.individual_id).join(", ")}>{records.map((record) => <div className="variant-phenotype-summary" key={record.individual_id}><EvidenceGrid items={[
    ["VCF sample", sample],
    ["Sex at birth", record.sex_at_birth || "—"],
    ["Age at evaluation", record.age_at_evaluation === null ? "—" : `${record.age_at_evaluation} ${record.age_at_evaluation_unit}`],
    ["Age at onset", record.age_at_onset === null ? "—" : `${record.age_at_onset} ${record.age_at_onset_unit}`],
    ["Reported race", record.reported_race.join(", ") || "—"],
    ["Reported ethnicity", record.reported_ethnicity.join(", ") || "—"],
    ["Current diagnosis", record.current_diagnosis || "—"],
    ["Source date", record.source_date || "—"],
  ]}/><p>{record.phenotype_summary || "No phenotype summary recorded."}</p>{record.present_features.length > 0 && <span><strong>Present:</strong> {record.present_features.join("; ")}</span>}{record.absent_features.length > 0 && <span><strong>Absent:</strong> {record.absent_features.join("; ")}</span>}</div>)}</EvidenceSection>;
}

function EvidenceSection({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return <section className="evidence-section"><p className="eyebrow">{eyebrow}</p><h2>{title}</h2>{children}</section>;
}

function EvidenceGrid({ items }: { items: [string, React.ReactNode][] }) {
  return <dl className="evidence-grid">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value === null || value === undefined || value === "" ? "—" : value}</dd></div>)}</dl>;
}

function RawVcfEvidencePanel({ evidence }: {
  evidence: NonNullable<VariantRow["rawVcfEvidence"]>;
}) {
  const groups = [
    ["Variant INFO", evidence.info],
    ["Selected VEP consequence", evidence.consequence],
    ["Sample FORMAT", evidence.format],
  ] as const;
  const fieldCount = groups.reduce(
    (total, [, fields]) => total + Object.keys(fields).length,
    0,
  );
  return <section className="raw-vcf-evidence"><details><summary><span><strong>All source VCF annotations</strong><small>Loaded on demand from the indexed source record</small></span><em>{fieldCount} populated fields</em></summary><div className="raw-vcf-groups">{groups.map(([label, fields]) => <section key={label}><h3>{label}</h3><dl>{Object.entries(fields).sort(([left], [right]) => left.localeCompare(right)).map(([field, value]) => <div key={field}><dt>{field}</dt><dd><code>{rawVcfDisplayValue(value)}</code></dd></div>)}</dl></section>)}</div></details></section>;
}

function rawVcfDisplayValue(value: string) {
  try {
    return decodeURIComponent(value.replaceAll("+", " "));
  } catch {
    return value;
  }
}

function cleanLabel(value: string | undefined) {
  return value ? value.replaceAll("_", " ").replaceAll("&", " / ") : "—";
}

function compactPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1).replace(/\.0$/, "")}%`;
}

function optionalNumber(value: string) {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const PHENOTYPE_MAPPING_FIELDS: [PhenotypeField, string, boolean][] = [
  ["individual_id", "Individual ID", true],
  ["sample_ids", "VCF sample ID(s)", false],
  ["sex_at_birth", "Sex at birth", false],
  ["age_at_evaluation", "Age at evaluation", false],
  ["age_at_evaluation_unit", "Age at evaluation unit", false],
  ["age_at_onset", "Age at onset", false],
  ["age_at_onset_unit", "Age at onset unit", false],
  ["reported_race", "Reported race", false],
  ["reported_ethnicity", "Reported ethnicity", false],
  ["phenotype_summary", "Phenotype summary", false],
  ["present_features", "Present features", false],
  ["absent_features", "Absent features", false],
  ["current_diagnosis", "Current diagnosis", false],
  ["notes", "Notes", false],
  ["source_date", "Source date", false],
];

const EMPTY_PHENOTYPE_FORM: Record<string, string> = {
  individual_id: "", sample_ids: "", sex_at_birth: "",
  age_at_evaluation: "", age_at_evaluation_unit: "years",
  age_at_onset: "", age_at_onset_unit: "years",
  reported_race: "", reported_ethnicity: "", phenotype_summary: "",
  present_features: "", absent_features: "", current_diagnosis: "",
  notes: "", source_date: "",
};

async function fileAsBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 32768;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

function phenotypeToForm(record: PhenotypeIndividual): Record<string, string> {
  return {
    individual_id: record.individual_id,
    sample_ids: record.sample_ids.join("; "),
    sex_at_birth: record.sex_at_birth || "",
    age_at_evaluation: record.age_at_evaluation?.toString() ?? "",
    age_at_evaluation_unit: record.age_at_evaluation_unit || "years",
    age_at_onset: record.age_at_onset?.toString() ?? "",
    age_at_onset_unit: record.age_at_onset_unit || "years",
    reported_race: record.reported_race.join("; "),
    reported_ethnicity: record.reported_ethnicity.join("; "),
    phenotype_summary: record.phenotype_summary || "",
    present_features: record.present_features.join("; "),
    absent_features: record.absent_features.join("; "),
    current_diagnosis: record.current_diagnosis || "",
    notes: record.notes || "",
    source_date: record.source_date || "",
  };
}

function PhenotypePanel() {
  const [tab, setTab] = useState<"records" | "manual" | "bulk">("records");
  const [stats, setStats] = useState<PhenotypeStats | null>(null);
  const [individuals, setIndividuals] = useState<PhenotypeIndividual[]>([]);
  const [profiles, setProfiles] = useState<PhenotypeProfile[]>([]);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState<Record<string, string>>({ ...EMPTY_PHENOTYPE_FORM });
  const [upload, setUpload] = useState<{ filename: string; content_base64: string } | null>(null);
  const [preview, setPreview] = useState<PhenotypePreview | null>(null);
  const [mapping, setMapping] = useState<Partial<Record<PhenotypeField, string>>>({});
  const [validation, setValidation] = useState<PhenotypeValidation | null>(null);
  const [headerRow, setHeaderRow] = useState(1);
  const [sheet, setSheet] = useState<string | null>(null);
  const [profileName, setProfileName] = useState("");
  const [updateMode, setUpdateMode] = useState<"update_nonblank" | "replace" | "skip_existing">("update_nonblank");
  const [preserveUnmapped, setPreserveUnmapped] = useState(true);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function refresh(query = search) {
    const [nextStats, nextIndividuals, nextProfiles] = await Promise.all([
      getPhenotypeStats(), getPhenotypeIndividuals(query), getPhenotypeProfiles(),
    ]);
    setStats(nextStats);
    setIndividuals(nextIndividuals);
    setProfiles(nextProfiles);
  }

  useEffect(() => {
    // Initial service hydration intentionally populates component state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh("").catch(() => setError("The local phenotype service is not running. Start the workbench to manage individual data."));
    // This page owns refreshes after later mutations; initial load runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function field(name: string, label: string, input: React.ReactNode) {
    return <label className="phenotype-field" key={name}><span>{label}</span>{input}</label>;
  }

  async function saveManual() {
    if (!form.individual_id.trim()) {
      setError("Individual ID is required.");
      return;
    }
    setWorking(true); setError(""); setMessage("");
    try {
      await savePhenotypeIndividual({ ...form, individual_id: form.individual_id.trim() });
      setMessage(`Saved ${form.individual_id.trim()}.`);
      setForm({ ...EMPTY_PHENOTYPE_FORM });
      await refresh("");
      setTab("records");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the individual.");
    } finally {
      setWorking(false);
    }
  }

  async function loadPreview(
    nextUpload: { filename: string; content_base64: string },
    nextSheet?: string | null,
    nextHeader?: number,
  ) {
    setWorking(true); setError(""); setMessage(""); setValidation(null);
    try {
      const value = await previewPhenotypeInput({
        ...nextUpload,
        sheet: nextSheet,
        header_row: nextHeader,
      });
      setPreview(value);
      setSheet(value.selected_sheet);
      setHeaderRow(value.header_row);
      setMapping(value.suggested_mapping);
      setTab("bulk");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not preview the phenotype input.");
    } finally {
      setWorking(false);
    }
  }

  async function chooseFile(file: File) {
    if (file.size > 20 * 1024 * 1024) {
      setError("Phenotype input must be 20 MB or smaller.");
      return;
    }
    const nextUpload = { filename: file.name, content_base64: await fileAsBase64(file) };
    setUpload(nextUpload);
    setProfileName("");
    await loadPreview(nextUpload);
    if (fileRef.current) fileRef.current.value = "";
  }

  function uploadPayload() {
    if (!upload) throw new Error("Choose a phenotype file first.");
    return {
      ...upload, sheet, header_row: headerRow, mapping,
      preserve_unmapped: preserveUnmapped, update_mode: updateMode,
      profile_name: profileName.trim(),
    };
  }

  async function validateUpload() {
    setWorking(true); setError(""); setMessage("");
    try {
      setValidation(await validatePhenotypeInput(uploadPayload()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not validate the phenotype input.");
    } finally {
      setWorking(false);
    }
  }

  async function importUpload() {
    if (!validation) {
      setError("Validate the sample and individual mappings before import.");
      return;
    }
    if (validation.duplicate_individual_ids.length || Object.keys(validation.ambiguous_sample_ids).length) {
      setError("Resolve duplicate individual IDs and ambiguous sample IDs before import.");
      return;
    }
    setWorking(true); setError(""); setMessage("");
    try {
      const result = await importPhenotypeInput(uploadPayload());
      setMessage(`${result.created} individuals created · ${result.updated} updated · ${result.skipped} skipped.`);
      setStats(result.stats);
      setUpload(null); setPreview(null); setValidation(null); setMapping({});
      await refresh("");
      setTab("records");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Phenotype import failed.");
    } finally {
      setWorking(false);
    }
  }

  function applyProfile(name: string) {
    setProfileName(name);
    const profile = profiles.find((item) => item.name === name);
    if (!profile || !preview) return;
    setMapping(Object.fromEntries(
      Object.entries(profile.mapping).filter(([, column]) => preview.columns.includes(column!)),
    ));
    setValidation(null);
  }

  return <div className="phenotype-page">
    <div className="content-header"><div><p className="eyebrow">Individual context</p><h1>Demographics & phenotypes</h1><p className="subtitle">Local individual records linked to one or more VCF sample IDs. No HPO inference or phenotype-based variant ranking is applied.</p></div><button className="primary-button dark" onClick={() => { setForm({ ...EMPTY_PHENOTYPE_FORM }); setTab("manual"); }}>Add individual</button></div>
    <div className="phenotype-stats"><Stat value={stats?.individuals ?? 0} label="individual records"/><Stat value={stats?.sample_links ?? 0} label="sample links"/><Stat value={stats?.matched_samples ?? 0} label="matched VCF samples"/><Stat value={stats?.imports ?? 0} label="bulk imports"/></div>
    <div className="phenotype-tabs"><button className={tab === "records" ? "active" : ""} onClick={() => setTab("records")}>Individuals</button><button className={tab === "manual" ? "active" : ""} onClick={() => setTab("manual")}>Manual entry</button><button className={tab === "bulk" ? "active" : ""} onClick={() => setTab("bulk")}>Spreadsheet import</button></div>
    {error && <div className="alert error phenotype-alert">{error}</div>}
    {message && <div className="alert phenotype-alert">{message}</div>}

    {tab === "records" && <section className="phenotype-card">
      <div className="phenotype-record-toolbar"><label className="search"><Icon name="search"/><input value={search} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => event.key === "Enter" && refresh()} placeholder="Individual, sample, diagnosis, or phenotype…"/></label><button className="secondary-button" onClick={() => refresh()}>Search</button></div>
      <div className="individual-grid">{individuals.map((record) => <button key={record.individual_id} className="individual-card" onClick={() => { setForm(phenotypeToForm(record)); setTab("manual"); }}><div><strong>{record.individual_id}</strong><span>{record.sample_ids.join(", ") || "No VCF sample linked"}</span></div><span>{record.current_diagnosis || record.phenotype_summary || "No clinical summary recorded"}</span><div className="individual-tags">{record.sex_at_birth && <small>{record.sex_at_birth}</small>}{record.reported_race.map((value) => <small key={`r-${value}`}>Race: {value}</small>)}{record.reported_ethnicity.map((value) => <small key={`e-${value}`}>Ethnicity: {value}</small>)}</div></button>)}</div>
      {!individuals.length && <div className="empty-state compact"><span className="empty-icon"><Icon name="file"/></span><h2>No individual records found</h2><p>Add one manually or import a CSV, TSV, or XLSX workbook.</p></div>}
    </section>}

    {tab === "manual" && <section className="phenotype-card">
      <div className="phenotype-card-head"><div><p className="eyebrow">Manual record</p><h2>{form.individual_id ? `Edit ${form.individual_id}` : "Add an individual"}</h2></div><span className="local-only-badge">Stored locally</span></div>
      <p className="phenotype-guidance">Use a stable local individual ID. Separate multiple samples, race/ethnicity values, or features with semicolons. Reported race and ethnicity are not treated as genetic ancestry.</p>
      <div className="phenotype-form-grid">
        {field("individual_id", "Individual ID *", <input value={form.individual_id} onChange={(event) => setForm({ ...form, individual_id: event.target.value })}/>)}
        {field("sample_ids", "VCF sample ID(s)", <input value={form.sample_ids} onChange={(event) => setForm({ ...form, sample_ids: event.target.value })} placeholder="SAMPLE01; SAMPLE01_RERUN"/>)}
        {field("sex_at_birth", "Sex at birth", <select value={form.sex_at_birth} onChange={(event) => setForm({ ...form, sex_at_birth: event.target.value })}><option value="">Not recorded</option><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option><option value="unknown">Unknown</option></select>)}
        {field("source_date", "Source date", <input type="date" value={form.source_date} onChange={(event) => setForm({ ...form, source_date: event.target.value })}/>)}
        <div className="age-pair">{field("age_at_evaluation", "Age at evaluation", <input type="number" min="0" max="130" step="0.1" value={form.age_at_evaluation} onChange={(event) => setForm({ ...form, age_at_evaluation: event.target.value })}/>)}{field("age_at_evaluation_unit", "Unit", <select value={form.age_at_evaluation_unit} onChange={(event) => setForm({ ...form, age_at_evaluation_unit: event.target.value })}><option>years</option><option>months</option><option>weeks</option><option>days</option></select>)}</div>
        <div className="age-pair">{field("age_at_onset", "Age at onset", <input type="number" min="0" max="130" step="0.1" value={form.age_at_onset} onChange={(event) => setForm({ ...form, age_at_onset: event.target.value })}/>)}{field("age_at_onset_unit", "Unit", <select value={form.age_at_onset_unit} onChange={(event) => setForm({ ...form, age_at_onset_unit: event.target.value })}><option>years</option><option>months</option><option>weeks</option><option>days</option></select>)}</div>
        {field("reported_race", "Reported race", <input value={form.reported_race} onChange={(event) => setForm({ ...form, reported_race: event.target.value })} placeholder="Self-described or not reported"/>)}
        {field("reported_ethnicity", "Reported ethnicity", <input value={form.reported_ethnicity} onChange={(event) => setForm({ ...form, reported_ethnicity: event.target.value })} placeholder="Self-described or not reported"/>)}
        {field("current_diagnosis", "Current diagnosis", <input value={form.current_diagnosis} onChange={(event) => setForm({ ...form, current_diagnosis: event.target.value })}/>)}
        {field("phenotype_summary", "Phenotype summary", <textarea rows={4} value={form.phenotype_summary} onChange={(event) => setForm({ ...form, phenotype_summary: event.target.value })}/>)}
        {field("present_features", "Present features", <textarea rows={3} value={form.present_features} onChange={(event) => setForm({ ...form, present_features: event.target.value })} placeholder="Recurrent infections; hypogammaglobulinemia"/>)}
        {field("absent_features", "Absent features / pertinent negatives", <textarea rows={3} value={form.absent_features} onChange={(event) => setForm({ ...form, absent_features: event.target.value })}/>)}
        {field("notes", "Notes", <textarea rows={3} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })}/>)}
      </div>
      <div className="phenotype-actions"><button className="secondary-button" onClick={() => { setForm({ ...EMPTY_PHENOTYPE_FORM }); setTab("records"); }}>Cancel</button><button className="primary-button dark" disabled={working} onClick={saveManual}>{working ? "Saving…" : "Save individual"}</button></div>
    </section>}

    {tab === "bulk" && <section className="phenotype-card">
      <div className="phenotype-card-head"><div><p className="eyebrow">Bulk intake</p><h2>Map a phenotype spreadsheet</h2></div><button className="secondary-button" onClick={() => fileRef.current?.click()}>Choose CSV, TSV, or XLSX</button></div>
      <input ref={fileRef} className="sr-only" type="file" accept=".csv,.tsv,.xlsx,text/csv,text/tab-separated-values,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => event.target.files?.[0] && chooseFile(event.target.files[0])}/>
      {!preview ? <button className="phenotype-upload-zone" onClick={() => fileRef.current?.click()}><Icon name="upload"/><strong>{working ? "Reading input…" : "Choose a phenotype table"}</strong><span>The file is sent only to the loopback service on this workstation.</span></button> : <>
        <div className="input-source-bar"><strong>{upload?.filename}</strong><span>{preview.row_count.toLocaleString()} data rows · header row {headerRow}{sheet ? ` · ${sheet}` : ""}</span></div>
        <div className="spreadsheet-options">
          {preview.sheet_names.length > 0 && <label><span>Excel sheet</span><select value={sheet ?? ""} onChange={(event) => upload && loadPreview(upload, event.target.value, headerRow)}>{preview.sheet_names.map((name) => <option key={name}>{name}</option>)}</select></label>}
          <label><span>Header row</span><input type="number" min="1" value={headerRow} onChange={(event) => { setHeaderRow(Number(event.target.value)); setValidation(null); }}/></label>
          <button className="secondary-button" onClick={() => upload && loadPreview(upload, sheet, headerRow)}>Refresh preview</button>
          <label><span>Saved mapping</span><select value={profiles.some((item) => item.name === profileName) ? profileName : ""} onChange={(event) => applyProfile(event.target.value)}><option value="">None</option>{profiles.map((profile) => <option key={profile.name}>{profile.name}</option>)}</select></label>
        </div>
        <div className="mapping-layout"><div><h3>Column mapping</h3><p>Mappings are suggested from the header but must be confirmed. The individual ID is required.</p><div className="mapping-grid">{PHENOTYPE_MAPPING_FIELDS.map(([key, label, required]) => <label key={key}><span>{label}{required ? " *" : ""}</span><select value={mapping[key] ?? ""} onChange={(event) => { setMapping({ ...mapping, [key]: event.target.value || undefined }); setValidation(null); }}><option value="">Not mapped</option>{preview.columns.map((column) => <option key={column}>{column}</option>)}</select></label>)}</div></div><div><h3>Data preview</h3><div className="phenotype-preview-table"><table><thead><tr>{preview.columns.slice(0, 8).map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.rows.slice(0, 8).map((row, index) => <tr key={index}>{preview.columns.slice(0, 8).map((column) => <td key={column}>{row[column]}</td>)}</tr>)}</tbody></table></div></div></div>
        <div className="import-options"><label><span>Existing individual</span><select value={updateMode} onChange={(event) => setUpdateMode(event.target.value as typeof updateMode)}><option value="update_nonblank">Update nonblank fields</option><option value="replace">Replace record</option><option value="skip_existing">Leave unchanged</option></select></label><label><span>Save mapping profile as</span><input value={profileName} onChange={(event) => setProfileName(event.target.value)} placeholder="Optional, e.g. LIMS export"/></label><Check label="Preserve unmapped columns as custom fields" checked={preserveUnmapped} onChange={setPreserveUnmapped}/><button className="secondary-button" disabled={working} onClick={validateUpload}>{working ? "Validating…" : "Validate mapping"}</button></div>
        {validation && <div className="validation-summary"><strong>{validation.valid_individuals} individuals ready</strong><span className={validation.matched_sample_ids.length ? "ok" : ""}>{validation.matched_sample_ids.length} exact VCF sample matches</span><span className={validation.unmatched_sample_ids.length ? "warn" : ""}>{validation.unmatched_sample_ids.length} unmatched sample IDs</span><span className={validation.duplicate_individual_ids.length ? "bad" : ""}>{validation.duplicate_individual_ids.length} duplicate individual IDs</span><span className={Object.keys(validation.ambiguous_sample_ids).length ? "bad" : ""}>{Object.keys(validation.ambiguous_sample_ids).length} ambiguous sample IDs</span>{validation.unmatched_sample_ids.length > 0 && <p>Unmatched links are preserved and may match a VCF imported later: {validation.unmatched_sample_ids.slice(0, 10).join(", ")}{validation.unmatched_sample_ids.length > 10 ? "…" : ""}</p>}{Object.keys(validation.case_insensitive_suggestions).length > 0 && <p>Case-sensitive corrections suggested: {Object.entries(validation.case_insensitive_suggestions).map(([from, to]) => `${from} → ${to}`).join(", ")}</p>}</div>}
        <div className="phenotype-actions"><button className="secondary-button" onClick={() => { setUpload(null); setPreview(null); setValidation(null); }}>Clear</button><button className="primary-button dark" disabled={working || !validation || validation.duplicate_individual_ids.length > 0 || Object.keys(validation.ambiguous_sample_ids).length > 0} onClick={importUpload}>{working ? "Importing…" : "Import individuals"}</button></div>
      </>}
    </section>}
  </div>;
}

function CohortPanel({ onReview }: {
  onReview: (rows: VariantRow[], summary: ImportSummary) => void;
}) {
  const [stats, setStats] = useState<CohortStats | null>(null);
  const [sourcePaths, setSourcePaths] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [allowUnknownAssembly, setAllowUnknownAssembly] = useState(false);
  const [importProfile, setImportProfile] = useState<"full" | "prefiltered">("full");
  const [prefilter, setPrefilter] = useState<WgsPrefilterOptions>({ ...DEFAULT_WGS_PREFILTER });
  const [indexing, setIndexing] = useState(false);
  const [importResult, setImportResult] = useState<CohortImportResult | null>(null);
  const [importJob, setImportJob] = useState<CohortImportJob | null>(null);
  const [mode, setMode] = useState<"variant" | "gene">("variant");
  const [exactQuery, setExactQuery] = useState("");
  const [gene, setGene] = useState("");
  const [impacts, setImpacts] = useState<Set<string>>(new Set(["HIGH", "MODERATE"]));
  const [maxPopmax, setMaxPopmax] = useState("0.01");
  const [minCadd, setMinCadd] = useState("");
  const [minAlpha, setMinAlpha] = useState("");
  const [minSplice, setMinSplice] = useState("");
  const [loGoFuncClass, setLoGoFuncClass] = useState<"" | "GOF" | "LOF" | "Neutral">("");
  const [minLoGoFunc, setMinLoGoFunc] = useState("");
  const [clinvarOnly, setClinvarOnly] = useState(false);
  const [clinvarConflictOnly, setClinvarConflictOnly] = useState(false);
  const [excludeConfirmedFrameRestored, setExcludeConfirmedFrameRestored] = useState(false);
  const [maneOnly, setManeOnly] = useState(true);
  const [excludeRepeat, setExcludeRepeat] = useState(true);
  const [excludeSegdup, setExcludeSegdup] = useState(true);
  const [zygosity, setZygosity] = useState<"all" | "heterozygous" | "homozygous" | "hemizygous">("all");
  const [queryResult, setQueryResult] = useState<CohortQueryResult | null>(null);
  const [selectedVariant, setSelectedVariant] = useState<CohortQueryRow | null>(null);
  const [variantDetail, setVariantDetail] = useState<CohortVariantDetail | null>(null);
  const [detailWorking, setDetailWorking] = useState(false);
  const [reviewWorking, setReviewWorking] = useState(false);
  const [detailCarrierIds, setDetailCarrierIds] = useState<Set<number>>(new Set());
  const [selectedCarrierIds, setSelectedCarrierIds] = useState<Set<number>>(new Set());
  const [managingSamples, setManagingSamples] = useState(false);
  const [cohortSamples, setCohortSamples] = useState<CohortSample[]>([]);
  const [sampleQuery, setSampleQuery] = useState("");
  const [selectedSampleIds, setSelectedSampleIds] = useState<Set<number>>(new Set());
  const [sampleWorking, setSampleWorking] = useState(false);
  const [sampleMessage, setSampleMessage] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    getCohortStats().then((value) => {
      if (active) { setStats(value); setError(""); }
    }).catch(() => {
      if (active) setError("The local cohort service is not running. Start the workbench to index or query VCFs.");
    });
    return () => { active = false; };
  }, []);

  async function loadSamples(query = sampleQuery) {
    setSampleWorking(true);
    try {
      setCohortSamples(await getCohortSamples(query));
      setSelectedSampleIds(new Set());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Cohort samples could not be loaded.");
    } finally {
      setSampleWorking(false);
    }
  }

  async function removeSelectedSamples() {
    if (!selectedSampleIds.size) return;
    const selectedEntries = cohortSamples.filter((sample) => selectedSampleIds.has(sample.id));
    const names = selectedEntries.slice(0, 5).map((sample) => sample.name).join(", ");
    const more = selectedEntries.length > 5 ? ` and ${selectedEntries.length - 5} more` : "";
    if (!window.confirm(`Remove ${selectedEntries.length} indexed sample entr${selectedEntries.length === 1 ? "y" : "ies"} (${names}${more})? This removes their cohort genotypes; reimporting the source VCF restores them.`)) return;
    setSampleWorking(true);
    setError("");
    try {
      const result = await removeCohortSamples([...selectedSampleIds]);
      setStats(result.stats);
      setSampleMessage(`${result.removed_count} sample entr${result.removed_count === 1 ? "y" : "ies"} removed · ${result.orphan_variants_removed.toLocaleString()} orphan variants reclaimed.`);
      setQueryResult(null);
      setSelectedVariant(null);
      setVariantDetail(null);
      setSelectedCarrierIds(new Set());
      await loadSamples(sampleQuery);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Selected samples could not be removed.");
      setSampleWorking(false);
    }
  }

  async function indexSources() {
    const paths = sourcePaths.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (!paths.length) {
      setError("Enter at least one annotated VCF or directory path.");
      return;
    }
    setIndexing(true);
    setError("");
    setImportResult(null);
    try {
      let job = await startCohortImport(
        paths, recursive, false, allowUnknownAssembly, importProfile,
        importProfile === "prefiltered" ? prefilter : undefined,
      );
      setImportJob(job);
      while (job.status === "queued" || job.status === "running") {
        await new Promise((resolve) => window.setTimeout(resolve, 700));
        job = await getCohortImportJob(job.id);
        setImportJob(job);
      }
      if (job.status === "failed") {
        throw new Error(job.error || "Cohort indexing failed.");
      }
      if (!job.result) {
        throw new Error("Cohort import completed without a result.");
      }
      const result = job.result;
      setImportResult(result);
      setStats(result.stats);
      if (managingSamples) await loadSamples();
      if (result.failed) setError(`${result.failed} VCF${result.failed === 1 ? "" : "s"} could not be indexed. See the file results below.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Cohort indexing failed.");
    } finally {
      setIndexing(false);
    }
  }

  async function searchCohort() {
    if (mode === "variant" && !exactQuery.trim()) {
      setError("Enter a variant such as 4:1004329:C:T or an rsID.");
      return;
    }
    if (mode === "gene" && !gene.trim()) {
      setError("Enter a gene symbol.");
      return;
    }
    setWorking(true);
    setError("");
    try {
      const result = await queryCohort(mode === "variant" ? {
        mode,
        query: exactQuery.trim(),
        zygosity,
        limit: 1000,
      } : {
        mode,
        gene: gene.trim().toUpperCase(),
        impacts: [...impacts],
        max_popmax: optionalNumber(maxPopmax),
        min_cadd: optionalNumber(minCadd),
        min_alpha_missense: optionalNumber(minAlpha),
        min_spliceai: optionalNumber(minSplice),
        logofunc_class: loGoFuncClass,
        min_logofunc_probability: loGoFuncClass ? optionalNumber(minLoGoFunc) : null,
        clinvar_pathogenic_only: clinvarOnly,
        clinvar_conflict_pathogenic_only: clinvarConflictOnly,
        exclude_confirmed_frame_restored: excludeConfirmedFrameRestored,
        mane_only: maneOnly,
        exclude_repeat: excludeRepeat,
        exclude_segdup: excludeSegdup,
        zygosity,
        limit: 1000,
      });
      setQueryResult(result);
      setSelectedVariant(null);
      setVariantDetail(null);
      setSelectedCarrierIds(new Set());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Cohort query failed.");
    } finally {
      setWorking(false);
    }
  }

  const importPercent = importJob?.total_bytes
    ? Math.min(100, (importJob.processed_bytes / importJob.total_bytes) * 100)
    : 0;
  const resultCarrierIds = new Set(
    queryResult?.rows.map((row) => row.sample_entry_id) ?? [],
  );
  const selectedReviewRows = queryResult?.rows.filter(
    (row) => selectedCarrierIds.has(row.sample_entry_id),
  ) ?? [];

  function toggleAllResultCarriers() {
    setSelectedCarrierIds(
      selectedCarrierIds.size === resultCarrierIds.size
        ? new Set()
        : new Set(resultCarrierIds),
    );
  }

  async function reviewRows(rows: CohortQueryRow[]) {
    if (!rows.length) return;
    setReviewWorking(true);
    setError("");
    try {
      const source = await getCohortReviewRecords(rows.map((row) => ({
        variant_key: row.variant_key,
        sample_entry_id: row.sample_entry_id,
      })));
      const parsedRows: VariantRow[] = [];
      const parsedPairs = new Set<string>();
      const parserWarnings: string[] = [];
      const intakeQc: ImportSummary["intakeQc"] = [];

      for (const sourceFile of source.files) {
        try {
          const parsed = await parseVcfFiles([
            new File([sourceFile.vcf], sourceFile.name, { type: "text/vcf" }),
          ], { retainRawAnnotations: true });
          parserWarnings.push(...parsed.summary.warnings);
          if (!intakeQc.length) intakeQc.push(...parsed.summary.intakeQc);
          const selections = new Map(sourceFile.selections.map((selection) => [
            `${selection.sample}\t${selection.variant_key}`,
            selection,
          ]));
          parsed.rows.forEach((row) => {
            const selection = selections.get(`${row.sample}\t${fullVariantId(row)}`);
            if (!selection) return;
            parsedPairs.add(`${selection.variant_key}\t${selection.sample_entry_id}`);
            parsedRows.push({ ...row, source: sourceFile.source_path });
          });
        } catch (reason) {
          parserWarnings.push(
            `${fileName(sourceFile.source_path)}: ${reason instanceof Error ? reason.message : "source VCF record could not be parsed"}`,
          );
        }
      }

      const fallback = rows
        .filter((row) => !parsedPairs.has(`${row.variant_key}\t${row.sample_entry_id}`))
        .map(cohortRowForReview);
      const reviewRows = [...parsedRows, ...fallback];
      if (!reviewRows.length) {
        throw new Error("No selected cohort records could be opened for review.");
      }
      const warnings = [
        ...(parsedRows.length ? [
          "Full source INFO, VEP CSQ, and sample FORMAT annotations were loaded on demand from indexed VCFs with tabix.",
        ] : []),
        ...source.warnings,
        ...parserWarnings,
      ];
      if (fallback.length) {
        warnings.push(
          `${fallback.length} selected carrier call${fallback.length === 1 ? "" : "s"} used the compact SQLite fallback because its indexed source record was unavailable.`,
        );
      }
      onReview(reviewRows, {
        files: new Set(rows.map((row) => row.source_path)).size,
        samples: new Set(rows.map((row) => row.sample)).size,
        passRecords: new Set(rows.map((row) => row.variant_key)).size,
        excludedNonPass: 0,
        rows: reviewRows.length,
        warnings,
        intakeQc,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Source annotations could not be loaded for review.");
    } finally {
      setReviewWorking(false);
    }
  }

  async function openVariantDetail(row: CohortQueryRow) {
    setSelectedVariant(row);
    setVariantDetail(null);
    setDetailCarrierIds(new Set([row.sample_entry_id]));
    setDetailWorking(true);
    try {
      const detail = await getCohortVariantDetail(row.variant_key);
      setVariantDetail(detail);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Variant details could not be loaded.");
    } finally {
      setDetailWorking(false);
    }
  }

  return <div className="cohort-page">
    <div className="content-header"><div><p className="eyebrow">Genotype-first discovery</p><h1>Search the local cohort</h1><p className="subtitle">Find every individual carrying an exact variant, or carriers of qualifying variants in a gene.</p></div><div className="cohort-header-actions">{queryResult?.rows.length ? <button className="secondary-button" onClick={() => downloadCohortTsv(queryResult.rows)}>Export carriers</button> : null}<button className="secondary-button" onClick={() => { const opening = !managingSamples; setManagingSamples(opening); setSampleMessage(""); if (opening) void loadSamples(""); }}>{managingSamples ? "Close sample manager" : "Manage samples"}</button></div></div>

    <div className="cohort-stats">
      <Stat value={stats?.individuals ?? 0} label="individuals"/>
      <Stat value={stats?.files ?? 0} label="indexed VCFs"/>
      <Stat value={stats?.variants ?? 0} label="unique variants"/>
      <Stat value={stats?.carrier_observations ?? 0} label="carrier calls"/>
    </div>
    {stats && <div className={`cohort-profile-banner ${stats.prefiltered_files ? "prefiltered" : "full"}`}><strong>{stats.prefiltered_files ? stats.full_files ? "Mixed cohort index" : "Compact clinical cohort index" : "Full cohort index"}</strong><span>{stats.full_files.toLocaleString()} full file{stats.full_files === 1 ? "" : "s"} · {stats.prefiltered_files.toLocaleString()} prefiltered file{stats.prefiltered_files === 1 ? "" : "s"}{stats.prefiltered_files ? " · absent noncoding variants may have been excluded during import" : " · exact searches are exhaustive for indexed PASS carrier calls"}</span></div>}

    {managingSamples && <section className="cohort-sample-manager">
      <div className="cohort-results-head"><div><p className="eyebrow">Indexed entries</p><h2>Manage cohort samples</h2></div><span>Removal is recoverable by reimporting the source VCF.</span></div>
      <div className="sample-manager-toolbar"><label className="search"><Icon name="search"/><input value={sampleQuery} onChange={(event) => setSampleQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void loadSamples()} placeholder="Find sample ID…"/></label><button className="secondary-button" disabled={sampleWorking} onClick={() => void loadSamples()}>{sampleWorking ? "Loading…" : "Search"}</button><button className="secondary-button" disabled={!cohortSamples.length} onClick={() => setSelectedSampleIds(selectedSampleIds.size === cohortSamples.length ? new Set() : new Set(cohortSamples.map((sample) => sample.id)))}>{selectedSampleIds.size === cohortSamples.length && cohortSamples.length ? "Clear all" : "Select all shown"}</button><button className="danger-button" disabled={sampleWorking || indexing || !selectedSampleIds.size} onClick={() => void removeSelectedSamples()}>Remove selected ({selectedSampleIds.size})</button></div>
      {sampleMessage && <div className="alert success">{sampleMessage}</div>}
      {cohortSamples.length ? <div className="cohort-sample-list">{cohortSamples.map((sample) => <label key={sample.id}><input type="checkbox" checked={selectedSampleIds.has(sample.id)} onChange={(event) => setSelectedSampleIds((current) => toggleSet(current, sample.id, event.target.checked))}/><span><strong>{sample.name}</strong><small title={sample.source_path}>{fileName(sample.source_path)} · {sample.carrier_observations.toLocaleString()} carrier calls</small></span><em className={sample.import_profile}>{sample.import_profile === "prefiltered" ? "Compact" : "Full"}</em></label>)}</div> : !sampleWorking && <div className="empty-state compact"><h2>No indexed samples</h2><p>Add an annotated VCF to populate the cohort.</p></div>}
    </section>}

    <div className="cohort-control-grid">
      <section className="cohort-card">
        <div className="cohort-card-head"><div><p className="eyebrow">Cohort data</p><h2>Index annotated VCFs</h2></div><span className="local-only-badge">SQLite · local only</span></div>
        <p>Enter GRCh38 VEP-annotated <span className="mono">.vcf</span> or <span className="mono">.vcf.gz</span> files. The local SQLite index retains non-reference carriers rather than every reference call, allowing hundreds of samples when workstation memory and disk are adequate. Lifted GRCh37 calls retain their original locus.</p>
        <div className="query-mode cohort-import-profile" role="group" aria-label="Cohort import profile"><button className={importProfile === "full" ? "active" : ""} onClick={() => setImportProfile("full")}><strong>Full index</strong><span>Exhaustive carrier search</span></button><button className={importProfile === "prefiltered" ? "active" : ""} onClick={() => setImportProfile("prefiltered")}><strong>Compact WGS</strong><span>Clinical prefilter first</span></button></div>
        {importProfile === "prefiltered" && <div className="cohort-prefilter-settings"><div className="cohort-prefilter-note"><strong>Compact WGS candidate import</strong><span>PASS/QC and population frequency are required first. Coding/essential-splice, SpliceAI, promoterAI, and the selected noncoding region route are then combined with OR. Unscored intronic/promoter indels are retained with a review flag.</span></div><div className="cohort-prefilter-grid"><label className="form-field"><span>gnomAD popmax ≤</span><input inputMode="decimal" value={prefilter.max_gnomad_popmax ?? ""} onChange={(event) => setPrefilter({ ...prefilter, max_gnomad_popmax: optionalNumber(event.target.value) })}/></label><label className="form-field"><span>SpliceAI ≥</span><input inputMode="decimal" value={prefilter.min_spliceai ?? ""} onChange={(event) => setPrefilter({ ...prefilter, min_spliceai: optionalNumber(event.target.value) })}/></label><label className="form-field"><span>|promoterAI| ≥</span><input inputMode="decimal" value={prefilter.min_promoterai_abs ?? ""} onChange={(event) => setPrefilter({ ...prefilter, min_promoterai_abs: optionalNumber(event.target.value) })}/></label></div><NoncodingModePicker value={prefilter.noncoding_mode} onChange={(noncoding_mode) => setPrefilter({ ...prefilter, noncoding_mode })}/><p className="wgs-filter-logic"><strong>Logic:</strong> PASS/QC AND (popmax ≤ threshold OR popmax unavailable) AND (exonic/essential-splice OR SpliceAI OR promoterAI OR selected noncoding regions OR explicitly flagged unscored intronic/promoter indel).</p></div>}
        <label className="form-field"><span>VCF file or directory path(s)</span><textarea rows={4} value={sourcePaths} onChange={(event) => setSourcePaths(event.target.value)} placeholder={"/absolute/path/annotated-vcfs\n/absolute/path/cohort.vcf.gz"} /></label>
        <div className="cohort-actions"><div><Check label="Search subdirectories" checked={recursive} onChange={setRecursive}/><Check label="I confirm header-ambiguous VCFs are GRCh38" checked={allowUnknownAssembly} onChange={setAllowUnknownAssembly}/></div><button className="primary-button dark" disabled={indexing} onClick={indexSources}>{indexing ? "Indexing VCFs…" : "Add or refresh cohort"}</button></div>
        {importJob && (indexing || importJob.status === "failed") && <div className={`cohort-import-progress ${importJob.status}`}><div><strong>{importJob.status === "queued" ? "Preparing cohort import" : importJob.status === "failed" ? "Import stopped" : ["preparing_index", "filtering", "compressing", "indexing_output"].includes(importJob.phase) ? `Prefiltering ${fileName(importJob.current_path) || "WGS VCF"}` : importJob.phase === "merging" ? `Merging staged records for ${fileName(importJob.current_path) || "VCF"}` : `Indexing ${fileName(importJob.current_path) || "VCFs"}`}</strong><span>{importPercent.toFixed(1)}% · {importJob.completed_files}/{importJob.total_files} files · {(importJob.import_profile === "prefiltered" ? importJob.prefilter_records_scanned : importJob.records_processed).toLocaleString()} records scanned</span></div><div className="progress-track" role="progressbar" aria-label="Cohort VCF import progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(importPercent)}><span style={{ width: `${importPercent}%` }} /></div><small>{compactFileSize(importJob.processed_bytes)} of {compactFileSize(importJob.total_bytes)} · {importJob.import_profile === "prefiltered" ? `${importJob.prefilter_records_retained.toLocaleString()} records retained by prefilter · ` : ""}{importJob.pass_records.toLocaleString()} PASS records staged · {importJob.carrier_count.toLocaleString()} carrier calls · {importJob.reader_count} reader{importJob.reader_count === 1 ? "" : "s"}</small></div>}
        {importResult && <div className="cohort-import-result"><strong>{importResult.imported} indexed · {importResult.skipped} unchanged · {importResult.failed} failed</strong>{importResult.files.slice(0, 6).map((item) => <span key={item.path} className={item.status === "failed" ? "failed" : ""}>{fileName(item.path)} — {item.status}{item.import_profile === "prefiltered" ? ` · compact (${(item.prefilter_records_retained ?? 0).toLocaleString()} of ${(item.prefilter_records_scanned ?? 0).toLocaleString()} retained)` : " · full"}{item.reader_count ? ` · ${item.reader_count} reader${item.reader_count === 1 ? "" : "s"}` : ""}{item.cache_hit ? " · prepared cache reused" : ""}{item.preparation_warning ? ` · ${item.preparation_warning}` : ""}{item.error ? `: ${item.error}` : ""}</span>)}{importResult.files.length > 6 && <span>+ {importResult.files.length - 6} more files</span>}</div>}
      </section>

      <section className="cohort-card query-card">
        <div className="cohort-card-head"><div><p className="eyebrow">Carrier query</p><h2>Who carries it?</h2></div></div>
        <div className="query-mode" role="group" aria-label="Cohort query type"><button className={mode === "variant" ? "active" : ""} onClick={() => setMode("variant")}>Exact variant</button><button className={mode === "gene" ? "active" : ""} onClick={() => setMode("gene")}>Qualifying variants in gene</button></div>
        {mode === "variant" ? <><label className="form-field"><span>Variant, locus, or rsID</span><input value={exactQuery} onChange={(event) => setExactQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && searchCohort()} placeholder="4:1004329:C:T or rs121918472" /></label><p className="query-note">Exact searches return all indexed non-reference carriers; pathogenicity and frequency filters are not applied.{stats?.prefiltered_files ? " Compact-profile files may not contain noncoding variants excluded during import." : ""}</p></> : <>
          <label className="form-field"><span>Gene symbol</span><input value={gene} onChange={(event) => setGene(event.target.value.toUpperCase())} onKeyDown={(event) => event.key === "Enter" && searchCohort()} placeholder="NFKB1" /></label>
          <div className="qualifying-grid">
            <div><span className="qualifying-label">Impact</span><div className="chip-grid">{IMPACTS.map((impact) => <button key={impact} className={`impact-chip ${impact.toLowerCase()} ${impacts.has(impact) ? "selected" : ""}`} onClick={() => setImpacts((current) => toggleSet(current, impact, !current.has(impact)))}>{impact}</button>)}</div></div>
            <label className="form-field"><span>gnomAD popmax ≤</span><input value={maxPopmax} onChange={(event) => setMaxPopmax(event.target.value)} inputMode="decimal" /></label>
            <label className="form-field"><span>CADD ≥ <small>optional</small></span><input value={minCadd} onChange={(event) => setMinCadd(event.target.value)} inputMode="decimal" placeholder="—" /></label>
            <label className="form-field"><span>AlphaMissense ≥ <small>optional</small></span><input value={minAlpha} onChange={(event) => setMinAlpha(event.target.value)} inputMode="decimal" placeholder="—" /></label>
            <label className="form-field"><span>SpliceAI ≥ <small>optional</small></span><input value={minSplice} onChange={(event) => setMinSplice(event.target.value)} inputMode="decimal" placeholder="—" /></label>
            <label className="form-field"><span>LoGoFunc class <small>optional</small></span><select value={loGoFuncClass} onChange={(event) => setLoGoFuncClass(event.target.value as typeof loGoFuncClass)}><option value="">All / unavailable</option><option value="GOF">GOF</option><option value="LOF">LOF</option><option value="Neutral">Neutral</option></select></label>
            {loGoFuncClass && <label className="form-field"><span>{loGoFuncClass} probability ≥ <small>optional</small></span><input value={minLoGoFunc} onChange={(event) => setMinLoGoFunc(event.target.value)} inputMode="decimal" placeholder="—" /></label>}
          </div>
          {loGoFuncClass && <p className="query-note">LoGoFunc uses its exact source transcript and amino-acid substitution. Missing predictions do not pass this filter.</p>}
          <div className="qualifying-checks"><Check label="Clinical transcripts (MANE + PICK fallback)" checked={maneOnly} onChange={setManeOnly}/><Check label="Exclude RepeatMasker" checked={excludeRepeat} onChange={setExcludeRepeat}/><Check label="Exclude SegDup" checked={excludeSegdup} onChange={setExcludeSegdup}/><Check label="Hide confirmed frame-restored events" checked={excludeConfirmedFrameRestored} onChange={setExcludeConfirmedFrameRestored}/><Check label="ClinVar P / LP only" checked={clinvarOnly} onChange={(checked) => { setClinvarOnly(checked); if (checked) setClinvarConflictOnly(false); }}/><Check label="ClinVar conflict with ≥1 P / LP" checked={clinvarConflictOnly} onChange={(checked) => { setClinvarConflictOnly(checked); if (checked) setClinvarOnly(false); }}/></div>
        </>}
        <div className="cohort-query-footer"><label>Genotype<select value={zygosity} onChange={(event) => setZygosity(event.target.value as typeof zygosity)}><option value="all">All carriers</option><option value="heterozygous">Heterozygous</option><option value="homozygous">Homozygous</option><option value="hemizygous">Hemizygous</option></select></label><button className="primary-button dark" disabled={working} onClick={searchCohort}><Icon name="search" />{working ? "Searching…" : "Find carriers"}</button></div>
      </section>
    </div>

    {error && <div className="alert error cohort-alert">{error}</div>}
    {queryResult && <section className="cohort-results">
      <div className="cohort-results-head"><div><p className="eyebrow">Query results</p><h2>{queryResult.total.toLocaleString()} carrier call{queryResult.total === 1 ? "" : "s"}</h2></div><div className="cohort-result-actions"><span>{queryResult.individuals.toLocaleString()} individuals · {queryResult.variants.toLocaleString()} variants{queryResult.truncated ? ` · first ${queryResult.limit.toLocaleString()} shown` : ""}</span>{queryResult.rows.length > 0 && <><button className="secondary-button" disabled={reviewWorking} onClick={toggleAllResultCarriers}>{selectedCarrierIds.size === resultCarrierIds.size ? "Clear carriers" : "Select all carriers"}</button><button className="primary-button dark" disabled={reviewWorking || !selectedReviewRows.length} onClick={() => void reviewRows(selectedReviewRows)}>{reviewWorking ? "LOADING SOURCE ANNOTATIONS…" : `REVIEW SELECTED (${selectedCarrierIds.size})`}</button><button className="secondary-button" disabled={reviewWorking} onClick={() => void reviewRows(queryResult.rows)}>REVIEW ALL SHOWN</button></>}</div></div>
      {queryResult.rows.length ? <div className="cohort-table-scroll"><table className="cohort-table cohort-selectable-table"><thead><tr><th aria-label="Select carriers"></th><th>Individual</th><th>Variant</th><th>Gene / consequence</th><th>Genotype evidence</th><th>Annotations</th><th>Source</th></tr></thead><tbody>{queryResult.rows.map((row) => {
        const selectedRow = selectedVariant?.variant_key === row.variant_key && selectedVariant.sample_entry_id === row.sample_entry_id;
        return <tr key={`${row.variant_key}:${row.sample_entry_id}:${row.source_path}`} className={selectedRow ? "selected" : ""} tabIndex={0} onClick={() => void openVariantDetail(row)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") void openVariantDetail(row); }}><td><input aria-label={`Select ${row.sample} for review`} type="checkbox" checked={selectedCarrierIds.has(row.sample_entry_id)} onClick={(event) => event.stopPropagation()} onChange={(event) => setSelectedCarrierIds((current) => toggleSet(current, row.sample_entry_id, event.target.checked))}/></td><td><strong>{row.sample}</strong><span className="cell-sub">{row.zygosity.replaceAll("_", " ")}{queryResult.mode === "gene" && row.sample_qualifying_variant_count > 1 ? ` · ${row.sample_qualifying_variant_count} qualifying variants` : ""}</span></td><td><VariantIdentifier row={row}/>{row.rsid && <span className="cell-sub">{row.rsid}</span>}{row.original_assembly && <span className="cell-sub mono">GRCh37: {row.original_chrom}:{row.original_pos} {row.original_ref}›{row.original_alt}</span>}</td><td><strong>{row.gene}</strong><span className={`impact-pill ${row.impact.toLowerCase()}`}>{row.impact}</span><span className="cell-sub">{row.hgvsp || row.hgvsc || cleanLabel(row.consequence)} · {row.mane ? "MANE" : row.picked ? "PICK fallback" : "alternate transcript"}</span></td><td><strong className="mono">{row.genotype}</strong><span className="cell-sub">DP {row.dp ?? "—"} · GQ {compactNumber(row.gq, 1)} · AB {compactNumber(row.allele_balance)}</span>{row.haplotype_frame_status && <span className="cell-sub">{haplotypeFrameLabel(row.haplotype_frame_status)}</span>}</td><td><span className={isPathogenic(row.clinvar) || isClinvarConflictWithPathogenic(row.clinvar, row.clinvar_conflicting) ? "clinvar pathogenic" : "clinvar"}>{cleanLabel(row.clinvar)}</span><span className="cell-sub">popmax {compactNumber(row.gnomad_popmax)} · CADD {compactNumber(row.cadd, 1)} · AM {compactNumber(row.alpha_missense)} · SpliceAI {compactNumber(row.spliceai)} · promoterAI {compactNumber(row.promoterai)}{row.logofunc_prediction ? ` · LoGoFunc ${row.logofunc_prediction}` : ""}</span></td><td><span title={row.source_path}>{fileName(row.source_path)}</span><small className={`source-profile ${row.import_profile}`}>{row.import_profile === "prefiltered" ? "Compact" : "Full"}</small></td></tr>;
      })}</tbody></table></div> : <div className="empty-state compact"><span className="empty-icon"><Icon name="search" /></span><h2>No carriers found</h2><p>{mode === "variant" ? "Check the GRCh38 locus and alleles, or try the rsID." : "Relax one or more qualification filters."}</p></div>}
      {selectedVariant && <div className="cohort-variant-detail">
        <div className="cohort-detail-head"><div><p className="eyebrow">Variant details</p><h2><VariantIdentifier row={selectedVariant} full/> {selectedVariant.rsid ? <small>{selectedVariant.rsid}</small> : null}</h2></div><div><button className="primary-button dark" disabled={detailWorking || reviewWorking} onClick={() => void reviewRows(variantDetail?.rows ?? queryResult.rows.filter((row) => row.variant_key === selectedVariant.variant_key))}>{reviewWorking ? "LOADING SOURCE ANNOTATIONS…" : "REVIEW ALL CARRIERS OF THIS VARIANT"}</button><button className="secondary-button" onClick={() => { setSelectedVariant(null); setVariantDetail(null); }}>Close</button></div></div>
        {detailWorking && <div className="cohort-detail-loading">Loading every stored transcript and carrier…</div>}
        <div className="cohort-detail-grid">
          <section className="cohort-carrier-detail"><h3>Carriers {variantDetail ? `(${variantDetail.individuals})` : ""}</h3>{variantDetail ? <><div className="cohort-detail-carriers">{variantDetail.rows.map((carrier) => <label key={carrier.sample_entry_id}><input type="checkbox" checked={detailCarrierIds.has(carrier.sample_entry_id)} onChange={(event) => setDetailCarrierIds((current) => toggleSet(current, carrier.sample_entry_id, event.target.checked))}/><span><strong>{carrier.sample}</strong><small className="mono">{carrier.genotype} · DP {carrier.dp ?? "—"} · GQ {compactNumber(carrier.gq, 1)} · AB {compactNumber(carrier.allele_balance)}</small><small title={carrier.source_path}>{fileName(carrier.source_path)} · {carrier.import_profile === "prefiltered" ? "Compact" : "Full"}</small></span></label>)}</div><div className="cohort-detail-review-actions"><button className="secondary-button" disabled={reviewWorking} onClick={() => setDetailCarrierIds(detailCarrierIds.size === variantDetail.rows.length ? new Set() : new Set(variantDetail.rows.map((row) => row.sample_entry_id)))}>{detailCarrierIds.size === variantDetail.rows.length ? "Clear all" : "Select all"}</button><button className="primary-button dark" disabled={reviewWorking || !detailCarrierIds.size} onClick={() => void reviewRows(variantDetail.rows.filter((row) => detailCarrierIds.has(row.sample_entry_id)))}>{reviewWorking ? "LOADING…" : `REVIEW SELECTED (${detailCarrierIds.size})`}</button></div></> : <dl><div><dt>Sample</dt><dd>{selectedVariant.sample}</dd></div><div><dt>Genotype</dt><dd className="mono">{selectedVariant.genotype} · {cleanLabel(selectedVariant.zygosity)}</dd></div><div><dt>Evidence</dt><dd>DP {selectedVariant.dp ?? "—"} · GQ {compactNumber(selectedVariant.gq, 1)} · AB {compactNumber(selectedVariant.allele_balance)} · QUAL {compactNumber(selectedVariant.qual, 1)}</dd></div></dl>}</section>
          <section className="cohort-transcript-detail"><h3>Stored transcript annotations {variantDetail ? `(${variantDetail.annotations.length})` : ""}</h3>{variantDetail ? <div className="cohort-transcript-list">{variantDetail.annotations.map((annotation, index) => <article key={`${annotation.gene}:${annotation.transcript}:${annotation.consequence}:${index}`}><div><strong>{annotation.gene}</strong><span className={`impact-pill ${annotation.impact.toLowerCase()}`}>{annotation.impact}</span><em>{annotation.mane ? "MANE" : annotation.picked ? "PICK" : "alternate"}</em></div><span className="mono">{annotation.transcript || "—"}</span><span>{annotation.hgvsp || annotation.hgvsc || cleanLabel(annotation.consequence)}</span><small>{cleanLabel(annotation.consequence)} · popmax {compactNumber(annotation.gnomad_popmax)} · CADD {compactNumber(annotation.cadd, 1)} · SpliceAI {compactNumber(annotation.spliceai)} · promoterAI {compactNumber(annotation.promoterai)}{annotation.logofunc_prediction ? ` · LoGoFunc ${annotation.logofunc_prediction}` : ""}</small></article>)}</div> : <dl><div><dt>Gene</dt><dd>{selectedVariant.gene}</dd></div><div><dt>Transcript</dt><dd className="mono">{selectedVariant.transcript || "—"}</dd></div><div><dt>HGVS</dt><dd>{selectedVariant.hgvsp || selectedVariant.hgvsc || "—"}</dd></div></dl>}</section>
          <section><h3>Clinical and prediction evidence</h3><dl><div><dt>ClinVar</dt><dd>{cleanLabel(selectedVariant.clinvar)}{selectedVariant.clinvar_conflicting ? ` · ${cleanLabel(selectedVariant.clinvar_conflicting)}` : ""}</dd></div><div><dt>Scores</dt><dd>popmax {compactNumber(selectedVariant.gnomad_popmax)} · CADD {compactNumber(selectedVariant.cadd, 1)} · AlphaMissense {compactNumber(selectedVariant.alpha_missense)} · SpliceAI {compactNumber(selectedVariant.spliceai)} · promoterAI {compactNumber(selectedVariant.promoterai)}</dd></div><div><dt>LoGoFunc</dt><dd>{selectedVariant.logofunc_prediction || (selectedVariant.logofunc_allele_available ? "Allele available; transcript/protein mismatch" : "Not annotated")}{selectedVariant.logofunc_prediction ? ` · N ${compactNumber(selectedVariant.logofunc_neutral)} · GOF ${compactNumber(selectedVariant.logofunc_gof)} · LOF ${compactNumber(selectedVariant.logofunc_lof)}` : ""}</dd></div><div><dt>LOFTEE</dt><dd>{cleanLabel(selectedVariant.loftee)} · 50 bp {cleanLabel(selectedVariant.loftee_50bp)}{selectedVariant.ptc_distance !== null ? ` · PTC distance ${selectedVariant.ptc_distance}` : ""}</dd></div><div><dt>Regions</dt><dd>{selectedVariant.repeat_masker ? "RepeatMasker" : "Not RepeatMasker"} · {selectedVariant.segdup ? "SegDup" : "Not SegDup"}</dd></div></dl></section>
          <section><h3>Source and coordinates</h3><dl><div><dt>GRCh38</dt><dd className="mono"><VariantIdentifier row={selectedVariant} full/></dd></div>{selectedVariant.original_assembly && <div><dt>Original</dt><dd className="mono">{selectedVariant.original_assembly}: {selectedVariant.original_chrom}:{selectedVariant.original_pos} {selectedVariant.original_ref}›{selectedVariant.original_alt}</dd></div>}<div><dt>Selected source</dt><dd title={selectedVariant.source_path}>{selectedVariant.source_path}</dd></div><div><dt>Index profile</dt><dd>{selectedVariant.import_profile === "prefiltered" ? "Compact clinical WGS" : "Full"}</dd></div></dl></section>
        </div>
        <CcreContextPanel compact chrom={selectedVariant.chrom} pos={selectedVariant.pos} ref={selectedVariant.ref} alt={selectedVariant.alt}/>
        {selectedVariant.haplotype_frame_status && <div className="cohort-haplotype-detail"><strong>{haplotypeFrameLabel(selectedVariant.haplotype_frame_status)}</strong><span>{selectedVariant.haplotype_protein_change || "No combined protein consequence stored"}{selectedVariant.haplotype_frame_partners ? ` · partners ${selectedVariant.haplotype_frame_partners}` : ""}</span></div>}
      </div>}
    </section>}
  </div>;
}

function GeneListsPanel({
  geneSets,
  bundledGeneSets,
  setGeneSet,
  resetGeneSet,
  lofConstrainedGenes,
  customLists,
  setCustomLists,
  referenceManifest,
  referenceError,
}: {
  geneSets: BundledGeneSets;
  bundledGeneSets: BundledGeneSets;
  setGeneSet: (key: BuiltInGeneSetKey, genes: Set<string>) => void;
  resetGeneSet: (key: BuiltInGeneSetKey) => void;
  lofConstrainedGenes: Set<string>;
  customLists: CustomGeneList[];
  setCustomLists: (lists: CustomGeneList[]) => void;
  referenceManifest: ReferenceManifest | null;
  referenceError: string;
}) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newGenes, setNewGenes] = useState("");

  function addCustomList(name: string, genes: Set<string>) {
    setCustomLists([
      ...customLists,
      {
        id: customGeneListId(),
        name: name.trim() || `Custom list ${customLists.length + 1}`,
        genes,
      },
    ]);
  }

  function createTypedList() {
    addCustomList(newName, parseGeneList(newGenes));
    setCreating(false);
    setNewName("");
    setNewGenes("");
  }

  async function uploadNewList(file: File) {
    const name = file.name.replace(/\.(?:txt|csv|tsv)$/i, "").replaceAll("_", " ");
    addCustomList(name, parseGeneList(await file.text()));
  }

  return <div className="gene-lists-page">
    <div className="content-header"><div><p className="eyebrow">Local review resources</p><h1>Gene lists</h1><p className="subtitle">Manage bundled diagnostic sets and create multiple named lab or project lists. Changes remain on this workstation.</p></div></div>

    <section className="gene-lists-section">
      <div className="gene-lists-section-head"><div><p className="eyebrow">Software defaults</p><h2>Built-in gene sets</h2><p>These lists are available as independent filters. Editing creates a local override; the bundled version can always be restored.</p></div></div>
      {referenceError && <div className="alert error">{referenceError}</div>}
      {referenceManifest && <p className="resource-release">IUIS {referenceManifest.iuis.release}: {referenceManifest.iuis.unique_genes} IEI genes, {referenceManifest.iuis.dominant_genes} dominant · Curated haploinsufficiency {referenceManifest.haploinsufficiency.release}: {referenceManifest.haploinsufficiency.genes} genes · gnomAD constraint v{referenceManifest.gnomad.release}</p>}
      <div className="built-in-gene-lists">
        <GeneSet label="IUIS 2024 IEI" genes={geneSets.iei} defaultGenes={bundledGeneSets.iei} detail="bundled default" setter={(genes) => setGeneSet("iei", genes)} reset={() => resetGeneSet("iei")}/>
        <GeneSet label="IEI haploinsufficiency" genes={geneSets.hi} defaultGenes={bundledGeneSets.hi} detail="manually curated default" setter={(genes) => setGeneSet("hi", genes)} reset={() => resetGeneSet("hi")}/>
        <GeneSet label="IEI dominant" genes={geneSets.dominant} defaultGenes={bundledGeneSets.dominant} detail="bundled from AD inheritance" setter={(genes) => setGeneSet("dominant", genes)} reset={() => resetGeneSet("dominant")}/>
        <div className="derived-gene-set"><span className="file-badge"><Icon name="dna" /></span><div><strong>LoF constrained</strong><span>{lofConstrainedGenes.size.toLocaleString()} genes · derived from bundled gnomAD constraint</span><small>pLI ≥ 0.9 or LOEUF &lt; 0.6. This is a sensitive prioritization filter, not evidence that a particular variant is pathogenic.</small></div><span className="read-only-badge">Automatic</span></div>
      </div>
    </section>

    <section className="gene-lists-section custom-gene-lists-section">
      <div className="gene-lists-section-head"><div><p className="eyebrow">Lab and project sets</p><h2>Custom gene lists</h2><p>Each list appears separately under the variant filters. When several custom lists are selected, their genes are combined as a union.</p></div><div><button className="secondary-button" onClick={() => uploadRef.current?.click()}>Upload list</button><button className="primary-button dark" onClick={() => setCreating(true)}>Create list</button><input ref={uploadRef} className="sr-only" type="file" accept=".txt,.csv,.tsv" onChange={(event) => { if (event.target.files?.[0]) void uploadNewList(event.target.files[0]); event.target.value = ""; }} /></div></div>

      {creating && <div className="new-gene-list-form"><label className="form-field"><span>List name</span><input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder={`Custom list ${customLists.length + 1}`} autoFocus /></label><label className="form-field"><span>Gene symbols</span><textarea value={newGenes} onChange={(event) => setNewGenes(event.target.value)} rows={8} placeholder={"NFKB1\nCTLA4\nSTAT3"} spellCheck={false} /></label><div><span>{parseGeneList(newGenes).size} unique genes</span><button className="secondary-button" onClick={() => { setCreating(false); setNewName(""); setNewGenes(""); }}>Cancel</button><button className="primary-button dark" onClick={createTypedList}>Save list</button></div></div>}

      {customLists.length ? <div className="custom-gene-list-grid">{customLists.map((list) => <CustomGeneSet key={list.id} list={list} onChange={(updated) => setCustomLists(customLists.map((item) => item.id === updated.id ? updated : item))} onDelete={() => { if (window.confirm(`Delete "${list.name}"?`)) setCustomLists(customLists.filter((item) => item.id !== list.id)); }} />)}</div> : !creating && <div className="empty-state compact gene-list-empty"><span className="empty-icon"><Icon name="file" /></span><h2>No custom lists yet</h2><p>Create a list by typing or pasting gene symbols, or upload a text, CSV, or TSV file.</p></div>}
    </section>
  </div>;
}

function CustomGeneSet({ list, onChange, onDelete }: { list: CustomGeneList; onChange: (list: CustomGeneList) => void; onDelete: () => void }) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(list.name);
  const [draft, setDraft] = useState([...list.genes].sort().join("\n"));
  const beginEditing = () => {
    setName(list.name);
    setDraft([...list.genes].sort().join("\n"));
    setEditing(true);
  };
  const save = () => {
    onChange({ ...list, name: name.trim() || "Custom list", genes: parseGeneList(draft) });
    setEditing(false);
  };
  return <article className="custom-gene-list-card"><header><div><strong>{list.name}</strong><span>{list.genes.size} gene{list.genes.size === 1 ? "" : "s"}</span></div><div><button className="secondary-button" onClick={beginEditing}>Edit</button><button className="secondary-button" onClick={() => uploadRef.current?.click()}>Replace file</button><button className="danger-text-button" onClick={onDelete}>Delete</button><input ref={uploadRef} className="sr-only" type="file" accept=".txt,.csv,.tsv" onChange={(event) => { const file = event.target.files?.[0]; if (file) file.text().then((text) => onChange({ ...list, genes: parseGeneList(text) })); event.target.value = ""; }} /></div></header>{editing ? <div className="custom-gene-list-editor"><label className="form-field"><span>List name</span><input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="form-field"><span>Gene symbols</span><textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={9} spellCheck={false} /></label><div><span>{parseGeneList(draft).size} unique genes</span><button className="secondary-button" onClick={() => setEditing(false)}>Cancel</button><button className="primary-button dark" onClick={save}>Apply changes</button></div></div> : <p>{[...list.genes].sort().slice(0, 18).join(", ")}{list.genes.size > 18 ? `, +${list.genes.size - 18} more` : ""}{!list.genes.size ? "No genes have been added." : ""}</p>}</article>;
}

function ImportPanel({ importing, importProgress, wgsImportJob, error, summary, pendingFiles, onStageFiles, onImportFiles, qcSettings, setQcSettings, qcPreset, setQcPreset, includeQcFailing, setIncludeQcFailing }: { importing: boolean; importProgress: string; wgsImportJob: WgsReviewJob | null; error: string; summary: ImportSummary | null; pendingFiles: File[]; onStageFiles: (files: File[]) => void; onImportFiles: (files: File[], analysisScope: AnalysisScope, filters: WgsPrefilterOptions, workstationPaths: string[]) => void; qcSettings: VariantQcSettings; setQcSettings: React.Dispatch<React.SetStateAction<VariantQcSettings>>; qcPreset: "standard" | "none" | "custom"; setQcPreset: (value: "standard" | "none" | "custom") => void; includeQcFailing: boolean; setIncludeQcFailing: (value: boolean) => void }) {
  const [mode, setMode] = useState<"review" | "annotate">(
    pendingFiles.length ? "review" : "annotate",
  );
  const [analysisScope, setAnalysisScope] = useState<AnalysisScope>("exome");
  const [wgsFilters, setWgsFilters] = useState<WgsPrefilterOptions>({ ...DEFAULT_WGS_PREFILTER });
  const [wgsWorkstationPaths, setWgsWorkstationPaths] = useState("");
  const reviewPicker = useRef<HTMLInputElement>(null);
  const reviewFolder = useRef<HTMLInputElement>(null);

  function configureFolderInput(element: HTMLInputElement | null) {
    reviewFolder.current = element;
    element?.setAttribute("webkitdirectory", "");
  }

  async function handleDrop(event: React.DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    const files = await droppedVcfFiles(event.dataTransfer);
    if (files.length) onStageFiles(files);
  }

  const submittedWgsFilters: WgsPrefilterOptions = { ...wgsFilters };
  const submittedWgsPaths = wgsWorkstationPaths
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const activeWgsPaths = analysisScope === "whole_genome" ? submittedWgsPaths : [];
  const wgsPercent = Math.max(0, Math.min(100, wgsImportJob?.progress ?? 0));

  return <div className="import-page">
    <div className="intake-welcome">
      <p className="eyebrow">Start here</p>
      <h1>Import VCF files</h1>
      <p>Choose the route that matches your files. Everything stays on this workstation.</p>
      <div className="analysis-scope-switch" role="radiogroup" aria-label="Analysis region">
        <button role="radio" aria-checked={analysisScope === "exome"} className={analysisScope === "exome" ? "active" : ""} onClick={() => setAnalysisScope("exome")}><strong>Exome region only</strong><span>Coding exons and splice-region padding</span></button>
        <button role="radio" aria-checked={analysisScope === "whole_genome"} className={analysisScope === "whole_genome" ? "active" : ""} onClick={() => setAnalysisScope("whole_genome")}><strong>Whole genome</strong><span>Indexed server-side intake and conservative prefiltering</span></button>
      </div>
      <div className="intake-mode-switch" role="tablist" aria-label="VCF intake route">
        <button role="tab" aria-selected={mode === "annotate"} className={mode === "annotate" ? "active" : ""} onClick={() => setMode("annotate")}><strong>Run VEP first</strong><span>Start with a raw or hard-filtered VCF</span></button>
        <button role="tab" aria-selected={mode === "review"} className={mode === "review" ? "active" : ""} onClick={() => setMode("review")}><strong>Review annotated VCF</strong><span>My VCF already has VEP annotations</span></button>
      </div>
    </div>

    {mode === "review" ? <><section className="intake-card intake-primary-card">
      <div className="intake-card-head"><span className="step-number">1</span><div><p className="eyebrow">Already annotated</p><h2>Select VCF files</h2></div></div>
      <button className="drop-zone review-drop-zone" onClick={() => reviewPicker.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop} disabled={importing}>
        <span className="drop-icon"><Icon name="upload" /></span>
        <strong>Drop VCF files here</strong>
        <span>or click to choose .vcf / .vcf.gz files</span>
        <small>Single-sample, multi-sample, and multiple VCFs are supported.</small>
      </button>
      <div className="folder-choice"><span>Have a folder of annotated VCFs?</span><button className="secondary-button" onClick={() => reviewFolder.current?.click()}>Choose folder</button></div>
      <input ref={reviewPicker} className="sr-only" type="file" accept={VCF_FILE_ACCEPT} multiple onChange={(event) => { if (event.target.files) onStageFiles(Array.from(event.target.files)); event.target.value = ""; }} />
      <input ref={configureFolderInput} className="sr-only" type="file" accept={VCF_FILE_ACCEPT} multiple onChange={(event) => { if (event.target.files) onStageFiles(Array.from(event.target.files)); event.target.value = ""; }} />
      {pendingFiles.length > 0 && <div className="pending-review-files"><div className="pending-review-head"><div><strong>{pendingFiles.length} file{pendingFiles.length === 1 ? "" : "s"} selected</strong><span>{compactFileSize(pendingFiles.reduce((total, file) => total + file.size, 0))} total · not read yet</span></div><button onClick={() => onStageFiles([])}>Clear all</button></div>{pendingFiles.map((file, index) => <div className="pending-review-row" key={`${file.name}:${file.size}:${file.lastModified}:${index}`}><span className="file-badge"><Icon name="file" /></span><div><strong>{file.name}</strong><span>{compactFileSize(file.size)}</span></div><button aria-label={`Remove ${file.name}`} onClick={() => onStageFiles(pendingFiles.filter((_, itemIndex) => itemIndex !== index))}>Remove</button></div>)}</div>}
      {analysisScope === "whole_genome" && <section className="wgs-prefilter-panel"><div className="settings-section-head"><div><h3>Whole-genome candidate import</h3><p>The local service creates or reuses BGZF/tabix indexes and filters chromosome shards with four readers before browser review.</p></div><span className="readiness ready">cCRE default</span></div><div className="qc-field-grid">
        <QcNumberField label="gnomAD popmax ≤" value={wgsFilters.max_gnomad_popmax} step="0.001" onChange={(value) => setWgsFilters((current) => ({ ...current, max_gnomad_popmax: value }))}/>
        <QcNumberField label="SpliceAI ≥" value={wgsFilters.min_spliceai} step="0.05" onChange={(value) => setWgsFilters((current) => ({ ...current, min_spliceai: value }))}/>
        <QcNumberField label="|promoterAI| ≥" value={wgsFilters.min_promoterai_abs} step="0.05" onChange={(value) => setWgsFilters((current) => ({ ...current, min_promoterai_abs: value }))}/>
      </div><NoncodingModePicker value={wgsFilters.noncoding_mode} onChange={(noncoding_mode) => setWgsFilters((current) => ({ ...current, noncoding_mode }))}/><details className="advanced-paths wgs-local-paths"><summary>Recommended for very large files: use existing workstation paths</summary><label className="form-field"><span>Annotated WGS VCF path(s), one per line</span><textarea rows={3} value={wgsWorkstationPaths} onChange={(event) => setWgsWorkstationPaths(event.target.value)} placeholder={"/absolute/path/case.annotated.vcf.gz"}/><small>A direct path avoids copying a multi-gigabyte browser-selected file into local staging.</small></label></details><p className="wgs-filter-logic"><strong>Logic:</strong> PASS/QC AND (popmax ≤ threshold OR popmax unavailable) AND (exonic/essential-splice OR SpliceAI ≥ threshold OR |promoterAI| ≥ threshold OR selected noncoding regions OR flagged unscored intronic/promoter indel). The indel exception applies only when the relevant precomputed score is unavailable, not when a populated score is below threshold.</p></section>}
      <div className="plain-defaults"><span>PASS records only</span><span>MANE + clinical transcript fallback</span><span>Repeat/SegDup excluded by default</span></div>
      <QcSettingsPanel settings={qcSettings} setSettings={setQcSettings} preset={qcPreset} setPreset={setQcPreset} includeFailing={includeQcFailing} setIncludeFailing={setIncludeQcFailing} />
      {error && <div className="alert error">{error}</div>}
      {analysisScope === "whole_genome" && importing && <div className={`cohort-import-progress wgs-import-progress ${wgsImportJob?.status === "failed" ? "failed" : ""}`}><div><strong>{wgsImportJob?.message || importProgress || "Preparing whole-genome input…"}</strong><span>{wgsPercent.toFixed(1)}%</span></div><div className="progress-track" role="progressbar" aria-label="Whole-genome indexing and prefiltering progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(wgsPercent)}><span style={{ width: `${wgsPercent}%` }} /></div><small>{wgsImportJob ? `${wgsImportJob.records_scanned.toLocaleString()} records scanned · ${wgsImportJob.records_retained.toLocaleString()} retained · ${wgsImportJob.reader_count} reader${wgsImportJob.reader_count === 1 ? "" : "s"}` : "Staging the selected WGS VCF on this workstation"}</small></div>}
      <div className="review-import-actions"><span>{importing && importProgress ? importProgress : pendingFiles.length || activeWgsPaths.length ? analysisScope === "whole_genome" ? "The indexed WGS prefilter runs locally before browser review." : "The selected files will be read using the thresholds above." : "Select one or more annotated VCFs to continue."}</span><button className="primary-button dark" disabled={importing || (pendingFiles.length === 0 && activeWgsPaths.length === 0)} onClick={() => onImportFiles(pendingFiles, analysisScope, submittedWgsFilters, activeWgsPaths)}>{importing ? analysisScope === "whole_genome" ? "Preparing WGS…" : "Importing annotations…" : "Import and review variants"}</button></div>
    </section><RecentReviewFiles onStageFiles={onStageFiles} onWgsPath={(path) => { onStageFiles([]); setAnalysisScope("whole_genome"); setWgsWorkstationPaths(path); setMode("review"); }}/></> : <AnnotationPanel analysisScope={analysisScope} onReviewPath={(path) => { onStageFiles([]); setAnalysisScope("whole_genome"); setWgsWorkstationPaths(path); setMode("review"); }} onReviewFile={(files) => { onStageFiles(files); setMode("review"); }} />}

    {summary && <div className="import-summary"><h2>Last review import</h2><div className="stat-grid"><Stat value={summary.files} label="files"/><Stat value={summary.samples} label="samples"/><Stat value={summary.rows} label="transcript rows"/></div><div className="intake-check-grid">{summary.intakeQc.map((check) => <div className={`intake-check ${check.status}`} key={check.id}><span>{check.status === "pass" ? "✓" : "!"}</span><div><strong>{check.label}</strong><small>{check.detail}</small></div></div>)}</div>{summary.warnings.map((warning) => <div className="alert" key={warning}>{warning}</div>)}</div>}
  </div>;
}

function RecentReviewFiles({ onStageFiles, onWgsPath }: { onStageFiles: (files: File[]) => void; onWgsPath: (path: string) => void }) {
  const [jobs, setJobs] = useState<AnnotationJob[]>([]);
  const [opening, setOpening] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    getJobs().then(setJobs).catch(() => setJobs([]));
  }, []);
  const completed = jobs.filter(
    (job) => job.status === "succeeded" && Boolean(job.final_output_path || job.output_path),
  ).slice(0, 8);
  if (!completed.length) return null;

  async function openJob(job: AnnotationJob) {
    setOpening(job.id);
    setError("");
    try {
      const path = job.final_output_path || job.output_path;
      if (job.analysis_scope === "whole_genome") {
        onWgsPath(path);
        return;
      }
      onStageFiles([await openJobReviewFile(job.id, fileName(path))]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not reopen the annotated VCF.");
    } finally {
      setOpening("");
    }
  }

  return <section className="intake-card recent-review-card"><div className="intake-card-head"><span className="step-number">↻</span><div><p className="eyebrow">Previously annotated</p><h2>Reopen a recent VCF</h2></div></div><p className="intake-copy">Successful local VEP outputs remain available after the browser is refreshed.</p><div className="recent-review-list">{completed.map((job) => { const path = job.final_output_path || job.output_path; return <div key={job.id}><div><strong>{fileName(path)}</strong><span title={path}>{path}</span></div><button className="secondary-button" disabled={Boolean(opening)} onClick={() => openJob(job)}>{opening === job.id ? "Selecting…" : "Select"}</button></div>; })}</div>{error && <div className="alert error">{error}</div>}</section>;
}

function QcNumberField({ label, value, onChange, step = "1" }: { label: string; value: number | null; onChange: (value: number | null) => void; step?: string }) {
  return <label className="qc-number-field"><span>{label}</span><input type="number" step={step} value={value ?? ""} placeholder="No cutoff" onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}/></label>;
}

function NoncodingModePicker({ value, onChange }: { value: WgsPrefilterOptions["noncoding_mode"]; onChange: (value: WgsPrefilterOptions["noncoding_mode"]) => void }) {
  const choices: Array<{ id: WgsPrefilterOptions["noncoding_mode"]; label: string; detail: string }> = [
    { id: "ccre", label: "ENCODE cCRE regions", detail: "Recommended · SCREEN Registry V4 overlap" },
    { id: "all", label: "All noncoding regions", detail: "Largest import; may use substantially more disk space" },
    { id: "none", label: "No additional noncoding regions", detail: "SpliceAI and promoterAI candidates are still included" },
  ];
  return <fieldset className="noncoding-mode-picker"><legend>Noncoding region inclusion</legend><div role="radiogroup" aria-label="Noncoding region inclusion">{choices.map((choice) => <label className={value === choice.id ? "active" : ""} key={choice.id}><input type="radio" name="noncoding-mode" value={choice.id} checked={value === choice.id} onChange={() => onChange(choice.id)}/><span><strong>{choice.label}</strong><small>{choice.detail}</small></span></label>)}</div></fieldset>;
}

function QcSettingsPanel({ settings, setSettings, preset, setPreset, includeFailing, setIncludeFailing }: { settings: VariantQcSettings; setSettings: React.Dispatch<React.SetStateAction<VariantQcSettings>>; preset: "standard" | "none" | "custom"; setPreset: (value: "standard" | "none" | "custom") => void; includeFailing: boolean; setIncludeFailing: (value: boolean) => void }) {
  const update = <K extends keyof VariantQcSettings>(key: K, value: VariantQcSettings[K]) => {
    setPreset("custom");
    setSettings((current) => ({ ...current, [key]: value }));
  };
  const choosePreset = (value: "standard" | "none" | "custom") => {
    setPreset(value);
    if (value === "standard") setSettings({ ...STANDARD_VARIANT_QC });
    if (value === "none") setSettings({ ...NO_VARIANT_QC });
  };
  return <section className="qc-settings-panel">
    <div className="qc-settings-head"><div><p className="eyebrow">Variant call QC</p><h3>Review thresholds</h3><p>Calls that fail are hidden by default, never removed from the imported VCF.</p></div><label><span>Preset</span><select value={preset} onChange={(event) => choosePreset(event.target.value as "standard" | "none" | "custom")}><option value="standard">Standard / gnomAD-like</option><option value="none">No thresholds</option><option value="custom">Custom</option></select></label></div>
    <div className="qc-field-grid">
      <QcNumberField label="Minimum DP" value={settings.minDp} onChange={(value) => update("minDp", value)}/>
      <QcNumberField label="Minimum GQ" value={settings.minGq} onChange={(value) => update("minGq", value)}/>
      <QcNumberField label="Minimum alt reads" value={settings.minAltDepth} onChange={(value) => update("minAltDepth", value)}/>
      <QcNumberField label="Het allele balance ≥" value={settings.hetAbMin} step="0.01" onChange={(value) => update("hetAbMin", value)}/>
      <QcNumberField label="Het allele balance ≤" value={settings.hetAbMax} step="0.01" onChange={(value) => update("hetAbMax", value)}/>
      <QcNumberField label="Hom/hemizygous alt balance ≥" value={settings.homAltAbMin} step="0.01" onChange={(value) => update("homAltAbMin", value)}/>
    </div>
    <label className="qc-ft-field"><span>Genotype FT handling</span><select value={settings.genotypeFtMode} onChange={(event) => update("genotypeFtMode", event.target.value as VariantQcSettings["genotypeFtMode"])}><option value="exclude_explicit">Exclude explicitly filtered genotypes</option><option value="ignore">Ignore FT</option><option value="require_pass">Require explicit FT=PASS</option></select><small>Default accepts PASS, ., blank, or absent FT; named filters such as LowGQ or FAIL are excluded.</small></label>
    <Check label="Include calls failing these thresholds" checked={includeFailing} onChange={setIncludeFailing} note="Failed calls appear with a QC fail flag" />
    <details className="qc-optional"><summary>Additional optional genotype and site thresholds</summary><p>These have no default cutoffs. Set only fields your calling workflow supports.</p><div className="qc-field-grid">
      <QcNumberField label="Maximum DP" value={settings.maxDp} onChange={(value) => update("maxDp", value)}/>
      <QcNumberField label="Minimum het ref reads" value={settings.minHetRefDepth} onChange={(value) => update("minHetRefDepth", value)}/>
      <QcNumberField label="Minimum QUAL" value={settings.minQual} step="0.1" onChange={(value) => update("minQual", value)}/>
      <QcNumberField label="Minimum QD" value={settings.minQd} step="0.1" onChange={(value) => update("minQd", value)}/>
      <QcNumberField label="Minimum MQ" value={settings.minMq} step="0.1" onChange={(value) => update("minMq", value)}/>
      <QcNumberField label="Maximum FS" value={settings.maxFs} step="0.1" onChange={(value) => update("maxFs", value)}/>
      <QcNumberField label="Maximum SOR" value={settings.maxSor} step="0.1" onChange={(value) => update("maxSor", value)}/>
      <QcNumberField label="Minimum MQRankSum" value={settings.minMqRankSum} step="0.1" onChange={(value) => update("minMqRankSum", value)}/>
      <QcNumberField label="Minimum ReadPosRankSum" value={settings.minReadPosRankSum} step="0.1" onChange={(value) => update("minReadPosRankSum", value)}/>
      <QcNumberField label="Minimum BaseQRankSum" value={settings.minBaseQRankSum} step="0.1" onChange={(value) => update("minBaseQRankSum", value)}/>
    </div></details>
  </section>;
}

type AnnotationSource = ServiceCapabilities["annotation_profile"]["sources"][number];

function DatasetSetupCard({
  source,
  analysisScope,
  enabled,
  onEnabled,
  downloadJob,
  onDownload,
  preparationPath,
  onPreparationPath,
  onPrepare,
}: {
  source: AnnotationSource;
  analysisScope: AnalysisScope;
  enabled: boolean;
  onEnabled: (value: boolean) => void;
  downloadJob?: ResourceDownloadJob;
  onDownload: (resourceId: "spliceai" | "cadd_wgs" | "clinvar" | "liftover" | "logofunc" | "ccre") => void;
  preparationPath: string;
  onPreparationPath: (value: string) => void;
  onPrepare: () => void;
}) {
  const supported = (source.available_in ?? ["exome", "whole_genome"]).includes(analysisScope);
  const activeDownload = downloadJob?.status === "queued" || downloadJob?.status === "running";
  const setupLabels = {
    manual: "Manual registration",
    download: "UI download",
    prepare: "Local preparation",
    bundled: "Bundled",
    deferred: "Deferred",
  };
  const status = !supported
    ? analysisScope === "exome" ? "Whole-genome only" : "Exome only"
    : activeDownload
    ? downloadJob?.status === "queued" ? "Queued" : downloadJob?.operation === "preparation" ? "Preparing" : "Downloading"
    : downloadJob?.status === "failed" ? downloadJob?.operation === "preparation" ? "Preparation failed" : "Download failed"
    : source.installed ? "Installed"
    : source.setup_mode === "deferred" ? "Not configured in this release"
    : source.required ? "Required · missing" : "Optional · not installed";
  const canToggle = supported && !["liftover", "ccre"].includes(source.id) && !source.required && source.available && source.setup_mode !== "deferred";
  const downloadId = source.download_id as "spliceai" | "cadd_wgs" | "clinvar" | "liftover" | "logofunc" | "ccre" | undefined;
  const buttonLabel = source.id === "clinvar"
    ? "Download latest"
    : source.id === "liftover" ? source.installed ? "Verify hg19 bundle" : "Download hg19 bundle"
    : source.id === "cadd_wgs" ? source.installed ? "Verify 83 GiB files" : "Download / resume 83 GiB"
    : source.installed ? "Verify files" : "Download / resume";
  return <article className={`dataset-card ${source.installed ? "installed" : "missing"} ${source.setup_mode} ${supported ? "" : "profile-unavailable"}`}>
    <div className="dataset-card-top">
      <label className="dataset-enable">
        <input type="checkbox" checked={supported && enabled} disabled={!canToggle} onChange={(event) => onEnabled(event.target.checked)}/>
        <span className="custom-check"/>
      </label>
      <div className="dataset-card-title"><strong>{source.label}{source.version ? ` ${source.version}` : ""}</strong><small>{source.description}</small></div>
      <span className={`dataset-status ${activeDownload ? "working" : source.installed ? "ready" : "missing"}`}>{status}</span>
    </div>
    <div className="dataset-meta"><span>{setupLabels[source.setup_mode]}</span>{source.size_hint && <span>{source.size_hint}</span>}</div>
    {activeDownload && <div className="resource-progress"><progress max={100} value={downloadJob?.progress ?? undefined}/><span>{downloadJob?.progress !== null && downloadJob?.progress !== undefined ? `${downloadJob.progress.toFixed(1)}%` : "Working…"} · {downloadJob?.message}</span></div>}
    {downloadJob?.status === "failed" && <div className="resource-download-error"><strong>{downloadJob.error || downloadJob.message}</strong><details><summary>Download log</summary><pre>{downloadJob.log || "No log output was captured."}</pre></details></div>}
    {source.setup_mode === "prepare" && <div className="dataset-preparation"><label><span>{source.prepare_id === "logofunc" ? "Existing LoGoFunc .csv.gz file or containing folder" : "Folder containing tss.tsv and promoterAI_tss500.tsv.gz"}</span><input value={preparationPath} onChange={(event) => onPreparationPath(event.target.value)} placeholder={source.prepare_id === "logofunc" ? "/absolute/path/to/LoGoFunc" : "/absolute/path/to/PromoterAI"} /></label><button type="button" disabled={activeDownload || !preparationPath.trim()} onClick={onPrepare}>{activeDownload ? "Preparing…" : source.prepare_id === "logofunc" ? "Use existing source" : source.installed ? "Prepare again" : "Prepare local files"}</button><small>{source.prepare_id === "logofunc" ? "The checksum-verified source stays in place; ignored local links and a provenance manifest are created without copying the 3.66 GB table." : "The licensed source files stay in their current folder and are not committed or uploaded. Annotation remains whole-genome only."}</small></div>}
    <div className="dataset-actions">
      {downloadId && <button type="button" disabled={activeDownload} onClick={() => onDownload(downloadId)}>{activeDownload ? "Downloading…" : buttonLabel}</button>}
      {source.reference_url && <a href={source.reference_url} target="_blank" rel="noreferrer">{source.reference_label || "Official reference"} ↗</a>}
    </div>
    <details className="dataset-instructions"><summary>{source.setup_mode === "manual" ? "Registration and setup instructions" : "Dataset details"}</summary><ol>{(source.instructions ?? []).map((instruction) => <li key={instruction}>{instruction}</li>)}</ol>{(source.configured_paths?.length ?? 0) > 0 && <div className="configured-locations"><span>Configured location{source.configured_paths?.length === 1 ? "" : "s"}</span>{source.configured_paths?.map((path) => <code key={path}>{path}</code>)}</div>}</details>
  </article>;
}

function AnnotationPanel({ analysisScope, onReviewFile, onReviewPath }: { analysisScope: AnalysisScope; onReviewFile: (files: File[]) => void; onReviewPath: (path: string) => void }) {
  const [capabilities, setCapabilities] = useState<ServiceCapabilities | null>(null);
  const [jobs, setJobs] = useState<AnnotationJob[]>([]);
  const [resourceJobs, setResourceJobs] = useState<ResourceDownloadJob[]>([]);
  const [step, setStep] = useState<1 | 2>(1);
  const [setupOnly, setSetupOnly] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [stagedFiles, setStagedFiles] = useState<StagedAnnotationFile[]>([]);
  const [inputPaths, setInputPaths] = useState("");
  const [outputDirectory, setOutputDirectory] = useState("");
  const [profile, setProfile] = useState("local");
  const [inputAssembly, setInputAssembly] = useState<"GRCh38" | "GRCh37" | "auto">("auto");
  const [passOnly, setPassOnly] = useState(true);
  const [useClinvar, setUseClinvar] = useState(true);
  const [sourceEnabled, setSourceEnabled] = useState<Record<string, boolean>>({});
  const [promoterAiSourceDir, setPromoterAiSourceDir] = useState("");
  const [loGoFuncSourcePath, setLoGoFuncSourcePath] = useState("");
  const [selectedDbnsfpPredictors, setSelectedDbnsfpPredictors] = useState<Set<string>>(new Set());
  const [fork, setFork] = useState(8);
  const [workerMode, setWorkerMode] = useState<"automatic" | "custom">("automatic");
  const [serviceError, setServiceError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [stageProgress, setStageProgress] = useState("");
  const [expandedLog, setExpandedLog] = useState<{ id: string; text: string } | null>(null);
  const filePicker = useRef<HTMLInputElement>(null);
  const folderPicker = useRef<HTMLInputElement>(null);

  function configureFolderInput(element: HTMLInputElement | null) {
    folderPicker.current = element;
    element?.setAttribute("webkitdirectory", "");
  }

  useEffect(() => {
    let active = true;
    async function refresh(initial = false) {
      try {
        const [nextCapabilities, nextJobs, nextResourceJobs] = await Promise.all([
          getCapabilities(),
          getJobs(),
          getResourceDownloads(),
        ]);
        if (!active) return;
        setCapabilities(nextCapabilities);
        setJobs(nextJobs);
        setResourceJobs(nextResourceJobs);
        setServiceError("");
        if (initial) {
          setProfile(nextCapabilities.profiles[0]?.id ?? "local");
          setInputAssembly(nextCapabilities.defaults.input_assembly);
          setOutputDirectory(`${nextCapabilities.pipeline_root}/results`);
          setFork(nextCapabilities.hardware.recommended_vep_workers);
          setWorkerMode("automatic");
          setSourceEnabled(Object.fromEntries(
            nextCapabilities.annotation_profile.sources.map((source) => [
              source.id,
              source.required || (source.enabled && source.available),
            ]),
          ));
        }
      } catch {
        if (active) setServiceError("Local annotation service is not running.");
      }
    }
    refresh(true);
    const timer = window.setInterval(() => refresh(false), 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  function chooseFiles(files: File[]) {
    const selected = vcfFiles(files);
    setSetupOnly(false);
    setSelectedFiles(selected);
    setStagedFiles([]);
    setServiceError(selected.length ? "" : "No .vcf or .vcf.gz files were found.");
    if (selected.length) setStep(2);
  }

  async function handleDrop(event: React.DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    chooseFiles(await droppedVcfFiles(event.dataTransfer));
  }

  async function queueAnnotation() {
    const manualPaths = inputPaths.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    let paths = stagedFiles.map((item) => item.path);
    if (!selectedFiles.length && !manualPaths.length) {
      setServiceError("Choose at least one VCF file or folder.");
      setStep(1);
      return;
    }
    if (!capabilities) {
      setServiceError("Start the local annotation service before queuing a run.");
      return;
    }
    setSubmitting(true);
    setServiceError("");
    try {
      if (selectedFiles.length && !stagedFiles.length) {
        const batch = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        const staged: StagedAnnotationFile[] = [];
        for (let index = 0; index < selectedFiles.length; index += 1) {
          setStageProgress(`Copying ${index + 1} of ${selectedFiles.length}: ${selectedFiles[index].name}`);
          staged.push(await stageAnnotationFile(selectedFiles[index], batch));
        }
        setStagedFiles(staged);
        paths = staged.map((item) => item.path);
      }
      paths = [...paths, ...manualPaths];
      setStageProgress("Adding annotation jobs…");
      const outputs = uniqueAnnotationOutputPaths(paths, outputDirectory);
      for (let index = 0; index < paths.length; index += 1) {
        const inputPath = paths[index];
        await submitJob({
          input_path: inputPath,
          output_path: outputs[index],
          profile,
          analysis_scope: analysisScope,
          input_assembly: inputAssembly,
          coding_only: analysisScope === "exome",
          include_filtered: !passOnly,
          use_clinvar: useClinvar,
          annotation_options: {
            ...Object.fromEntries(
              (capabilities.annotation_profile.sources ?? []).map((source) => [
                source.id,
                (source.available_in ?? ["exome", "whole_genome"]).includes(analysisScope)
                  && Boolean(sourceEnabled[source.id]),
              ]),
            ),
            analysis_scope: analysisScope,
            fork,
            dbnsfp_predictors: [...selectedDbnsfpPredictors],
          },
        });
      }
      setJobs(await getJobs());
      setSelectedFiles([]);
      setStagedFiles([]);
      setStep(1);
    } catch (error) {
      setServiceError(error instanceof Error ? error.message : "Could not queue annotation.");
    } finally {
      setSubmitting(false);
      setStageProgress("");
    }
  }

  async function stopJob(jobId: string) {
    try {
      await cancelJob(jobId);
      setJobs(await getJobs());
    } catch (error) {
      setServiceError(error instanceof Error ? error.message : "Could not cancel job.");
    }
  }

  async function downloadResource(resourceId: "spliceai" | "cadd_wgs" | "clinvar" | "liftover" | "logofunc" | "ccre") {
    setServiceError("");
    try {
      const job = await startResourceDownload(resourceId);
      setResourceJobs((current) => [
        job,
        ...current.filter((item) => item.id !== job.id),
      ]);
    } catch (error) {
      setServiceError(
        error instanceof Error ? error.message : "Could not start the download.",
      );
    }
  }

  async function preparePromoterAi() {
    setServiceError("");
    try {
      const job = await startPromoterAiPreparation(promoterAiSourceDir.trim());
      setResourceJobs((current) => [
        job,
        ...current.filter((item) => item.id !== job.id),
      ]);
    } catch (error) {
      setServiceError(
        error instanceof Error ? error.message : "Could not start PromoterAI preparation.",
      );
    }
  }

  async function prepareLoGoFunc() {
    setServiceError("");
    try {
      const job = await startLoGoFuncPreparation(loGoFuncSourcePath.trim());
      setResourceJobs((current) => [
        job,
        ...current.filter((item) => item.id !== job.id),
      ]);
    } catch (error) {
      setServiceError(
        error instanceof Error ? error.message : "Could not install the LoGoFunc source.",
      );
    }
  }

  async function showLog(jobId: string) {
    try {
      setExpandedLog({ id: jobId, text: await getJobLog(jobId) });
    } catch (error) {
      setServiceError(error instanceof Error ? error.message : "Could not read job log.");
    }
  }

  async function reviewJob(job: AnnotationJob) {
    try {
      const path = job.final_output_path || job.output_path;
      if (job.analysis_scope === "whole_genome") {
        onReviewPath(path);
        return;
      }
      onReviewFile([await openJobReviewFile(job.id, fileName(path))]);
    } catch (error) {
      setServiceError(error instanceof Error ? error.message : "Could not reopen the annotated VCF.");
    }
  }

  const recentJobs = jobs.slice(0, 8);
  const profileReady = Boolean(capabilities?.annotation_profile.ready);
  const dbnsfpOptions = capabilities?.annotation_profile.dbnsfp_predictors ?? [];
  const availableDbnsfpOptions = dbnsfpOptions.filter((item) => item.available);
  const latestResourceJobs = new Map<string, ResourceDownloadJob>();
  resourceJobs.forEach((job) => {
    if (!latestResourceJobs.has(job.resource_id)) {
      latestResourceJobs.set(job.resource_id, job);
    }
  });
  const resourceSetupBusy = resourceJobs.some(
    (job) => job.status === "queued" || job.status === "running",
  );
  return <section className="intake-card annotate-card intake-primary-card">
    <div className="intake-card-head"><span className="step-number">{step}</span><div><p className="eyebrow">Local VEP</p><h2>{step === 1 ? "Select raw VCF files" : setupOnly ? "Set up annotation datasets" : "Check annotation settings"}</h2></div><span className={`service-badge ${capabilities ? "online" : "offline"}`}>{capabilities ? "service ready" : "service offline"}</span></div>
    {step === 1 ? <>
      <p className="intake-copy">Drop individual VCFs below, or choose a folder. Each VCF becomes one annotation job.</p>
      <button className="drop-zone annotation-drop-zone" onClick={() => filePicker.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
        <span className="drop-icon"><Icon name="upload" /></span><strong>Drop .vcf or .vcf.gz files here</strong><span>or drop a folder / click to choose files</span><small>Gzip and BGZF-compressed VCFs · single- or multi-sample</small>
      </button>
      <div className="folder-choice"><span>Process every VCF in one folder</span><button className="secondary-button" onClick={() => folderPicker.current?.click()}>Choose folder</button></div>
      <div className="dataset-setup-entry"><div><strong>First time using VEP?</strong><span>Check required datasets, register dbNSFP, and download SpliceAI or ClinVar before selecting a patient VCF.</span></div><button className="secondary-button" onClick={() => { setSetupOnly(true); setStep(2); }}>Set up annotation datasets</button></div>
      <input ref={filePicker} className="sr-only" type="file" accept={VCF_FILE_ACCEPT} multiple onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))} />
      <input ref={configureFolderInput} className="sr-only" type="file" accept={VCF_FILE_ACCEPT} multiple onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))} />
      <details className="advanced-paths"><summary>Advanced: use existing workstation paths</summary><label className="form-field"><span>VCF path(s), one per line</span><textarea rows={3} value={inputPaths} onChange={(event) => setInputPaths(event.target.value)} placeholder={"/absolute/path/patient.vcf.gz\n/absolute/path/folder/another.vcf.gz"} /></label><button className="secondary-button" onClick={() => setStep(2)} disabled={!inputPaths.trim()}>Continue to settings</button></details>
    </> : <>
      {!setupOnly && <div className="wizard-selection"><div><strong>{selectedFiles.length || inputPaths.split(/\r?\n/).filter(Boolean).length} VCF file{(selectedFiles.length || inputPaths.split(/\r?\n/).filter(Boolean).length) === 1 ? "" : "s"} selected</strong><span>{selectedFiles.slice(0, 3).map((file) => file.name).join(", ") || "Existing workstation paths"}{selectedFiles.length > 3 ? ` and ${selectedFiles.length - 3} more` : ""}</span></div><button onClick={() => setStep(1)}>Change</button></div>}
      {!setupOnly && <section className="settings-section"><div className="settings-section-head"><div><h3>{analysisScope === "exome" ? "Exome-region annotation" : "Whole-genome annotation"}</h3><p>{analysisScope === "exome" ? "Coding exons and splice-region padding are selected before VEP." : "The input is automatically prepared as sorted BGZF with tabix/CSI indexing before parallel VEP annotation."}</p></div></div>
        <div className="run-defaults"><div className="scope-run-summary"><strong>{analysisScope === "exome" ? "Exome region only" : "Whole genome"}</strong><span>{analysisScope === "exome" ? "promoterAI and full-genome CADD unavailable" : "indexed WGS intake"}</span></div><Check label="PASS records only" checked={passOnly} onChange={setPassOnly} /><Check label="Refresh ClinVar before run" checked={useClinvar} onChange={setUseClinvar} /></div>
        <div className="form-pair simple"><label className="form-field"><span>Input genome build</span><select value={inputAssembly} onChange={(event) => setInputAssembly(event.target.value as typeof inputAssembly)}>{capabilities?.input_assemblies.map((item) => <option key={item.id} value={item.id}>{item.label}</option>) ?? <option value="GRCh38">GRCh38 / hg38</option>}</select></label><label className="form-field worker-field"><span>VEP workers <small>{workerMode === "automatic" ? "Automatic" : "Custom"}</small></span><div className="worker-value"><strong>{fork}</strong><span>worker{fork === 1 ? "" : "s"}</span>{workerMode === "custom" && <button type="button" onClick={() => { const recommended = capabilities?.hardware.recommended_vep_workers ?? 1; setFork(recommended); setWorkerMode("automatic"); }}>Use automatic</button>}</div><input className="worker-range" type="range" min={1} max={capabilities?.hardware.max_vep_workers ?? 8} step={1} value={fork} onChange={(event) => { setFork(Number(event.target.value)); setWorkerMode("custom"); }} /><small>Detected {capabilities?.hardware.logical_cpus ?? "—"} logical CPU threads · recommended {capabilities?.hardware.recommended_vep_workers ?? "—"}.</small></label></div>
      </section>}
      <section className="settings-section"><div className="settings-section-head"><div><h3>Annotation datasets</h3><p>Availability and indexes are checked automatically. Open each dataset for sources and setup instructions.</p></div><span className={`readiness ${profileReady ? "ready" : "missing"}`}>{profileReady ? "Ready to run" : "Setup needed"}</span></div>
        {capabilities?.annotation_profile.error && <div className="alert error">{capabilities.annotation_profile.error}</div>}
        {capabilities?.annotation_profile.foundations.map((item) => <div className="foundation-row" key={item.id}><span className={`availability-dot ${item.available ? "ready" : "missing"}`} /><strong>{item.label}</strong><span>{item.available ? `Available${item.version ? ` · release ${item.version}` : ""}` : "Missing"}</span></div>)}
        <div className="pinned-bundle-note"><strong>Validated annotation bundle · VEP 113 / GRCh38</strong><span>This workstation profile is intentionally pinned. Annotation resource changes are installed only through a tested software release.</span></div>
        <div className="dataset-grid">{capabilities?.annotation_profile.sources.map((source) => <DatasetSetupCard key={source.id} source={source} analysisScope={analysisScope} enabled={sourceEnabled[source.id] ?? source.enabled} onEnabled={(checked) => setSourceEnabled((current) => ({ ...current, [source.id]: checked }))} downloadJob={latestResourceJobs.get(source.id)} onDownload={downloadResource} preparationPath={source.id === "promoterai" ? promoterAiSourceDir : source.id === "logofunc" ? loGoFuncSourcePath : ""} onPreparationPath={source.id === "promoterai" ? setPromoterAiSourceDir : source.id === "logofunc" ? setLoGoFuncSourcePath : () => undefined} onPrepare={source.id === "promoterai" ? preparePromoterAi : source.id === "logofunc" ? prepareLoGoFunc : () => undefined}/>)}</div>
        {!setupOnly && <details className="dbnsfp-options"><summary><span><strong>Additional dbNSFP predictors</strong><small>Optional; core AlphaMissense, CADD, REVEL and commonly used predictors remain included.</small></span><em>{selectedDbnsfpPredictors.size} selected</em></summary><div className="dbnsfp-options-body"><div className="dbnsfp-option-actions"><p>Select only predictors useful to your analysis. More columns increase output size and annotation work.</p><div><button type="button" onClick={() => setSelectedDbnsfpPredictors(new Set(availableDbnsfpOptions.filter((item) => item.recommended).map((item) => item.id)))}>Recommended extended</button><button type="button" onClick={() => setSelectedDbnsfpPredictors(new Set(availableDbnsfpOptions.map((item) => item.id)))}>Select all available</button><button type="button" onClick={() => setSelectedDbnsfpPredictors(new Set())}>Clear</button></div></div><div className="dbnsfp-predictor-grid">{dbnsfpOptions.map((item) => <label className={!item.available ? "unavailable" : ""} key={item.id}><input type="checkbox" checked={selectedDbnsfpPredictors.has(item.id)} disabled={!item.available} onChange={(event) => setSelectedDbnsfpPredictors((current) => toggleSet(current, item.id, event.target.checked))}/><span className="custom-check"/><span><strong>{item.label}</strong><small>{item.category}{item.recommended ? " · recommended extended" : ""}</small></span></label>)}</div>{dbnsfpOptions.some((item) => !item.available) && <p className="dbnsfp-unavailable-note">Unavailable choices are not present in the installed dbNSFP header and cannot be queued.</p>}</div></details>}
      </section>
      {!setupOnly && <details className="advanced-paths"><summary>Output and execution</summary><div className="form-pair simple"><label className="form-field"><span>Output folder</span><input value={outputDirectory} onChange={(event) => setOutputDirectory(event.target.value)} /></label><label className="form-field"><span>Execution</span><select value={profile} onChange={(event) => setProfile(event.target.value)} disabled={!capabilities}>{capabilities?.profiles.map((item) => <option key={item.id} value={item.id}>{item.label}</option>) ?? <option>Local workstation</option>}</select></label></div></details>}
      <div className="annotation-actions wizard-actions"><button className="secondary-button" onClick={() => { setSetupOnly(false); setStep(1); }}>{setupOnly ? "Done" : "Back"}</button>{!setupOnly && <button className="primary-button dark" onClick={queueAnnotation} disabled={submitting || resourceSetupBusy || !capabilities || !profileReady}>{submitting ? stageProgress || "Starting…" : resourceSetupBusy ? "Wait for dataset download" : "Start VEP annotation"}</button>}</div>
    </>}
    {serviceError && <div className={`alert ${capabilities ? "error" : ""}`}>{serviceError}{!capabilities && <small> Start <span className="mono">python3 -m local_service.workbench_service</span> in the pipeline folder.</small>}</div>}
    {recentJobs.length > 0 && <div className="job-list"><div className="job-list-head"><strong>Recent annotation jobs</strong><span>{jobs.filter((job) => job.status === "queued" || job.status === "running").length} active</span></div>{recentJobs.map((job) => <div className="job-row" key={job.id}><span className={`job-status ${job.status}`}>{job.status}</span><div><strong title={job.input_path}>{fileName(job.input_path)}</strong><span title={job.final_output_path ?? job.output_path}>{job.final_output_path ?? job.output_path}</span><small>{job.input_assembly === "GRCh37" ? "GRCh37 → GRCh38 liftover" : `${job.input_assembly} input`}</small>{job.error && <small>{job.error}</small>}</div><div className="job-actions">{job.status === "succeeded" && <button onClick={() => reviewJob(job)}>Review</button>}<button onClick={() => showLog(job.id)}>Log</button>{(job.status === "queued" || job.status === "running") && <button onClick={() => stopJob(job.id)}>Cancel</button>}</div></div>)}</div>}
    {expandedLog && <div className="log-view"><div><strong>Job log</strong><button onClick={() => setExpandedLog(null)}>×</button></div><pre>{expandedLog.text || "No log output yet."}</pre></div>}
  </section>;
}

type LegacyFileEntry = {
  isFile: boolean;
  isDirectory: boolean;
  file: (callback: (file: File) => void) => void;
  createReader: () => { readEntries: (callback: (entries: LegacyFileEntry[]) => void) => void };
};

function vcfFiles(files: File[]) {
  return files.filter((file) => /\.vcf(?:\.gz)?$/i.test(file.name));
}

async function filesFromEntry(entry: LegacyFileEntry): Promise<File[]> {
  if (entry.isFile) {
    return new Promise((resolve) => entry.file((file) => resolve([file])));
  }
  if (!entry.isDirectory) return [];
  const reader = entry.createReader();
  const entries: LegacyFileEntry[] = [];
  while (true) {
    const chunk = await new Promise<LegacyFileEntry[]>((resolve) => reader.readEntries(resolve));
    if (!chunk.length) break;
    entries.push(...chunk);
  }
  return (await Promise.all(entries.map(filesFromEntry))).flat();
}

async function droppedVcfFiles(dataTransfer: DataTransfer) {
  const entries = Array.from(dataTransfer.items)
    .map((item) => (item as DataTransferItem & { webkitGetAsEntry?: () => LegacyFileEntry | null }).webkitGetAsEntry?.())
    .filter(Boolean) as unknown as LegacyFileEntry[];
  const files = entries.length
    ? (await Promise.all(entries.map(filesFromEntry))).flat()
    : Array.from(dataTransfer.files);
  return vcfFiles(files);
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() || path;
}

function annotationOutputPath(inputPath: string, outputDirectory: string) {
  const separator = inputPath.includes("\\") && !inputPath.includes("/") ? "\\" : "/";
  const filename = fileName(inputPath).replace(/\.vcf(?:\.gz)?$/i, "") + ".vep.vcf.gz";
  const inputDirectory = inputPath.slice(0, Math.max(inputPath.lastIndexOf("/"), inputPath.lastIndexOf("\\")));
  const directory = outputDirectory.trim().replace(/[\\/]$/, "") || inputDirectory;
  return `${directory}${separator}${filename}`;
}

function uniqueAnnotationOutputPaths(inputPaths: string[], outputDirectory: string) {
  const seen = new Map<string, number>();
  return inputPaths.map((inputPath) => {
    const candidate = annotationOutputPath(inputPath, outputDirectory);
    const count = (seen.get(candidate) ?? 0) + 1;
    seen.set(candidate, count);
    return count === 1
      ? candidate
      : candidate.replace(/\.vep\.vcf\.gz$/i, `.${count}.vep.vcf.gz`);
  });
}

function GeneSet({ label, genes, defaultGenes, setter, reset, detail }: { label: string; genes: Set<string>; defaultGenes: Set<string>; setter: (genes: Set<string>) => void; reset: () => void; detail?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const openEditor = () => {
    setDraft([...genes].sort().join("\n"));
    setEditing(true);
  };
  const applyDraft = () => {
    setter(parseGeneList(draft));
    setEditing(false);
  };
  return <div className="gene-set-entry"><div className="gene-set-row"><span className="file-badge"><Icon name="file" /></span><div><strong>{label}</strong><span>{genes.size ? `${genes.size} genes loaded` : "No list loaded"}{detail ? ` · ${detail}` : ""}</span></div><div className="gene-set-actions"><button className="secondary-button" onClick={openEditor}>Edit</button><button className="secondary-button" onClick={() => ref.current?.click()}>Upload</button></div><input ref={ref} className="sr-only" type="file" accept=".txt,.csv,.tsv" onChange={(event) => { if (event.target.files?.[0]) readGeneList(event.target.files[0], setter); event.target.value = ""; }} /></div>{editing && <div className="gene-set-editor"><label><span>One gene symbol per line; commas, spaces, and pasted columns are also accepted.</span><textarea value={draft} onChange={(event) => setDraft(event.target.value)} spellCheck={false} /></label><div><button className="text-button" onClick={() => { reset(); setEditing(false); }}>{defaultGenes.size ? "Restore bundled list" : "Clear list"}</button><span>{parseGeneList(draft).size} unique genes</span><button className="secondary-button" onClick={() => setEditing(false)}>Cancel</button><button className="primary-button dark" onClick={applyDraft}>Apply list</button></div></div>}</div>;
}

function Stat({ value, label }: { value: number | string; label: string }) { return <div><strong>{typeof value === "number" ? value.toLocaleString() : value}</strong><span>{label}</span></div>; }

function GenePanel({ genes, rows, onSelect }: { genes: [string, number][]; rows: VariantRow[]; onSelect: (gene: string) => void }) {
  return <><div className="content-header"><div><p className="eyebrow">Gene-level summary</p><h1>Prioritized genes</h1><p className="subtitle">Counts reflect the active variant filters.</p></div></div><div className="gene-grid">{genes.map(([gene, count]) => { const geneRows = rows.filter((row) => row.gene === gene); const strongest = geneRows.some((row) => row.impact === "HIGH") ? "HIGH" : geneRows[0]?.impact; return <button key={gene} onClick={() => onSelect(gene)}><span className="gene-rank">{String(genes.indexOf(genes.find(([item]) => item === gene)!) + 1).padStart(2, "0")}</span><strong>{gene}</strong><span>{count} variant{count === 1 ? "" : "s"}</span><span className={`impact-pill ${strongest?.toLowerCase()}`}>{strongest}</span><Icon name="chevron" /></button>; })}</div></>;
}

function downloadTsv(rows: VariantRow[]) {
  const detectedDbnsfp = ADDITIONAL_DBNSFP_PREDICTORS.filter((definition) =>
    rows.some((row) => row.availableDbnsfpPredictors?.includes(definition.id)));
  const headers = ["sample", "variant_id", "chrom", "pos", "ref", "alt", "original_assembly", "original_chrom", "original_pos", "original_ref", "original_alt", "unscored_indel_reasons", "gene", "HGVSc", "HGVSp", "consequence", "impact", "gnomad_popmax", "gnomad_popmax_population", "gnomad_frequencies", "CADD_phred", "CADD_raw", "AlphaMissense", "AlphaMissense_pred", "REVEL", "MetaRNN", "MetaRNN_pred", "PrimateAI", "PrimateAI_pred", "SIFT", "SIFT_pred", "PolyPhen_HDIV", "PolyPhen_HDIV_pred", "GERP_RS", "phyloP100way", "phastCons100way", "LOFTEE", "LOFTEE_PTC_50BP", "LOFTEE_50BP_original", "PTC_distance_from_last_exon", "PTC_calc_status", "haplotype_frame_status", "haplotype_frame_partners", "haplotype_protein_change", "ClinVar", "ClinVar_conflicting_evidence", "SpliceAI", "promoterAI", "LoGoFunc_prediction", "LoGoFunc_neutral", "LoGoFunc_GOF", "LoGoFunc_LOF", "LoGoFunc_source_transcript", "LoGoFunc_source_HGVSp", "LoGoFunc_match", "genotype", "MANE", "PICK", "RepeatMasker", "SegDup", ...detectedDbnsfp.flatMap((definition) => [definition.scoreColumn, ...(definition.predictionColumn ? [definition.predictionColumn] : [])])];
  const body = rows.map((row) => [row.sample, fullVariantId(row), row.chrom, row.pos, row.ref, row.alt, row.originalAssembly ?? "", row.originalChrom ?? "", row.originalPos ?? "", row.originalRef ?? "", row.originalAlt ?? "", row.unscoredIndelReasons?.join("&") ?? "", row.gene, row.hgvsC, row.hgvsP, row.consequence, row.impact, row.gnomadPopmax ?? "", row.gnomadPopmaxPopulation ?? "", JSON.stringify(row.gnomadFrequencies ?? {}), row.cadd ?? "", row.caddRaw ?? "", row.alphaMissense ?? "", row.alphaPrediction, row.revel ?? "", row.metaRnn ?? "", row.metaRnnPrediction ?? "", row.primateAi ?? "", row.primateAiPrediction ?? "", row.sift ?? "", row.siftPrediction ?? "", row.polyPhen ?? "", row.polyPhenPrediction ?? "", row.gerpRs ?? "", row.phyloP100way ?? "", row.phastCons100way ?? "", row.loftee, row.loftee50bp, row.loftee50bpOriginal, row.ptcDistanceFromLastExon ?? "", row.ptcCalcStatus, row.haplotypeFrameStatus ?? "", row.haplotypeFramePartners?.join(",") ?? "", row.haplotypeProteinChange ?? "", row.clinvar, row.clinvarConflictingEvidence ?? "", row.spliceAI ?? "", row.promoterAI ?? "", row.loGoFuncPrediction, row.loGoFuncNeutral ?? "", row.loGoFuncGof ?? "", row.loGoFuncLof ?? "", row.loGoFuncSourceTranscript, row.loGoFuncSourceHgvsp, row.loGoFuncMatch, row.genotype, row.mane, row.picked, row.repeat, row.segdup, ...detectedDbnsfp.flatMap((definition) => [row.dbnsfpPredictors?.[definition.id]?.score ?? "", ...(definition.predictionColumn ? [row.dbnsfpPredictors?.[definition.id]?.prediction ?? ""] : [])])].join("\t"));
  const url = URL.createObjectURL(new Blob([[headers.join("\t"), ...body].join("\n")], { type: "text/tab-separated-values" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "iei-prioritized-variants.tsv"; anchor.click(); URL.revokeObjectURL(url);
}

function downloadCohortTsv(rows: CohortQueryRow[]) {
  const headers = ["sample", "variant", "rsid", "original_assembly", "original_chrom", "original_pos", "original_ref", "original_alt", "unscored_indel_reasons", "gene", "HGVSc", "HGVSp", "consequence", "impact", "genotype", "zygosity", "DP", "GQ", "allele_balance", "gnomAD_popmax", "CADD", "AlphaMissense", "SpliceAI", "promoterAI", "LoGoFunc_prediction", "LoGoFunc_neutral", "LoGoFunc_GOF", "LoGoFunc_LOF", "LoGoFunc_source_transcript", "LoGoFunc_source_HGVSp", "LoGoFunc_match", "ClinVar", "ClinVar_conflicting_evidence", "LOFTEE", "LOFTEE_PTC_50BP", "LOFTEE_50BP_original", "PTC_distance_from_last_exon", "PTC_calc_status", "haplotype_frame_status", "haplotype_frame_partners", "haplotype_protein_change", "haplotype_transcript", "MANE", "PICK", "source_vcf"];
  const body = rows.map((row) => [row.sample, row.variant_key, row.rsid ?? "", row.original_assembly ?? "", row.original_chrom ?? "", row.original_pos ?? "", row.original_ref ?? "", row.original_alt ?? "", row.unscored_indel_reasons, row.gene, row.hgvsc, row.hgvsp, row.consequence, row.impact, row.genotype, row.zygosity, row.dp ?? "", row.gq ?? "", row.allele_balance ?? "", row.gnomad_popmax ?? "", row.cadd ?? "", row.alpha_missense ?? "", row.spliceai ?? "", row.promoterai ?? "", row.logofunc_prediction, row.logofunc_neutral ?? "", row.logofunc_gof ?? "", row.logofunc_lof ?? "", row.logofunc_source_transcript, row.logofunc_source_hgvsp, row.logofunc_match, row.clinvar, row.clinvar_conflicting, row.loftee, row.loftee_50bp, row.loftee_50bp_original, row.ptc_distance ?? "", row.ptc_calc_status, row.haplotype_frame_status, row.haplotype_frame_partners, row.haplotype_protein_change, row.haplotype_transcript, row.mane, row.picked, row.source_path].join("\t"));
  const url = URL.createObjectURL(new Blob([[headers.join("\t"), ...body].join("\n")], { type: "text/tab-separated-values" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "iei-cohort-carriers.tsv"; anchor.click(); URL.revokeObjectURL(url);
}

function cohortRowForReview(row: CohortQueryRow): VariantRow {
  const impact = IMPACTS.includes(row.impact as typeof IMPACTS[number])
    ? row.impact as VariantRow["impact"]
    : "UNKNOWN";
  const genotypeClass = row.zygosity === "homozygous"
    ? "homozygous_alt" as const
    : row.zygosity === "hemizygous"
      ? "hemizygous" as const
      : "heterozygous" as const;
  const adAlt = row.dp !== null && row.allele_balance !== null
    ? Math.round(row.dp * row.allele_balance)
    : null;
  const adRef = row.dp !== null && adAlt !== null ? row.dp - adAlt : null;
  const genotypeEvidence = {
    gt: row.genotype,
    called: true,
    carrier: true,
    dp: row.dp,
    gq: row.gq,
    adRef,
    adAlt,
    alleleBalance: row.allele_balance,
    pl: null,
    phased: row.phased,
    phaseSet: "",
    phaseHaplotype: null,
    genotypeClass,
    genotypeFilter: "",
  };
  return {
    key: `cohort:${row.sample_entry_id}:${row.variant_key}:${row.transcript || row.gene}`,
    source: fileName(row.source_path),
    sample: row.sample,
    id: row.rsid || row.variant_key,
    chrom: row.chrom,
    pos: row.pos,
    ref: row.ref,
    alt: row.alt,
    originalAssembly: row.original_assembly ?? undefined,
    originalChrom: row.original_chrom ?? undefined,
    originalPos: row.original_pos,
    originalRef: row.original_ref ?? undefined,
    originalAlt: row.original_alt ?? undefined,
    unscoredIndelReasons: row.unscored_indel_reasons
      ? row.unscored_indel_reasons.split("&").filter(Boolean)
      : [],
    qual: row.qual,
    gene: row.gene,
    geneId: row.gene_id,
    transcript: row.transcript,
    hgvsC: row.hgvsc,
    hgvsP: row.hgvsp,
    consequence: row.consequence,
    impact,
    gnomadPopmax: row.gnomad_popmax,
    cadd: row.cadd,
    alphaMissense: row.alpha_missense,
    alphaPrediction: "",
    loftee: row.loftee,
    loftee50bp: row.loftee_50bp,
    loftee50bpOriginal: row.loftee_50bp_original,
    loftee50bpChanged: row.loftee_50bp_changed,
    ptcDistanceFromLastExon: row.ptc_distance,
    ptcCalcStatus: row.ptc_calc_status,
    clinvar: row.clinvar,
    clinvarConflictingEvidence: row.clinvar_conflicting,
    haplotypeFrameStatus: row.haplotype_frame_status as VariantRow["haplotypeFrameStatus"],
    haplotypeFramePartners: row.haplotype_frame_partners
      ? row.haplotype_frame_partners.split(",").filter(Boolean)
      : [],
    haplotypeProteinChange: row.haplotype_protein_change,
    spliceAI: row.spliceai,
    promoterAI: row.promoterai,
    loGoFuncPrediction: row.logofunc_prediction,
    loGoFuncNeutral: row.logofunc_neutral,
    loGoFuncGof: row.logofunc_gof,
    loGoFuncLof: row.logofunc_lof,
    loGoFuncAlleleAvailable: row.logofunc_allele_available,
    loGoFuncSourceTranscript: row.logofunc_source_transcript,
    loGoFuncSourceHgvsp: row.logofunc_source_hgvsp,
    loGoFuncMatch: row.logofunc_match,
    genotype: row.genotype,
    dp: row.dp,
    gq: row.gq,
    adRef,
    adAlt,
    alleleBalance: row.allele_balance,
    genotypeClass,
    genotypeFilter: "",
    sampleGenotypes: { [row.sample]: genotypeEvidence },
    mane: row.mane,
    picked: row.picked,
    repeat: row.repeat_masker,
    segdup: row.segdup,
    phase: row.phased ? "phased" : "unknown",
    otherPredictors: [],
  };
}
