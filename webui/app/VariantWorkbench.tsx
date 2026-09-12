"use client";

import Image from "next/image";
import { passesMinimumScore, screenVariantBatches } from "./review-filters";

import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { WorkbenchErrorBoundary } from "./WorkbenchErrorBoundary";
import { WorkbenchQuit } from "./WorkbenchQuit";
import { clearLegacySavedCandidates } from "./session-privacy";
import {
  cancelJob,
  getCapabilities,
  getCcreContext,
  getScreenContext,
  getScreenContextCatalog,
  getCohortStats,
  getCohortProfiles,
  getCohortSamples,
  getCohortReviewRecords,
  getCohortSampleReview,
  getCohortVariantDetail,
  getJobLog,
  getJobs,
  getResourceDownloads,
  getGeneKnowledgeStatus,
  getGeneKnowledgeFilters,
  getGeneKnowledgeGene,
  getClinGenErepoVariant,
  getGeniaVariant,
  installGeniaFiles,
  installOmimGeneKnowledge,
  startOmimGeneKnowledgeDownload,
  getWgsReviewJob,
  getPhenotypeIndividuals,
  getPhenotypeProfiles,
  getPhenotypeStats,
  getPhenotypesBySample,
  importPhenotypeInput,
  openJobReviewFile,
  openWgsReviewFile,
  prefilterWgsReview,
  filterScreenContext,
  previewPhenotypeInput,
  queryCohort,
  removeCohortSamples,
  getSampleLibrary,
  getSampleLibraryPhenotype,
  inspectSampleLibrary,
  importSampleLibrary,
  openSampleLibraryFile,
  getSampleLibraryReviewRecord,
  mapSampleLibraryIdentity,
  activateSampleLibraryVersion,
  updateSampleLibraryMetadata,
  reindexSampleLibraryDataset,
  removeSampleLibraryDatasetFromCohort,
  removeSampleLibraryDataset,
  getStorageStats,
  getStorageConfiguration,
  getStorageMigrations,
  cleanupStorage,
  chooseLocalResourceSource,
  compactStorage,
  getServiceHealth,
  getSoftwareUpdateStatus,
  checkForSoftwareUpdate,
  installSoftwareUpdate,
  rollbackSoftwareUpdate,
  type SoftwareUpdateStatus,
  type SoftwareUpdateCheck,
  type SoftwareUpdateResult,
  openStorageLocation,
  restartWorkbenchService,
  setStorageLocation,
  startStorageMigration,
  testStorageLocation,
  savePhenotypeIndividual,
  stageAnnotationFile,
  startResourceDownload,
  setupAnnotationEngine,
  startDbnsfpDownload,
  startLoGoFuncPreparation,
  startFuncVepPreparation,
  startPromoterAiPreparation,
  submitJob,
  validatePhenotypeInput,
  type AnnotationJob,
  type CohortQueryResult,
  type CohortQueryRow,
  type CohortSample,
  type CohortStats,
  type CohortProfile,
  type CohortVariantDetail,
  type CcreContext,
  type ScreenContextCatalog,
  type ScreenContextEvidence,
  type PhenotypeField,
  type PhenotypeIndividual,
  type PhenotypePreview,
  type PhenotypeProfile,
  type PhenotypeStats,
  type PhenotypeValidation,
  type ResourceDownloadJob,
  type GeneKnowledgeStatus,
  type GeneKnowledgeFilters,
  type GeneKnowledgeGene,
  type ClinGenErepoVariant,
  type GeniaComponentId,
  type GeniaVariant,
  type ServiceCapabilities,
  type StagedAnnotationFile,
  type WgsPrefilterOptions,
  type WgsReviewJob,
  type SampleLibraryDataset,
  type SampleLibraryInspection,
  type SampleLibraryImportSource,
  type StorageStats,
  type StorageConfiguration,
  type StorageLocation,
  type StorageLocationKind,
  type StorageLocationTest,
  type StorageMigrationJob,
  bulkSampleLibraryAction,
  startBulkIntake, getBulkIntake, cancelBulkIntake, type BulkIntakeJob,
  openSampleLibraryReviewSelection,
  spliceAiLookup,
  type SpliceAiLookupResult,
} from "./local-service";
import {
  ADDITIONAL_DBNSFP_PREDICTORS,
  candidateCompoundHetKeys,
  canonicalVariantKey,
  collapseToOneRowPerVariant,
  hasClinGenPathogenicEvidence,
  hasGeniaPathogenicEvidence,
  vcfSampleCount,
  NO_VARIANT_QC,
  parseVcfFiles,
  POPULATION_FREQUENCY_SOURCE_LABELS,
  type PopulationFrequencySource,
  predictorBinaryClassification,
  proteinMatchAppliesToTranscript,
  proteinMatchDisplayStatus,
  preferredClinicalTranscriptRows,
  STANDARD_VARIANT_QC,
  variantQcFailures, genotypeQcFailures, type GenotypeEvidence,
  type ImportSummary,
  type ProteinMatchDetail,
  type ProteinMatchEvidence,
  type PredictorObservation,
  type VariantQcSettings,
  type VariantRow,
} from "./vcf";
import { GlossaryText, GlossaryView } from "./glossary";
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
  isPreviousHaploinsufficiencyDefault,
  loadBundledReferences,
  parseGeneList,
  type GeneConstraint,
  type ReferenceManifest,
  type ReferenceKey,
} from "./reference-data";
import {
  activeRegulatorySet,
  readRegulatoryContextSets,
  RegulatoryEvidencePanel,
  RegulatoryFilterControl,
  RegulatorySummary,
  storeRegulatoryContextSets,
  type RegulatoryContextSet,
} from "./regulatory-evidence";

type View = "variants" | "genes" | "compound" | "saved" | "family" | "cohort" | "sample_library" | "storage" | "phenotypes" | "gene_lists" | "gene_knowledge" | "glossary" | "import" | "about";
type AnalysisScope = "exome" | "whole_genome";
type LibraryImportOptions = {
  keep: boolean;
  includeInCohort: boolean;
};
type PendingIdentity = Pick<SampleLibraryDataset, "id" | "sample_id" | "vcf_sample_name" | "individual_id">;
type LibraryVersionGroup = {
  id: string;
  versionNumber: number;
  current: boolean;
  datasets: SampleLibraryDataset[];
};
type LibraryCallsetGroup = {
  id: string;
  versions: LibraryVersionGroup[];
  current: LibraryVersionGroup | null;
};
type VersionDecision = {
  inspection: SampleLibraryInspection;
  resolve: (value: { action: "replace" | "separate"; callsetId?: string } | null) => void;
};
type LibraryReviewIdentity = Pick<
  SampleLibraryDataset,
  "id" | "sample_id" | "vcf_sample_name" | "individual_id" | "cohort_sample_entry_id"
>;

function attachLibraryIdentity(
  row: VariantRow,
  identities: ReadonlyMap<string, LibraryReviewIdentity>,
) {
  const directIdentity = identities.get(row.sample);
  if (directIdentity) {
    return {
      ...row,
      libraryDatasetId: directIdentity.id,
      librarySampleId: directIdentity.sample_id,
      libraryIndividualId: directIdentity.individual_id,
      transcriptSourceDatasetId: directIdentity.id,
      cohortSampleEntryId: directIdentity.cohort_sample_entry_id ?? undefined,
    };
  }
  // A jointly called cohort is represented by one browser row whose sample
  // label is "Cohort", so it cannot match a Sample Library individual by
  // that label. Any indexed carrier can retrieve the same complete source
  // record and restore its transcript annotations when the row is opened.
  const restorationIdentity = row.carriers
    ?.map((carrier) => identities.get(carrier.sample))
    .find((identity) => identity?.cohort_sample_entry_id);
  return restorationIdentity ? {
    ...row,
    transcriptSourceDatasetId: restorationIdentity.id,
    cohortSampleEntryId: restorationIdentity.cohort_sample_entry_id ?? undefined,
  } : row;
}

type Zygosity = "all" | "hom" | "compound" | "de_novo";
type DisplayItem =
  | "quality" | "population" | "gnomadPopulations" | "clinvar" | "transcript" | "geneConstraint"
  | "alphaMissense" | "cadd" | "spliceAI" | "promoterAI" | "loGoFunc"
  | "funcVepCti" | "funcVepCte" | "funcVepSp"
  | "alphaGenomeAvi" | "alphaGenomeAviRaw"
  | "revel" | "metaRnn" | "primateAi" | "sift" | "polyPhen"
  | "caddRaw" | "gerp" | "phyloP" | "phastCons" | "loftee";

const RESEARCH_USE_NOTICE = "Research use only. GUIDE-IEI organizes evidence but does not classify variants or generate diagnostic reports. Confirm clinically actionable findings in a certified clinical laboratory before patient care.";
const CLINVAR_AA_MATCH_HELP = "Candidate PS1/PM5 evidence only. Confirm transcript, reference amino acid, condition, review status, disease mechanism, and evidence independence before applying ACMG/AMP criteria.";

function hasClinicalProteinMatch(
  row: VariantRow,
  kind: ProteinMatchDetail["kind"],
) {
  const sources = [
    row.clinvarProteinMatch,
    row.clingenProteinMatch,
    row.geniaProteinMatch,
  ];
  if (sources.some((evidence) => {
    return proteinMatchAppliesToTranscript(evidence, kind, row.transcript);
  })) return true;
  if (row.clinvarProteinMatch) return false;
  return kind === "change" ? row.clinvarAaChangeMatch === true : row.clinvarAaMatch === true;
}

function confirmResearchUseExport() {
  return window.confirm(`${RESEARCH_USE_NOTICE}\n\nContinue with this export?`);
}

const IMPACTS = ["HIGH", "MODERATE", "LOW", "MODIFIER"] as const;
const POPMAX_PRESETS = [0.0001, 0.001, 0.01] as const;
const CODING_CONSEQUENCES = new Set([
  "coding_sequence_variant", "frameshift_variant", "inframe_deletion",
  "inframe_insertion", "missense_variant", "protein_altering_variant",
  "start_lost", "start_retained_variant", "stop_gained", "stop_lost",
  "stop_retained_variant", "synonymous_variant",
]);
const DEFAULT_DISPLAY = new Set<DisplayItem>([
  "quality", "population", "clinvar", "transcript", "geneConstraint",
  "alphaMissense", "cadd", "spliceAI", "promoterAI", "loGoFunc", "funcVepCti", "loftee",
  "alphaGenomeAvi",
]);
const DISPLAY_STORAGE_KEY = "iei-review-visible-evidence-v3";
const AVI_DISPLAY_MIGRATION_KEY = "iei-review-avi-display-v1";
const PREVIOUS_DISPLAY_STORAGE_KEY = "iei-review-visible-evidence-v2";
const LEGACY_DISPLAY_STORAGE_KEY = "iei-review-visible-evidence-v1";
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

const LOFTEE_FILTER_EXPLANATIONS: Record<string, string> = {
  END_TRUNC: "The predicted truncation is in LOFTEE's terminal truncation region, where nonsense-mediated decay may not occur.",
  INCOMPLETE_CDS: "The transcript has an incomplete coding sequence; its start or stop codon is not defined.",
  EXON_INTRON_UNDEF: "The transcript's exon/intron boundaries are undefined, so the predicted loss-of-function consequence is uncertain.",
  SMALL_INTRON: "The affected splice site belongs to an unusually small intron (less than 15 bp by default).",
  ANC_ALLELE: "The alternate allele restores the sequence inferred for the human ancestral allele.",
  NON_DONOR_DISRUPTING: "The splice-donor disruption probability does not reach LOFTEE's donor-disruption threshold.",
  NON_ACCEPTOR_DISRUPTING: "The splice-acceptor disruption probability does not reach LOFTEE's acceptor-disruption threshold.",
  RESCUE_DONOR: "A nearby in-frame donor splice site is predicted to rescue the disrupted donor site.",
  RESCUE_ACCEPTOR: "A nearby in-frame acceptor splice site is predicted to rescue the disrupted acceptor site.",
  GC_TO_GT_DONOR: "The allele creates a more canonical GT donor motif and is therefore unlikely to disrupt splicing.",
  "5UTR_SPLICE": "The essential splice consequence occurs in the transcript's 5′ UTR rather than its coding sequence.",
  "3UTR_SPLICE": "The essential splice consequence occurs in the transcript's 3′ UTR rather than its coding sequence.",
};

const LOFTEE_FLAG_EXPLANATIONS: Record<string, string> = {
  SINGLE_EXON: "The predicted loss-of-function consequence is in a single-exon transcript.",
  NAGNAG_SITE: "The splice variant is in a NAGNAG acceptor sequence that may permit frame-restoring splicing.",
  PHYLOCSF_WEAK: "The exon lacks the conservation pattern expected for a protein-coding exon.",
  PHYLOCSF_UNLIKELY_ORF: "The exon is coding-like, but the annotated reading frame is not the frame best supported by conservation.",
  NON_CAN_SPLICE: "The affected splice site is non-canonical rather than the usual GT–AG motif.",
  NO_EXON_NUMBER: "LOFTEE could not determine the transcript exon number for its terminal-position assessment.",
};

function lofteeCodes(value = "") {
  return [...new Set(value.split(/[,&|]/).map((code) => code.trim()).filter(Boolean))];
}

function lofteeExplanation(code: string, kind: "filter" | "flag") {
  const explanations = kind === "filter" ? LOFTEE_FILTER_EXPLANATIONS : LOFTEE_FLAG_EXPLANATIONS;
  return explanations[code] || `${cleanLabel(code)} (no expanded explanation is available for this LOFTEE code).`;
}

function lofteeConfidenceLabel(value: string) {
  if (value === "HC") return "HC · High-confidence predicted loss-of-function";
  if (value === "LC") return "LC · Low-confidence predicted loss-of-function";
  if (value === "OS") return "OS · Other splice consequence";
  return value || "Not applicable / not annotated";
}

function isReferenceDisruptedTranscript(row: Pick<VariantRow, "biotype">) {
  return row.biotype?.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_") === "protein_coding_lof";
}

function isStartLostVariant(row: Pick<VariantRow, "consequence">) {
  return row.consequence.split("&").includes("start_lost");
}

function lofteeDisplayLabel(row: VariantRow) {
  if (isReferenceDisruptedTranscript(row) && !row.loftee) {
    return "Not applicable · reference transcript is protein_coding_LoF";
  }
  if (isStartLostVariant(row) && !row.loftee) {
    return "Not applicable · start-loss is outside LOFTEE scope";
  }
  return lofteeConfidenceLabel(row.loftee);
}

function ptcCalculationStatusLabel(status: string) {
  if (status === "ok") return "Calculation completed";
  if (status === "ok_utr_only_terminal_exon") {
    return "Calculation completed · terminal exon contains only 3′ UTR";
  }
  if (status.startsWith("unsupported_transcript_biotype:")) {
    return `Not calculated · unsupported transcript biotype ${status.split(":", 2)[1] || "unknown"}`;
  }
  return `Not calculated · ${cleanLabel(status).toLowerCase()}`;
}

function ptcDistanceLabel(distance: number) {
  if (distance > 0) return `PTC ${distance.toLocaleString()} bp upstream of final exon junction`;
  if (distance < 0) return `PTC ${Math.abs(distance).toLocaleString()} bp downstream of final exon junction`;
  return "PTC at final exon junction";
}

function ptcRuleLabel(result: string) {
  if (result === "PASS") return "50-bp rule: PASS · PTC is >50 bp upstream; NMD expected";
  if (result === "FAIL") return "Rule suggests possible NMD escape";
  return result ? `50-bp rule: ${result}` : "";
}

function isSingleExonTranscript(exon?: string) {
  const match = (exon ?? "").trim().match(/^(?:\d+)\/(\d+)$/);
  return match?.[1] === "1";
}

function isHighImpactSpliceVariant(row: Pick<VariantRow, "impact" | "consequence">) {
  return row.impact === "HIGH" && row.consequence
    .split("&")
    .some((term) => term.startsWith("splice_") && term.endsWith("_variant"));
}

function spliceSiteLabel(row: Pick<VariantRow, "consequence" | "hgvsC">) {
  const offset = row.hgvsC.match(/([+-]\d+)(?=[ACGTN*]>)/i)?.[1] || "";
  if (row.consequence.includes("splice_donor_variant")) {
    return `Donor${offset ? ` ${offset}` : ""}`;
  }
  if (row.consequence.includes("splice_acceptor_variant")) {
    return `Acceptor${offset ? ` ${offset}` : ""}`;
  }
  return "Splice site";
}

function primarySpliceConsequenceLabel(consequence: string) {
  if (consequence.includes("splice_acceptor_variant")) return "splice acceptor variant";
  if (consequence.includes("splice_donor_variant")) return "splice donor variant";
  const primary = consequence.split("&").find((term) => term.startsWith("splice_"));
  return cleanLabel(primary || consequence);
}

function spliceAiDisplay(value: number | null) {
  return value === null ? "Not annotated" : value.toFixed(2);
}

function isHom(gt: string) {
  const alleles = gt.split(/[|/]/);
  return alleles.length === 2 && alleles[0] === alleles[1] && alleles[0] !== "0" && alleles[0] !== ".";
}

/** Label for the frequency value a row carries (review M9): the filter is
 * named after gnomAD popmax, but a row annotated without a popmax field
 * falls back to VEP's MAX_AF or the gnomAD global AF, and says so. */
function frequencySourceLabel(source: PopulationFrequencySource | undefined, short = false) {
  if (source === "max_af") return short ? "Max observed AF (MAX_AF)" : POPULATION_FREQUENCY_SOURCE_LABELS.max_af;
  if (source === "gnomad_global") return short ? "gnomAD global AF" : POPULATION_FREQUENCY_SOURCE_LABELS.gnomad_global;
  if (source === "legacy_pooled") return short ? "Population AF (pre-upgrade index)" : POPULATION_FREQUENCY_SOURCE_LABELS.legacy_pooled;
  return "gnomAD popmax";
}

function compactNumber(value: number | null, digits = 3) {
  if (value === null) return "—";
  if (value > 0 && value < 0.001) return value.toExponential(1);
  return value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
}

function alleleFrequencyLabel(value: number) {
  const decimal = value.toLocaleString("en-US", {
    useGrouping: false,
    maximumSignificantDigits: 12,
  });
  const percent = (value * 100).toLocaleString("en-US", {
    useGrouping: false,
    maximumSignificantDigits: 12,
  });
  return `${decimal} (${percent}%)`;
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

const SPLICEAI_LOOKUP_CONSENT_KEY = "guideIeiSpliceaiOnlineLookupOk";

/** Per-variant, user-initiated online SpliceAI lookup for unscored indels.
 *  The one deliberate exception to fully-local operation: only
 *  chrom/pos/ref/alt leave the machine, and only on an explicit click. */
function SpliceAiOnlineLookup({ variant }: { variant: VariantCoordinates }) {
  const [phase, setPhase] = useState<"idle" | "confirm" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<SpliceAiLookupResult | null>(null);
  const [message, setMessage] = useState("");

  const run = () => {
    setPhase("loading");
    spliceAiLookup(variant)
      .then((value) => { setResult(value); setPhase("done"); })
      .catch((reason) => {
        setMessage(reason instanceof Error ? reason.message : "The lookup failed.");
        setPhase("error");
      });
  };
  const begin = () => {
    if (window.localStorage.getItem(SPLICEAI_LOOKUP_CONSENT_KEY) === "yes") run();
    else setPhase("confirm");
  };

  if (phase === "done" && result) {
    const mane = result.transcripts[0];
    const others = result.transcripts.slice(1);
    return (
      <div className="spliceai-lookup">
        <span>SpliceAI online lookup</span>
        <div className="spliceai-lookup-body">
          {mane ? (
            <>
              <strong>{mane.gene} · {mane.transcript}{mane.mane_select ? " · MANE Select" : ""}</strong>
              <dl>
                <div><dt>Acceptor gain</dt><dd>{mane.scores.acceptor_gain.delta ?? "—"} <small>{mane.scores.acceptor_gain.position ?? ""} bp</small></dd></div>
                <div><dt>Acceptor loss</dt><dd>{mane.scores.acceptor_loss.delta ?? "—"} <small>{mane.scores.acceptor_loss.position ?? ""} bp</small></dd></div>
                <div><dt>Donor gain</dt><dd>{mane.scores.donor_gain.delta ?? "—"} <small>{mane.scores.donor_gain.position ?? ""} bp</small></dd></div>
                <div><dt>Donor loss</dt><dd>{mane.scores.donor_loss.delta ?? "—"} <small>{mane.scores.donor_loss.position ?? ""} bp</small></dd></div>
              </dl>
              {others.length > 0 && <details><summary>{others.length} more transcript{others.length > 1 ? "s" : ""}</summary>{others.map((item) => <p key={item.transcript} className="mono">{item.transcript}: AG {item.scores.acceptor_gain.delta ?? "—"} · AL {item.scores.acceptor_loss.delta ?? "—"} · DG {item.scores.donor_gain.delta ?? "—"} · DL {item.scores.donor_loss.delta ?? "—"}</p>)}</details>}
            </>
          ) : <strong>No overlapping transcript was scored.</strong>}
          <small>
            Online result from the {result.source} ({result.masked ? "masked" : "raw"} scores,
            distance {result.distance}) — not from the installed dataset.
            Retrieved {result.retrieved_at}{result.cached ? " (stored locally from an earlier lookup)" : ""}.
          </small>
        </div>
      </div>
    );
  }

  return (
    <div className="spliceai-lookup">
      <span>SpliceAI online lookup</span>
      <div className="spliceai-lookup-body">
        {phase === "confirm" ? (
          <>
            <p>
              This sends <strong>only this variant&apos;s position and alleles</strong>
              {" "}({variant.chrom}:{variant.pos} {variant.ref}›{variant.alt}) to the Broad
              Institute&apos;s public SpliceAI server. No sample, genotype, or phenotype
              information is transmitted. Results are stored locally so the variant is not
              sent again.
            </p>
            <div className="spliceai-lookup-actions">
              <button className="primary-button dark" onClick={() => { window.localStorage.setItem(SPLICEAI_LOOKUP_CONSENT_KEY, "yes"); run(); }}>Agree and look up</button>
              <button className="secondary-button" onClick={() => setPhase("idle")}>Cancel</button>
            </div>
          </>
        ) : phase === "loading" ? (
          <p>Contacting the Broad SpliceAI service… scoring an indel can take up to a minute.</p>
        ) : (
          <>
            {phase === "error" && <p className="spliceai-lookup-error">{message}</p>}
            <p>
              The precomputed SpliceAI table covers SNVs only, so this indel has no local
              score. You can request one from the Broad&apos;s public SpliceAI service —
              an explicit online lookup for this single variant.
            </p>
            <div className="spliceai-lookup-actions">
              <button className="secondary-button" onClick={begin}>{phase === "error" ? "Try again" : "Get SpliceAI score online (Broad lookup)"}</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function fullVariantId(row: VariantCoordinates) {
  return `${row.chrom}:${row.pos}:${row.ref}:${row.alt}`;
}

function regulatoryVariantKey(row: VariantCoordinates) {
  return fullVariantId(row);
}

function hasCodingTranscriptConsequence(row: Pick<VariantRow, "consequence">) {
  return row.consequence.split(/[&,]/).some((term) => CODING_CONSEQUENCES.has(term.trim()));
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
    const genes = Array.isArray(stored)
      ? new Set(stored.map((gene) => String(gene).toUpperCase()))
      : new Set(fallback);
    if (key === "hi" && isPreviousHaploinsufficiencyDefault(genes, fallback)) {
      localStorage.removeItem(GENE_SET_STORAGE_KEYS[key]);
      return new Set(fallback);
    }
    return genes;
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
  const [quitMessage, setQuitMessage] = useState("");
  const [rows, setRows] = useState<VariantRow[]>([]);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [reviewAnalysisScope, setReviewAnalysisScope] = useState<AnalysisScope>("exome");
  const [privacyWarning, setPrivacyWarning] = useState(false);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      try {
        setPrivacyWarning(!clearLegacySavedCandidates(window.localStorage));
      } catch {
        setPrivacyWarning(true);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);
  const replaceRows: Dispatch<SetStateAction<VariantRow[]>> = useCallback((next) => {
    setRows(next);
    setSaved(new Set());
  }, []);
  if (quitMessage) return <main className="app-shell"><h1>GUIDE-IEI</h1><p role="status">{quitMessage}</p></main>;
  return <>
    {privacyWarning && <div className="alert error" role="alert">Legacy saved-candidate browser data could not be removed. Clear this site’s browser data to remove earlier patient-derived bookmarks. New stars are session-only.</div>}
    <WorkbenchErrorBoundary>
      <WorkbenchSession onQuit={setQuitMessage} rows={rows} setRows={replaceRows} summary={summary} setSummary={setSummary}
        saved={saved} setSaved={setSaved} reviewAnalysisScope={reviewAnalysisScope} setReviewAnalysisScope={setReviewAnalysisScope} />
    </WorkbenchErrorBoundary>
  </>;
}

function WorkbenchSession({ onQuit, rows, setRows, summary, setSummary, saved, setSaved, reviewAnalysisScope, setReviewAnalysisScope }: {
  onQuit: (message: string) => void;
  rows: VariantRow[]; setRows: Dispatch<SetStateAction<VariantRow[]>>;
  summary: ImportSummary | null; setSummary: Dispatch<SetStateAction<ImportSummary | null>>;
  saved: Set<string>; setSaved: Dispatch<SetStateAction<Set<string>>>;
  reviewAnalysisScope: AnalysisScope; setReviewAnalysisScope: Dispatch<SetStateAction<AnalysisScope>>;
}) {
  const [view, setView] = useState<View>("import");
  const [query, setQuery] = useState("");
  // Filtering re-runs over every row; letting React defer the search value
  // keeps typing responsive on large imports (audit H9).
  const deferredQuery = useDeferredValue(query);
  const [samples, setSamples] = useState<Set<string>>(new Set());
  const [impacts, setImpacts] = useState<Set<string>>(new Set(["HIGH", "MODERATE"]));
  const [popmax, setPopmax] = useState<number | null>(0.01);
  const [popmaxDraft, setPopmaxDraft] = useState("0.01");
  const [popmaxError, setPopmaxError] = useState("");
  const [maneOnly, setManeOnly] = useState(true);
  const [excludeRepeat, setExcludeRepeat] = useState(true);
  const [excludeSegdup, setExcludeSegdup] = useState(true);
  const [clinvarOnly, setClinvarOnly] = useState(false);
  const [clinvarConflictOnly, setClinvarConflictOnly] = useState(false);
  const [clingenPathogenicOnly, setClingenPathogenicOnly] = useState(false);
  const [geniaPathogenicOnly, setGeniaPathogenicOnly] = useState(false);
  const [proteinChangeMatchOnly, setProteinChangeMatchOnly] = useState(false);
  const [proteinResidueMatchOnly, setProteinResidueMatchOnly] = useState(false);
  const [excludeConfirmedFrameRestored, setExcludeConfirmedFrameRestored] = useState(true);
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
  const [aviMin, setAviMin] = useState<number | null>(null);
  const [spliceMin, setSpliceMin] = useState<number | null>(null);
  const [promoterAbsMin, setPromoterAbsMin] = useState<number | null>(null);
  const [loGoFuncClass, setLoGoFuncClass] = useState<"all" | "GOF" | "LOF" | "Neutral">("all");
  const [loGoFuncMin, setLoGoFuncMin] = useState<number | null>(null);
  const [constraints, setConstraints] = useState<Map<string, GeneConstraint>>(new Map());
  const [referenceManifest, setReferenceManifest] = useState<ReferenceManifest | null>(null);
  const [referenceError, setReferenceError] = useState("");
  const [referenceFailures, setReferenceFailures] = useState<Partial<Record<ReferenceKey, string>>>({});
  const [referencesLoading, setReferencesLoading] = useState(true);
  const [referenceAttempt, setReferenceAttempt] = useState(0);
  const [visibleInfo, setVisibleInfo] = useState<Set<DisplayItem>>(DEFAULT_DISPLAY);
  const [visibleDbnsfpPredictors, setVisibleDbnsfpPredictors] = useState<Set<string>>(new Set());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [selected, setSelected] = useState<VariantRow | null>(null);
  const transcriptDetailCache = useRef(new Map<string, VariantRow>());
  const [importing, setImporting] = useState(false);
  const [importProgress, setImportProgress] = useState("");
  const [wgsImportJob, setWgsImportJob] = useState<WgsReviewJob | null>(null);
  const [importError, setImportError] = useState("");
  const [pendingReviewFiles, setPendingReviewFiles] = useState<File[]>([]);
  const [pendingIdentityDatasets, setPendingIdentityDatasets] = useState<PendingIdentity[]>([]);
  const [versionDecision, setVersionDecision] = useState<VersionDecision | null>(null);
  const [phenotypeTarget, setPhenotypeTarget] = useState<string | null>(null);
  const [ieiGenes, setIeiGenes] = useState<Set<string>>(new Set());
  const [hiGenes, setHiGenes] = useState<Set<string>>(new Set());
  const [dominantGenes, setDominantGenes] = useState<Set<string>>(new Set());
  const [geneKnowledgeFilters, setGeneKnowledgeFilters] = useState<GeneKnowledgeFilters | null>(null);
  const [iuisCategory, setIuisCategory] = useState("");
  const [omimAssociatedOnly, setOmimAssociatedOnly] = useState(false);
  const [geniaGeiOnly, setGeniaGeiOnly] = useState(false);
  const [customGeneLists, setCustomGeneLists] = useState<CustomGeneList[]>([]);
  const [bundledGeneSets, setBundledGeneSets] = useState<BundledGeneSets>({
    iei: new Set(),
    hi: new Set(),
    dominant: new Set(),
  });
  const [trio, setTrio] = useState<TrioDefinition | null>(null);
  const [pedigreeTrios, setPedigreeTrios] = useState<TrioDefinition[]>([]);
  const [pedigreeWarnings, setPedigreeWarnings] = useState<string[]>([]);
  const [trioThresholds, setTrioThresholds] = useState<TrioThresholds>({ ...DEFAULT_TRIO_THRESHOLDS });
  const [screenCatalog, setScreenCatalog] = useState<ScreenContextCatalog | null>(null);
  const [regulatoryContextSets, setRegulatoryContextSets] = useState<RegulatoryContextSet[]>([]);
  const [activeRegulatorySetId, setActiveRegulatorySetId] = useState("immune-core");
  const [regulatoryFilterEnabled, setRegulatoryFilterEnabled] = useState(false);
  const [regulatoryMatches, setRegulatoryMatches] = useState<Set<string> | null>(null);
  const [regulatoryResultInput, setRegulatoryResultInput] = useState<symbol | null>(null);
  const [regulatoryFilterLoading, setRegulatoryFilterLoading] = useState(false);
  const [regulatoryFilterProgress, setRegulatoryFilterProgress] = useState("");
  const [regulatoryFilterError, setRegulatoryFilterError] = useState("");

  useEffect(() => {
    if (!selected?.compactTranscriptView) return;
    const row = selected;
    const cacheKey = [
      row.transcriptSourceDatasetId ?? "", row.cohortSampleEntryId ?? "",
      normalizedVariantKey(row), row.sample,
    ].join("|");
    const cached = transcriptDetailCache.current.get(cacheKey);
    if (cached) {
      setSelected(cached);
      return;
    }
    if (!row.transcriptSourceDatasetId && !row.cohortSampleEntryId) {
      const unavailable = {
        ...row,
        compactTranscriptView: false,
        transcriptDetailStatus: "unavailable" as const,
        transcriptDetailError: "The complete transcript table remains in the Sample Library, but its Cohort Search entry is unavailable. Add or repair this sample in Cohort Search, then reopen it.",
      };
      transcriptDetailCache.current.set(cacheKey, unavailable);
      setSelected(unavailable);
      return;
    }

    let active = true;
    setSelected((current) => current?.key === row.key
      ? { ...current, transcriptDetailStatus: "loading" }
      : current);
    void (async () => {
      const fullRows: VariantRow[] = [];
      let originalSourceError: unknown = null;
      if (row.transcriptSourceDatasetId) {
        try {
          const source = await getSampleLibraryReviewRecord(
            row.transcriptSourceDatasetId, normalizedVariantKey(row),
          );
          const parsed = await parseVcfFiles([
            new File([source.vcf], source.name, { type: "text/vcf" }),
          ], { intake: "server-records" });
          fullRows.push(...parsed.rows.filter((candidate) => (
            candidate.sample === source.sample
            && normalizedVariantKey(candidate) === source.variant_key
            && !candidate.compactTranscriptView
          )));
        } catch (reason) {
          originalSourceError = reason;
        }
      }
      if (!fullRows.length && row.cohortSampleEntryId) {
        const source = await getCohortReviewRecords([{
          variant_key: normalizedVariantKey(row),
          sample_entry_id: row.cohortSampleEntryId,
        }]);
        for (const sourceFile of source.files) {
          const selection = sourceFile.selections.find((item) => (
            item.sample_entry_id === row.cohortSampleEntryId
            && item.variant_key === normalizedVariantKey(row)
          ));
          if (!selection) continue;
          const parsed = await parseVcfFiles([
            new File([sourceFile.vcf], sourceFile.name, { type: "text/vcf" }),
          ], { intake: "server-records" });
          fullRows.push(...parsed.rows.filter((candidate) => (
            candidate.sample === selection.sample
            && normalizedVariantKey(candidate) === selection.variant_key
            && !candidate.compactTranscriptView
          )));
        }
      }
      if (!fullRows.length) {
        if (originalSourceError instanceof Error) throw originalSourceError;
        throw new Error("The indexed source record could not be restored.");
      }
      const representative = fullRows.find((candidate) => (
        candidate.gene === row.gene && candidate.transcript === row.transcript
      )) ?? fullRows.find((candidate) => candidate.gene === row.gene && candidate.mane)
        ?? null;
      const enriched: VariantRow = {
        ...row,
        compactTranscriptView: false,
        transcriptDetailStatus: "loaded",
        transcriptDetailError: undefined,
        collapsedTranscriptRows: representative
          ? fullRows.filter((candidate) => candidate !== representative)
          : fullRows,
      };
      transcriptDetailCache.current.set(cacheKey, enriched);
      if (active) {
        setSelected((current) => current?.key === row.key ? enriched : current);
      }
    })().catch((reason) => {
      const unavailable: VariantRow = {
        ...row,
        compactTranscriptView: false,
        transcriptDetailStatus: "unavailable",
        transcriptDetailError: reason instanceof Error
          ? `Complete transcript annotations could not be opened: ${reason.message}`
          : "Complete transcript annotations could not be opened from Cohort Search.",
      };
      transcriptDetailCache.current.set(cacheKey, unavailable);
      if (active) {
        setSelected((current) => current?.key === row.key ? unavailable : current);
      }
    });
    return () => { active = false; };
  }, [selected?.key, selected?.compactTranscriptView, selected?.transcriptSourceDatasetId, selected?.cohortSampleEntryId]); // eslint-disable-line react-hooks/exhaustive-deps

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
      setReferenceFailures(references.errors);
      setReferenceError(Object.values(references.errors).join("; "));
      setReferencesLoading(false);
    }).catch((error) => {
      if (!active) return;
      setReferenceError(error instanceof Error ? error.message : "Bundled reference data could not be loaded.");
      setReferenceFailures({ iei: "Unavailable", hi: "Unavailable", dominant: "Unavailable", constraints: "Unavailable" });
      setReferencesLoading(false);
    });
    return () => { active = false; };
  }, [referenceAttempt]);

  useEffect(() => {
    let active = true;
    const load = () => getGeneKnowledgeFilters()
      .then((filters) => { if (active) setGeneKnowledgeFilters(filters); })
      .catch(() => { if (active) setGeneKnowledgeFilters(null); });
    void load();
    const reload = () => { void load(); };
    window.addEventListener("gene-knowledge-updated", reload);
    return () => { active = false; window.removeEventListener("gene-knowledge-updated", reload); };
  }, []);

  useEffect(() => {
    let active = true;
    const frame = window.requestAnimationFrame(() => {
      if (active) setRegulatoryContextSets(readRegulatoryContextSets());
    });
    getScreenContextCatalog().then((value) => {
      if (!active) return;
      setScreenCatalog(value);
      if (value.presets.length && !value.presets.some((item) => item.id === activeRegulatorySetId)) {
        setActiveRegulatorySetId(value.presets[0].id);
      }
    }).catch(() => {
      if (active) setScreenCatalog({ available: false, registry: "SCREEN Registry V4", assembly: "GRCh38", tissues: [], immune_contexts: [], presets: [] });
    });
    return () => { active = false; window.cancelAnimationFrame(frame); };
    // The first available preset is selected only during initial hydration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      try {
        const currentPreference = localStorage.getItem(DISPLAY_STORAGE_KEY);
        const previousPreference = localStorage.getItem(PREVIOUS_DISPLAY_STORAGE_KEY);
        const stored = JSON.parse(
          currentPreference ?? previousPreference ?? localStorage.getItem(LEGACY_DISPLAY_STORAGE_KEY) ?? "null",
        );
        if (Array.isArray(stored)) {
          let migrated = stored.flatMap((item: unknown) =>
            item === "funcVep" ? ["funcVepCti"] : typeof item === "string" ? [item] : [],
          );
          if (currentPreference === null) {
            migrated = migrated.filter((item) => item !== "funcVepCte" && item !== "funcVepSp");
            if (!migrated.includes("funcVepCti")) migrated.push("funcVepCti");
          }
          if (localStorage.getItem(AVI_DISPLAY_MIGRATION_KEY) === null) {
            if (!migrated.includes("alphaGenomeAvi")) migrated.push("alphaGenomeAvi");
          }
          setVisibleInfo(new Set(migrated as DisplayItem[]));
        }
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
      localStorage.setItem(AVI_DISPLAY_MIGRATION_KEY, "1");
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
  const selectedIuisCategoryGenes = useMemo(
    () => new Set(iuisCategory ? geneKnowledgeFilters?.iuis_category_genes[iuisCategory] ?? [] : []),
    [geneKnowledgeFilters, iuisCategory],
  );
  const omimGenes = useMemo(() => new Set(geneKnowledgeFilters?.omim_genes ?? []), [geneKnowledgeFilters]);
  const geniaGeiGenes = useMemo(() => new Set(geneKnowledgeFilters?.genia_gei_genes ?? []), [geneKnowledgeFilters]);
  const referenceUnavailable = (key: ReferenceKey) => referencesLoading || Boolean(referenceFailures[key]);
  const referenceFilterBlocked = (ieiOnly && referenceUnavailable("iei"))
    || (hiOnly && referenceUnavailable("hi")) || (dominantOnly && referenceUnavailable("dominant"))
    || (lofConstrainedOnly && referenceUnavailable("constraints"));
  const referenceNote = (key: ReferenceKey, ready: string) => referencesLoading
    ? "Loading reference…" : referenceFailures[key] ? "Unavailable — see reference warning" : ready;

  const eligibleRows = useMemo(() => referencedRows.filter((row) => {
    if (!includeQcFailing && variantQcFailures(row, qcSettings).length) return false;
    const q = deferredQuery.trim().toLowerCase();
    if (q && ![row.gene, row.id, row.hgvsC, row.hgvsP, row.sample, `${row.chrom}:${row.pos}`, fullVariantId(row)].some((value) => value.toLowerCase().includes(q))) return false;
    if (samples.size && !samples.has(row.sample)) return false;
    if (impacts.size && !impacts.has(row.impact)) return false;
    if (popmax !== null && row.gnomadPopmax !== null && row.gnomadPopmax > popmax) return false;
    if (maneOnly && !preferredClinicalKeys.has(row.key)) return false;
    if (excludeRepeat && row.repeat) return false;
    if (excludeSegdup && row.segdup) return false;
    if (clinvarOnly && !isPathogenic(row.clinvar)) return false;
    if (clingenPathogenicOnly && !hasClinGenPathogenicEvidence(row.clingenErepo)) return false;
    if (geniaPathogenicOnly && !hasGeniaPathogenicEvidence(row.genia)) return false;
    if (proteinChangeMatchOnly && !hasClinicalProteinMatch(row, "change")) return false;
    if (proteinResidueMatchOnly && !hasClinicalProteinMatch(row, "residue")) return false;
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
    if (iuisCategory && !selectedIuisCategoryGenes.has(row.gene.toUpperCase())) return false;
    if (omimAssociatedOnly && !omimGenes.has(row.gene.toUpperCase())) return false;
    if (geniaGeiOnly && !geniaGeiGenes.has(row.gene.toUpperCase())) return false;
    if (selectedCustomListIds.size && !selectedCustomGenes.has(row.gene.toUpperCase())) return false;
    if (alphaMin !== null && (row.alphaMissense === null || row.alphaMissense < alphaMin)) return false;
    if (caddMin !== null && (row.cadd === null || row.cadd < caddMin)) return false;
    if (reviewAnalysisScope === "whole_genome" && !passesMinimumScore(row.alphaGenomeAviPhred, aviMin)) return false;
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
  }), [referencedRows, preferredClinicalKeys, deferredQuery, samples, impacts, popmax, maneOnly, excludeRepeat, excludeSegdup, clinvarOnly, clinvarConflictOnly, clingenPathogenicOnly, geniaPathogenicOnly, proteinChangeMatchOnly, proteinResidueMatchOnly, excludeConfirmedFrameRestored, ieiOnly, hiOnly, dominantOnly, lofConstrainedOnly, iuisCategory, selectedIuisCategoryGenes, omimAssociatedOnly, omimGenes, geniaGeiOnly, geniaGeiGenes, selectedCustomListIds, selectedCustomGenes, alphaMin, caddMin, aviMin, reviewAnalysisScope, spliceMin, promoterAbsMin, loGoFuncClass, loGoFuncMin, ieiGenes, hiGenes, dominantGenes, lofConstrainedGenes, qcSettings, includeQcFailing]);

  const selectedRegulatorySet = useMemo(
    () => activeRegulatorySet(screenCatalog, regulatoryContextSets, activeRegulatorySetId),
    [screenCatalog, regulatoryContextSets, activeRegulatorySetId],
  );
  const regulatoryInput = useMemo(() => ({ token: Symbol("SCREEN input"), eligibleRows, selectedRegulatorySet }), [eligibleRows, selectedRegulatorySet]);

  useEffect(() => {
    if (reviewAnalysisScope !== "whole_genome") {
      return;
    }
    if (!regulatoryFilterEnabled) {
      return;
    }
    if (!screenCatalog?.available || !selectedRegulatorySet) {
      const frame = window.requestAnimationFrame(() => {
        setRegulatoryMatches(null);
        setRegulatoryResultInput(regulatoryInput.token);
        setRegulatoryFilterLoading(false);
        setRegulatoryFilterError("Prepared SCREEN context data or a context set is unavailable.");
      });
      return () => window.cancelAnimationFrame(frame);
    }
    const unique = new Map<string, { key: string; chrom: string; pos: number; ref: string; alt: string }>();
    eligibleRows.forEach((row) => {
      const key = regulatoryVariantKey(row);
      unique.set(key, { key, chrom: row.chrom, pos: row.pos, ref: row.ref, alt: row.alt });
    });
    const variants = [...unique.values()];
    let active = true;
    (async () => {
      await Promise.resolve();
      if (!active) return;
      setRegulatoryFilterLoading(true);
      setRegulatoryMatches(null);
      setRegulatoryResultInput(regulatoryInput.token);
      setRegulatoryFilterError("");
      setRegulatoryFilterProgress(`Screening 0 of ${variants.length.toLocaleString()} unique variants…`);
      await screenVariantBatches(variants, (batch) => filterScreenContext({
          variants: batch,
          tissue_ids: selectedRegulatorySet.tissueIds,
          immune_context_ids: selectedRegulatorySet.immuneContextIds,
          mode: "any",
        }), (matches, tested, total) => {
          setRegulatoryMatches(matches);
          setRegulatoryFilterProgress(`Screened ${tested.toLocaleString()} of ${total.toLocaleString()} unique variants · ${matches.size.toLocaleString()} matches so far`);
        }, () => active);
    })().catch((reason: unknown) => {
      if (active) {
        setRegulatoryMatches(null);
        setRegulatoryFilterError(reason instanceof Error ? reason.message : "SCREEN context filtering failed.");
      }
    }).finally(() => {
      if (active) {
        setRegulatoryFilterLoading(false);
        setRegulatoryFilterProgress("");
      }
    });
    return () => { active = false; };
  }, [reviewAnalysisScope, regulatoryFilterEnabled, selectedRegulatorySet, screenCatalog?.available, eligibleRows, regulatoryInput]);

  const regulatoryCurrentMatches = regulatoryResultInput === regulatoryInput.token ? regulatoryMatches : null;
  const regulatoryCurrentError = regulatoryResultInput === regulatoryInput.token ? regulatoryFilterError : "";
  const regulatoryScreenPending = reviewAnalysisScope === "whole_genome" && regulatoryFilterEnabled
    && !regulatoryCurrentError && (regulatoryResultInput !== regulatoryInput.token || regulatoryFilterLoading);
  const regulatoryScreenError = reviewAnalysisScope === "whole_genome" && regulatoryFilterEnabled ? regulatoryCurrentError : "";

  const screenFilteredRows = useMemo(
    () => {
      if (reviewAnalysisScope !== "whole_genome" || !regulatoryFilterEnabled) {
        return eligibleRows;
      }
      // Only show confirmed matches for this exact input/context selection.
      // The results panel labels partial results and errors explicitly.
      if (!regulatoryCurrentMatches) return [];
      return eligibleRows.filter((row) => regulatoryCurrentMatches.has(regulatoryVariantKey(row)));
    },
    [eligibleRows, reviewAnalysisScope, regulatoryFilterEnabled, regulatoryCurrentMatches],
  );

  const qcFailingCalls = useMemo(
    () => new Set(
      referencedRows
        .filter((row) => variantQcFailures(row, qcSettings).length)
        .map((row) => `${row.source}:${row.chrom}:${row.pos}:${row.ref}:${row.alt}:${row.sample}`),
    ).size,
    [referencedRows, qcSettings],
  );

  const compoundKeys = useMemo(
    () => candidateCompoundHetKeys(screenFilteredRows),
    [screenFilteredRows],
  );
  const trioPairs = useMemo(
    () => trio ? compoundHetPairs(screenFilteredRows, trio, trioThresholds) : [],
    [screenFilteredRows, trio, trioThresholds],
  );
  const trioCandidatePairs = useMemo(
    () => trioPairs.filter((pair) => pair.phase !== "cis" && pair.phase !== "excluded_hemizygous"),
    [trioPairs],
  );
  const trioCompoundVariantKeys = useMemo(
    () => new Set(trioCandidatePairs.flatMap((pair) => [pair.first.key, pair.second.key])),
    [trioCandidatePairs],
  );
  const deNovoAssessments = useMemo(() => {
    const assessments = new Map<string, DeNovoAssessment>();
    if (trio) screenFilteredRows.forEach((row) => assessments.set(row.key, assessDeNovo(row, trio, trioThresholds)));
    return assessments;
  }, [screenFilteredRows, trio, trioThresholds]);
  const deNovoCandidateKeys = useMemo(() => new Set(
    [...deNovoAssessments.entries()]
      .filter(([, assessment]) => ["high_confidence", "possible", "possible_parental_mosaicism"].includes(assessment.status))
      .map(([key]) => key),
  ), [deNovoAssessments]);

  const filtered = useMemo(() => screenFilteredRows.filter((row) => {
    if (zygosity === "hom" && !isHom(row.genotype)) return false;
    if (zygosity === "compound" && !(trio ? trioCompoundVariantKeys.has(row.key) : compoundKeys.has(`${row.sample}:${row.gene}`))) return false;
    if (zygosity === "de_novo" && !deNovoCandidateKeys.has(row.key)) return false;
    if (view === "saved" && !saved.has(row.key)) return false;
    if (view === "compound" && !(trio ? trioCompoundVariantKeys.has(row.key) : compoundKeys.has(`${row.sample}:${row.gene}`))) return false;
    return true;
  }), [screenFilteredRows, zygosity, trio, trioCompoundVariantKeys, compoundKeys, deNovoCandidateKeys, view, saved]);

  // Retain reference-disrupted transcript consequences for review, while
  // placing them after consequences modeled against an intact reference ORF.
  const [oneRowPerVariant, setOneRowPerVariant] = useState<boolean>(() =>
    typeof window === "undefined" || window.localStorage.getItem("guideIeiOneRowPerVariant") !== "off");
  useEffect(() => {
    window.localStorage.setItem("guideIeiOneRowPerVariant", oneRowPerVariant ? "on" : "off");
  }, [oneRowPerVariant]);
  const collapsedRows = useMemo(
    () => (oneRowPerVariant ? collapseToOneRowPerVariant(filtered) : filtered),
    [filtered, oneRowPerVariant],
  );
  const prioritizedRows = useMemo(() => [...collapsedRows].sort(
    (left, right) => Number(isReferenceDisruptedTranscript(left)) - Number(isReferenceDisruptedTranscript(right)),
  ), [collapsedRows]);

  const uniqueSamples = [...new Set(referencedRows.map((row) => row.sample))];
  useEffect(() => {
    // A configured trio must not survive an import that no longer contains
    // its three samples; stale sample names silently zeroed every family
    // result while the panel still claimed to be configured.
    if (!trio) return;
    const present = new Set(uniqueSamples);
    if (![trio.proband, trio.mother, trio.father].every((sample) => present.has(sample))) {
      setTrio(null);
    }
  }, [uniqueSamples, trio]); // eslint-disable-line react-hooks/exhaustive-deps
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
    libraryOptions: LibraryImportOptions = {
      keep: true, includeInCohort: true,
    },
    exomeCohortPopmax: number | null = 0.01,
  ) {
    if (!list?.length && !workstationPaths.length) return;
    let savedDatasetCount = 0;
    setImporting(true);
    setImportError("");
    setWgsImportJob(null);
    try {
      let reviewFiles = Array.from(list ?? []);
      const wgsMessages: string[] = [];
      const librarySources: SampleLibraryImportSource[] = [];
      // Cohort exome files (16+ samples) take the same server-side prepared
      // route as whole-genome imports, without changing the exome scope:
      // PASS-or-unfiltered + gnomAD popmax, coding regions only — the score
      // and regulatory routes stay disabled.
      let serverPrepFilters: WgsPrefilterOptions | undefined =
        analysisScope === "whole_genome" ? wgsFilters : undefined;
      if (analysisScope === "exome") {
        for (const file of reviewFiles) {
          if ((await vcfSampleCount(file)) >= 16) {
            serverPrepFilters = {
              max_gnomad_popmax: exomeCohortPopmax,
              min_spliceai: null,
              min_promoterai_abs: null,
              noncoding_mode: "none",
            };
            wgsMessages.push(
              `Cohort exome import: prepared on the local service (gnomAD popmax ${exomeCohortPopmax === null ? "filter off" : `≤ ${exomeCohortPopmax}`}, coding regions).`,
            );
            break;
          }
        }
      }
      if (serverPrepFilters) {
        if (analysisScope === "whole_genome" && !wgsFilters) throw new Error("Whole-genome prefilter settings are required.");
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
          let job = await prefilterWgsReview(source.path, serverPrepFilters);
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
          librarySources.push({
            review_id: filtered.id,
            original_path: source.path,
            original_name: source.name,
            source_record_count: filtered.records_scanned,
            retained_record_count: filtered.records_retained,
          });
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
      } else if (libraryOptions.keep) {
        const batch = `sample-library-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        for (let index = 0; index < reviewFiles.length; index += 1) {
          setImportProgress(`Preparing persistent copy ${index + 1} of ${reviewFiles.length}: ${reviewFiles[index].name}`);
          const staged = await stageAnnotationFile(reviewFiles[index], batch);
          librarySources.push({
            path: staged.path,
            original_path: staged.path,
            original_name: reviewFiles[index].name,
          });
        }
      }
      let library: Awaited<ReturnType<typeof importSampleLibrary>> | null = null;
      if (libraryOptions.keep) {
        setImportProgress("Checking whether these samples are already in the Sample Library…");
        const inspections = await inspectSampleLibrary({
          sources: librarySources,
          analysis_scope: analysisScope,
        });
        for (let index = 0; index < inspections.length; index += 1) {
          const inspection = inspections[index];
          if (inspection.status !== "possible_update") continue;
          const decision = await new Promise<{ action: "replace" | "separate"; callsetId?: string } | null>((resolve) => {
            setVersionDecision({ inspection, resolve });
          });
          if (!decision) throw new Error("Import cancelled before the Sample Library was changed.");
          librarySources[index] = {
            ...librarySources[index],
            identity_action: decision.action,
            replace_callset_id: decision.callsetId,
          };
        }
        setImportProgress(`Saving to the Sample Library${libraryOptions.includeInCohort ? " and building Cohort Search…" : "…"}`);
        library = await importSampleLibrary({
          sources: librarySources,
          analysis_scope: analysisScope,
          index_scope: "compact",
          include_in_cohort: libraryOptions.includeInCohort,
          qc_settings: { ...qcSettings, preset: qcPreset, include_failing: includeQcFailing },
          prefilter_settings: serverPrepFilters ?? {},
          retention_routes: analysisScope === "whole_genome"
            ? ["coding/essential-splice", "SpliceAI", "promoterAI", wgsFilters?.noncoding_mode === "ccre" ? "ENCODE cCRE" : wgsFilters?.noncoding_mode === "all" ? "all noncoding" : "no additional noncoding", "unscored relevant indels"]
            : ["exome region"],
        });
        savedDatasetCount = library.datasets.length;
        // Review the durable managed copies, not the transient upload. This
        // makes persistence and cohort indexing independent of browser row
        // expansion and guarantees the screen reflects what was saved.
        const managedReviewFiles: File[] = [];
        for (let index = 0; index < library.imports.length; index += 1) {
          const item = library.imports[index];
          setImportProgress(`Opening managed review ${index + 1} of ${library.imports.length}…`);
          managedReviewFiles.push(await openSampleLibraryReviewSelection(
            item.datasets.map((dataset) => dataset.id),
            `managed-review-${index + 1}.vcf.gz`,
          ));
        }
        reviewFiles = managedReviewFiles;
      }
      setImportProgress("Preparing the browser review…");
      const result = await parseVcfFiles(reviewFiles, {
        intake: libraryOptions.keep || analysisScope === "whole_genome" ? "prepared-review" : "user",
        aggregateMaxPopmax: analysisScope === "exome" ? exomeCohortPopmax : undefined,
        // A retained dataset keeps every source annotation in the managed
        // VCF. Materialize a bounded clinical view in the browser and restore
        // the complete record from Cohort Search when a variant is opened.
        clinicalTranscriptsOnly: libraryOptions.keep,
      });
      result.summary.warnings.unshift(...wgsMessages);
      if (library) {
        const identityBySample = new Map(library.datasets.map((dataset) => [dataset.vcf_sample_name, dataset]));
        result.rows = result.rows.map((row) => attachLibraryIdentity(row, identityBySample));
        setPendingIdentityDatasets(library.imports.flatMap((item) =>
          ["new", "separate_dataset"].includes(item.import_outcome)
            ? item.datasets.filter((dataset) => !dataset.individual_id)
            : []
        ));
        // The backend reports per-file warnings — most importantly a failed
        // cohort-index update. Composing an unconditional success line here
        // told the reviewer samples were searchable when they were not.
        const libraryWarnings = library.imports.flatMap((item) => item.warnings ?? []);
        const cohortFailures = library.imports.filter((item) =>
          (item.warnings ?? []).some((warning) => warning.includes("Repair Cohort Search"))
        ).length;
        const newCallsets = library.imports.filter((item) => ["new", "separate_dataset"].includes(item.import_outcome)).length;
        const updatedCallsets = library.imports.filter((item) => ["updated_annotation", "updated_version"].includes(item.import_outcome)).length;
        const reusedCallsets = library.imports.filter((item) => item.import_outcome === "exact_current").length;
        const previousCopies = library.imports.filter((item) => item.import_outcome === "exact_previous").length;
        const libraryActions = [
          newCallsets ? `${newCallsets} new source dataset${newCallsets === 1 ? "" : "s"} saved` : "",
          updatedCallsets ? `${updatedCallsets} source dataset${updatedCallsets === 1 ? "" : "s"} updated with version history retained` : "",
          reusedCallsets ? `${reusedCallsets} exact duplicate${reusedCallsets === 1 ? "" : "s"} reused` : "",
          previousCopies ? `${previousCopies} already-retained previous version${previousCopies === 1 ? "" : "s"} reopened without replacing the current version` : "",
        ].filter(Boolean);
        if (libraryOptions.includeInCohort && cohortFailures > 0) {
          result.summary.warnings.unshift(
            `Sample Library import completed, but Cohort Search could not be updated for ${cohortFailures} of ${library.imports.length} file${library.imports.length === 1 ? "" : "s"}. The retained reviews are safe — open Sample Library and choose Repair Cohort Search.`,
            ...libraryWarnings.filter((warning) => !warning.includes("Repair Cohort Search")),
          );
        } else {
          result.summary.warnings.unshift(
            `${libraryActions.join("; ")}.${libraryOptions.includeInCohort && (newCallsets + updatedCallsets) > 0 ? " New current versions are available in Cohort Search." : ""}`,
            ...libraryWarnings,
          );
        }
      } else {
        result.summary.warnings.unshift("Review-once import: this dataset was not saved in the Sample Library or Cohort Search.");
      }
      setRows(result.rows);
      setSummary(result.summary);
      setReviewAnalysisScope(analysisScope);
      if (analysisScope !== "whole_genome") {
        setRegulatoryFilterEnabled(false);
        setRegulatoryMatches(null);
      }
      setView("variants");
      setSelected(null);
      setSamples(new Set());
      setPendingReviewFiles([]);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Could not parse the selected VCF files.";
      setImportError(savedDatasetCount
        ? `${savedDatasetCount} sample dataset${savedDatasetCount === 1 ? " was" : "s were"} saved before the browser review failed. Open it from Sample Library. ${detail}`
        : detail);
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

  function saveRegulatoryContextSets(sets: RegulatoryContextSet[]) {
    setRegulatoryContextSets(sets);
    storeRegulatoryContextSets(sets);
  }

  function resetFilters() {
    setQuery(""); setSamples(new Set()); setImpacts(new Set(["HIGH", "MODERATE"]));
    setPopmax(0.01); setPopmaxDraft("0.01"); setPopmaxError(""); setManeOnly(true); setExcludeRepeat(true); setExcludeSegdup(true);
    setClinvarOnly(false); setClinvarConflictOnly(false); setClingenPathogenicOnly(false); setGeniaPathogenicOnly(false);
    setProteinChangeMatchOnly(false); setProteinResidueMatchOnly(false);
    setExcludeConfirmedFrameRestored(true);
    setIncludeQcFailing(false);
    setIeiOnly(false); setHiOnly(false); setDominantOnly(false); setLofConstrainedOnly(false);
    setIuisCategory(""); setOmimAssociatedOnly(false); setGeniaGeiOnly(false);
    setSelectedCustomListIds(new Set());
    setRegulatoryFilterEnabled(false); setRegulatoryMatches(null);
    setZygosity("all"); setAlphaMin(null); setCaddMin(null); setAviMin(null); setSpliceMin(null); setPromoterAbsMin(null); setLoGoFuncClass("all"); setLoGoFuncMin(null);
  }

  function selectPopmax(value: number | null) {
    setPopmax(value);
    setPopmaxError("");
    if (value !== null) {
      setPopmaxDraft(value.toLocaleString("en-US", {
        useGrouping: false,
        maximumSignificantDigits: 12,
      }));
    }
  }

  function applyCustomPopmax() {
    const text = popmaxDraft.trim();
    if (!/^(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) {
      setPopmaxError("Enter a number from 0 to 1, such as 0, 0.00001, or 1e-5.");
      return;
    }
    const value = Number(text);
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      setPopmaxError("Enter a number from 0 to 1, such as 0, 0.00001, or 1e-5.");
      return;
    }
    selectPopmax(value);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><Image className="brand-mark" src="/favicon.svg" width={36} height={36} alt="" unoptimized /><span>GUIDE-IEI</span><span className="version">MVP 0.6</span></div>
        <div className="top-actions">
          <span className="research-use-label">Research use only</span>
          <button className="primary-button" onClick={() => { setView("import"); setSelected(null); }}><Icon name="upload" />Import VCF</button>
          <WorkbenchQuit onQuit={onQuit} />
        </div>
      </header>

      {referenceError && <div className="alert error" role="alert">
        <strong>Some bundled references are unavailable.</strong> {referenceError}.
        <p>Affected gene-set and constraint filters cannot be used; other references remain available. This is not evidence that a gene lacks an association or constraint.</p>
        <button className="secondary-button" disabled={referencesLoading} onClick={() => { setReferencesLoading(true); setReferenceAttempt((attempt) => attempt + 1); }}>{referencesLoading ? "Retrying…" : "Retry references"}</button>
      </div>}

      <div className={`workspace ${selected ? "review-mode" : ""} ${view === "cohort" ? "cohort-mode" : ""} ${view === "sample_library" ? "library-mode" : ""} ${view === "storage" || view === "about" ? "storage-mode" : ""} ${view === "phenotypes" ? "phenotype-mode" : ""} ${view === "family" ? "family-mode" : ""} ${view === "gene_lists" || view === "gene_knowledge" || view === "glossary" ? "gene-lists-mode" : ""} ${view === "import" ? "import-mode" : ""}`}>
        <nav className="rail" aria-label="Primary navigation">
          <div className="nav-group-label">Review</div>
          {([
            ["variants", "Variants", filtered.length], ["genes", "Genes", geneCounts.length],
            ["compound", "Comp het", trio ? trioCandidatePairs.length : compoundKeys.size], ["saved", "Saved", saved.size],
          ] as const).map(([id, label, count]) => (
            <button key={id} className={`nav-item ${view === id ? "active" : ""}`} onClick={() => setView(id)}><span>{label}</span><span>{referenceFilterBlocked ? "—" : count}</span></button>
          ))}
          {!summary?.cohortMode && <button className={`nav-item ${view === "family" ? "active" : ""}`} onClick={() => { setView("family"); setSelected(null); }}><span>Family analysis</span><span>{trio ? deNovoCandidateKeys.size + trioCandidatePairs.length : "—"}</span></button>}
          <div className="nav-group-label secondary">Data</div>
          <button className={`nav-item ${view === "sample_library" ? "active" : ""}`} onClick={() => { setView("sample_library"); setSelected(null); }}><span>Sample library</span><Icon name="file" /></button>
          <button className={`nav-item ${view === "cohort" ? "active" : ""}`} onClick={() => { setView("cohort"); setSelected(null); }}><span>Cohort search</span><Icon name="search" /></button>
          <button className={`nav-item ${view === "phenotypes" ? "active" : ""}`} onClick={() => { setPhenotypeTarget(null); setView("phenotypes"); setSelected(null); }}><span>Phenotypes</span><Icon name="file" /></button>
          <button className={`nav-item ${view === "gene_knowledge" ? "active" : ""}`} onClick={() => { setView("gene_knowledge"); setSelected(null); }}><span>Gene knowledge</span><Icon name="dna" /></button>
          <button className={`nav-item ${view === "gene_lists" ? "active" : ""}`} onClick={() => { setView("gene_lists"); setSelected(null); }}><span>Gene lists</span><span>{4 + customGeneLists.length}</span></button>
          <button className={`nav-item ${view === "glossary" ? "active" : ""}`} onClick={() => { setView("glossary"); setSelected(null); }}><span>Glossary</span><Icon name="file" /></button>
          <button className={`nav-item ${view === "storage" ? "active" : ""}`} onClick={() => { setView("storage"); setSelected(null); }}><span>Storage</span><Icon name="file" /></button>
          <button className={`nav-item ${view === "about" ? "active" : ""}`} onClick={() => { setView("about"); setSelected(null); }}><span>About &amp; updates</span><Icon name="star" /></button>
          <button className={`nav-item ${view === "import" ? "active" : ""}`} onClick={() => setView("import")}><span>Import & QC</span><Icon name="chevron" /></button>
          <div className="rail-note"><strong>Defaults active</strong><span>PASS upstream</span><span>MANE transcripts</span><span>Repeat/SegDup excluded</span></div>
        </nav>

        <aside className="filters">
          <div className="filter-heading"><span><Icon name="filter" />Filters</span><button onClick={resetFilters}>Reset</button></div>
          <label className="search"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Gene, HGVS, ID, locus…" /></label>

          <FilterSection title="Samples" count={samples.size}>
            {uniqueSamples.map((sample) => <Check key={sample} label={sample} checked={samples.has(sample)} onChange={(checked) => setSamples((current) => toggleSet(current, sample, checked))} />)}
          </FilterSection>
          <FilterSection title="Gene sets" count={Number(ieiOnly) + Number(hiOnly) + Number(dominantOnly) + Number(lofConstrainedOnly) + Number(Boolean(iuisCategory)) + Number(omimAssociatedOnly) + Number(geniaGeiOnly) + selectedCustomListIds.size}>
            <Check label="IUIS 2024 IEI" checked={ieiOnly} onChange={setIeiOnly} disabled={!ieiOnly && referenceUnavailable("iei")} note={referenceNote("iei", `${ieiGenes.size} genes · bundled/custom`)} />
            <Check label="IEI autosomal-dominant" checked={dominantOnly} onChange={setDominantOnly} disabled={!dominantOnly && referenceUnavailable("dominant")} note={referenceNote("dominant", `${dominantGenes.size} genes · bundled/custom`)} />
            <Check label="IEI haploinsufficiency" checked={hiOnly} onChange={setHiOnly} disabled={!hiOnly && referenceUnavailable("hi")} note={referenceNote("hi", `${hiGenes.size} genes · curated/custom`)} />
            <Check label="LoF constrained" checked={lofConstrainedOnly} onChange={setLofConstrainedOnly} disabled={!lofConstrainedOnly && referenceUnavailable("constraints")} note={referenceNote("constraints", `${lofConstrainedGenes.size} genes · pLI ≥0.9 or LOEUF <0.6`)} />
            <label className="field-label">IUIS category</label><select value={iuisCategory} onChange={(event) => setIuisCategory(event.target.value)}><option value="">No IUIS category filter</option>{geneKnowledgeFilters?.iuis_categories.map((item) => <option key={item.category} value={item.category}>{item.category} ({item.genes})</option>)}</select>
            <Check label="OMIM-associated gene" checked={omimAssociatedOnly} onChange={setOmimAssociatedOnly} disabled={!omimGenes.size} note={omimGenes.size ? `${omimGenes.size} genes · locally licensed data` : "OMIM is not installed"} />
            <Check label="GenIA GEI gene" checked={geniaGeiOnly} onChange={setGeniaGeiOnly} disabled={!geniaGeiGenes.size} note={geniaGeiGenes.size ? `${geniaGeiGenes.size} genes · GEI gene–disease list` : "Unavailable · GenIA GEI list is not installed"} />
            {customGeneLists.map((list) => <Check key={list.id} label={list.name} checked={selectedCustomListIds.has(list.id)} onChange={(checked) => setSelectedCustomListIds((current) => toggleSet(current, list.id, checked))} note={`${list.genes.size} custom genes`} />)}
            {!customGeneLists.length && <button className="filter-link" onClick={() => { setView("gene_lists"); setSelected(null); }}>Create a custom list</button>}
            {selectedCustomListIds.size > 1 && <p className="microcopy">Selected custom lists are combined as a union.</p>}
          </FilterSection>
          <FilterSection title="Impact" count={impacts.size}>
            <div className="chip-grid">{IMPACTS.map((impact) => <button key={impact} className={`impact-chip ${impact.toLowerCase()} ${impacts.has(impact) ? "selected" : ""}`} onClick={() => setImpacts((current) => toggleSet(current, impact, !current.has(impact)))}>{impact}</button>)}</div>
          </FilterSection>
          <FilterSection title="Population frequency">
            <div className="frequency-current"><span>gnomAD popmax</span><strong>{popmax === null ? "No limit" : `≤ ${alleleFrequencyLabel(popmax)}`}</strong></div>
            <div className="frequency-presets" aria-label="gnomAD popmax presets">
              {POPMAX_PRESETS.map((value) => <button type="button" className={popmax === value ? "active" : ""} aria-pressed={popmax === value} key={value} onClick={() => selectPopmax(value)}>≤ {value}</button>)}
              <button type="button" className={popmax === null ? "active" : ""} aria-pressed={popmax === null} onClick={() => selectPopmax(null)}>No limit</button>
            </div>
            <label className={`frequency-custom ${popmax !== null && !POPMAX_PRESETS.includes(popmax as typeof POPMAX_PRESETS[number]) ? "active" : ""}`}>
              <span>Custom</span>
              <div><input type="text" inputMode="decimal" value={popmaxDraft} aria-invalid={Boolean(popmaxError)} aria-describedby="popmax-help" onChange={(event) => { setPopmaxDraft(event.target.value); setPopmaxError(""); }} onKeyDown={(event) => { if (event.key === "Enter") applyCustomPopmax(); }} placeholder="0.00001 or 1e-5"/><button type="button" onClick={applyCustomPopmax}>Apply</button></div>
            </label>
            {popmaxError && <p className="frequency-error" role="alert">{popmaxError}</p>}
            <p className="microcopy" id="popmax-help">Uses the annotation&apos;s gnomAD popmax when present; otherwise VEP&apos;s MAX_AF (highest AF across 1000 Genomes, ESP and gnomAD), then the gnomAD global AF — never a mix. Each variant names the source it used. At 0, only variants with a recorded 0 or no value remain. Missing values are retained at every threshold.</p>
          </FilterSection>
          <FilterSection title="Clinical database">
            <Check label="ClinVar P / LP only" checked={clinvarOnly} onChange={(checked) => { setClinvarOnly(checked); if (checked) setClinvarConflictOnly(false); }} />
            <Check label="ClinVar conflict with ≥1 P / LP" checked={clinvarConflictOnly} onChange={(checked) => { setClinvarConflictOnly(checked); if (checked) setClinvarOnly(false); }} />
            <Check label="ClinGen P / LP only" checked={clingenPathogenicOnly} onChange={setClingenPathogenicOnly} />
            <Check label="GenIA P / LP only" checked={geniaPathogenicOnly} onChange={setGeniaPathogenicOnly} />
            <Check label="P / LP same protein change · any clinical source" checked={proteinChangeMatchOnly} onChange={setProteinChangeMatchOnly} />
            <Check label="P / LP different missense at same residue · any clinical source" checked={proteinResidueMatchOnly} onChange={setProteinResidueMatchOnly} />
          </FilterSection>
          <FilterSection title="Prediction scores">
            <p className="microcopy" id="prediction-threshold-help">A threshold keeps only variants that <em>have</em> that score at or above it: variants with no value for the predictor are excluded while the threshold is set, unlike the frequency filter, where a missing value is retained. Coverage differs by predictor — AlphaMissense scores missense SNVs only, dbNSFP CADD covers SNVs (the optional CADD dataset adds indels), SpliceAI covers SNVs and short indels near splice sites, PromoterAI scores promoter SNVs. Leave a threshold empty to keep unscored variants.</p>
            <Threshold label="AlphaMissense ≥" value={alphaMin} placeholder="optional" onChange={setAlphaMin} />
            <Threshold label="CADD phred ≥" value={caddMin} placeholder="optional" onChange={setCaddMin} />
            {reviewAnalysisScope === "whole_genome" && <><Threshold label="AlphaGenome AVI Phred ≥" value={aviMin} placeholder="optional" onChange={setAviMin} min={0} /><p className="microcopy">Genome-wide SNV score. A threshold excludes unscored variants, including indels; leave empty for no AVI filter.</p></>}
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
            <Check label="Hide confirmed frame-restored events" checked={excludeConfirmedFrameRestored} onChange={setExcludeConfirmedFrameRestored} note="Hidden by default; possible/unphased events always remain visible" />
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
          {reviewAnalysisScope === "whole_genome" && <FilterSection title="Regulatory context" count={regulatoryFilterEnabled && regulatoryCurrentMatches ? regulatoryCurrentMatches.size : undefined}>
            <RegulatoryFilterControl
              catalog={screenCatalog}
              activeSetId={activeRegulatorySetId}
              setActiveSetId={setActiveRegulatorySetId}
              customSets={regulatoryContextSets}
              enabled={regulatoryFilterEnabled}
              setEnabled={(value) => {
                setRegulatoryFilterEnabled(value);
                // Regulatory records are MODIFIER impact; the impact chips
                // gate the rows this filter sees, so widen them on enable.
                if (value) setImpacts(new Set(IMPACTS));
                if (!value) {
                  setRegulatoryMatches(null);
                  setRegulatoryFilterLoading(false);
                  setRegulatoryFilterProgress("");
                  setRegulatoryFilterError("");
                }
              }}
              loading={regulatoryScreenPending}
              progress={regulatoryFilterProgress}
              error={regulatoryScreenError}
            />
          </FilterSection>}
        </aside>

        <section className="content">
          {view === "cohort" ? (
            <CohortPanel onReview={(reviewRows, reviewSummary, analysisScope) => {
              setRows(reviewRows);
              setSummary(reviewSummary);
              setReviewAnalysisScope(analysisScope);
              if (analysisScope !== "whole_genome") {
                setRegulatoryFilterEnabled(false);
                setRegulatoryMatches(null);
              }
              setQuery("");
              setImpacts(new Set());
              setPopmax(null);
              setPopmaxError("");
              setManeOnly(true);
              setExcludeRepeat(false);
              setExcludeSegdup(false);
              setClinvarOnly(false);
              setClinvarConflictOnly(false);
              setClingenPathogenicOnly(false);
              setGeniaPathogenicOnly(false);
              setProteinChangeMatchOnly(false);
              setProteinResidueMatchOnly(false);
              setExcludeConfirmedFrameRestored(true);
              setIeiOnly(false);
              setHiOnly(false);
              setDominantOnly(false);
              setLofConstrainedOnly(false);
              setIuisCategory("");
              setOmimAssociatedOnly(false);
              setGeniaGeiOnly(false);
              setSelectedCustomListIds(new Set());
              setAlphaMin(null);
              setCaddMin(null);
              setAviMin(null);
              setSpliceMin(null);
              setPromoterAbsMin(null);
              setZygosity("all");
              setIncludeQcFailing(true);
              setSamples(new Set());
              setView("variants");
              setSelected(reviewRows[0] ?? null);
            }} />
          ) : view === "sample_library" ? (
            <SampleLibraryPanel onReview={(reviewRows, reviewSummary, analysisScope) => {
              setRows(reviewRows); setSummary(reviewSummary); setReviewAnalysisScope(analysisScope);
              resetFilters();
              setView("variants"); setSelected(null);
            }} onManagePhenotype={(individualId) => { setPhenotypeTarget(individualId ?? null); setView("phenotypes"); }} />
          ) : view === "storage" ? (
            <StoragePanel />
          ) : view === "about" ? (
            <AboutPanel />
          ) : view === "phenotypes" ? (
            <PhenotypePanel initialIndividualId={phenotypeTarget} />
          ) : view === "gene_knowledge" ? (
            <GeneKnowledgeSettingsPanel />
          ) : view === "glossary" ? (
            <GlossaryView />
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
          ) : referenceFilterBlocked ? (
            <div className="alert error" role="alert"><strong>Results paused: a selected reference filter is unavailable.</strong> Retry loading references, or uncheck the affected filter. No result or export is shown until the selected filters can be evaluated.</div>
          ) : view === "family" ? (
            <FamilyPanel
              rows={screenFilteredRows}
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
            <GenePanel genes={geneCounts} rows={prioritizedRows} onSelect={(gene) => { setQuery(gene); setView("variants"); }} />
          ) : selected ? (
            <VariantReviewWorkspace
              rows={prioritizedRows}
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
              analysisScope={reviewAnalysisScope}
              screenCatalog={screenCatalog}
              setScreenCatalog={setScreenCatalog}
              activeRegulatorySetId={activeRegulatorySetId}
              setActiveRegulatorySetId={setActiveRegulatorySetId}
              regulatoryContextSets={regulatoryContextSets}
              setRegulatoryContextSets={saveRegulatoryContextSets}
              onManagePhenotype={() => { setPhenotypeTarget(selected.libraryIndividualId ?? null); setSelected(null); setView("phenotypes"); }}
            />
          ) : (
            <>
              {view === "saved" && <p className="alert" role="note">Candidate stars are session-only. Export TSV to keep a record before loading another dataset, refreshing, or closing this tab.</p>}
              <div className="content-header">
                <div><p className="eyebrow">{summary ? `${summary.files} imported file${summary.files === 1 ? "" : "s"}` : "No VCF imported"}</p><h1>{view === "compound" ? "Candidate compound heterozygotes" : view === "saved" ? "Saved candidates" : "Prioritized variants"}</h1><p className="subtitle">{regulatoryScreenPending ? "Partial results · " : regulatoryScreenError ? "SCREEN unavailable · " : ""}{filtered.length} transcript-level rows · {new Set(filtered.map((row) => row.gene)).size} genes · {(() => { const count = summary?.cohortMode ? summary.samples : new Set(filtered.map((row) => row.sample)).size; return `${count} sample${count === 1 ? "" : "s"}`; })()}</p></div>
                <div className="header-controls"><DisplaySettingsButton open={settingsOpen} setOpen={setSettingsOpen} visibleInfo={visibleInfo} setVisibleInfo={setVisibleInfo} availableDbnsfpPredictors={availableDbnsfpPredictors} visibleDbnsfpPredictors={visibleDbnsfpPredictors} setVisibleDbnsfpPredictors={setVisibleDbnsfpPredictors} oneRowPerVariant={oneRowPerVariant} setOneRowPerVariant={setOneRowPerVariant} /><button className="secondary-button" disabled={regulatoryScreenPending || Boolean(regulatoryScreenError)} onClick={() => { if (confirmResearchUseExport()) downloadTsv(filtered, qcSettings); }}>Export TSV</button></div>
              </div>
              {summary && <div className="qc-strip"><span><strong>{summary.samples}</strong> sample{summary.samples === 1 ? "" : "s"}</span><span><strong>{summary.intakeQc.filter((check) => check.status === "pass").length}</strong> intake checks passed</span><span><strong>{qcFailingCalls}</strong> calls {includeQcFailing ? "flagged" : "hidden by QC"}</span>{(summary.warnings.length > 0 || summary.intakeQc.some((check) => check.status === "warning")) && <button onClick={() => setView("import")}>Review intake QC</button>}</div>}
              {regulatoryScreenPending && <div className="alert" role="status" aria-live="polite"><strong>SCREEN filtering in progress — results are incomplete.</strong><p>{regulatoryFilterProgress || "Starting SCREEN regulatory-activity checks…"}</p><p>Confirmed matches appear as screening finishes each batch. A blank list during screening does not mean that no variants qualify.</p></div>}
              {regulatoryScreenError && <div className="alert error" role="alert"><strong>SCREEN filtering could not finish.</strong><p>{regulatoryScreenError}</p><p>No final filtered result is available. Turn off the SCREEN filter to see the other results.</p></div>}
              {!regulatoryScreenError && (!regulatoryScreenPending || prioritizedRows.length > 0) && <VariantTable rows={prioritizedRows} hasImportedData={Boolean(summary)} saved={saved} setSaved={setSaved} setSelected={setSelected} compoundKeys={compoundKeys} visibleInfo={visibleInfo} trio={trio} trioThresholds={trioThresholds} trioCompoundVariantKeys={trioCompoundVariantKeys} qcSettings={qcSettings} />}
            </>
          )}
        </section>
      </div>
      {pendingIdentityDatasets.length > 0 && <IdentityMappingDialog
        datasets={pendingIdentityDatasets}
        onComplete={() => setPendingIdentityDatasets([])}
      />}
      {versionDecision && <DatasetVersionDialog
        key={`${versionDecision.inspection.path}:${versionDecision.inspection.matches.map((match) => match.callset_id).join(",")}`}
        inspection={versionDecision.inspection}
        onChoose={(choice) => {
          const resolve = versionDecision.resolve;
          setVersionDecision(null);
          resolve(choice);
        }}
      />}
    </main>
  );
}

function FilterSection({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return <section className="filter-section"><h2>{title}{count ? <span>{count}</span> : null}</h2><div className="filter-body">{children}</div></section>;
}

function Check({ label, checked, onChange, note, disabled = false }: { label: string; checked: boolean; onChange: (value: boolean) => void; note?: string; disabled?: boolean }) {
  return <label className={`check-row ${disabled ? "disabled" : ""}`}><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span className="custom-check" /><span>{label}{note && <small>{note}</small>}</span></label>;
}

function Threshold({ label, value, placeholder, onChange, disabled = false, min }: { label: string; value: number | null; placeholder: string; onChange: (value: number | null) => void; disabled?: boolean; min?: number }) {
  return <label className="threshold"><span>{label}</span><input type="number" min={min} step="0.01" value={value ?? ""} placeholder={placeholder} disabled={disabled} onChange={(event) => { const input = event.target; if (input.value === "") onChange(null); else if (Number.isFinite(input.valueAsNumber) && (min === undefined || input.valueAsNumber >= min)) onChange(input.valueAsNumber); }} /></label>;
}

function carrierQcFailures(
  carrier: { sample: string; evidence: GenotypeEvidence },
  row: VariantRow,
  settings: VariantQcSettings,
) {
  const evidence = carrier.evidence;
  return genotypeQcFailures({
    dp: evidence.dp, gq: evidence.gq, adRef: evidence.adRef,
    adAlt: evidence.adAlt, alleleBalance: evidence.alleleBalance,
    genotype: evidence.gt, genotypeClass: evidence.genotypeClass,
    genotypeFilter: evidence.genotypeFilter,
  }, row, settings);
}

function toggleSet<T>(current: Set<T>, item: T, checked: boolean) {
  const next = new Set(current);
  if (checked) next.add(item);
  else next.delete(item);
  return next;
}

const GENIA_LIST_CLASS_ORDER = ["P", "LP", "VUS", "LB", "B", "NC", "RF"];

function geniaListClassifications(records: VariantRow["genia"]) {
  const classifications = new Set(
    (records ?? [])
      .map((record) => record.classCode.trim().toUpperCase())
      .filter(Boolean),
  );
  return [...classifications].sort((left, right) => {
    const leftIndex = GENIA_LIST_CLASS_ORDER.indexOf(left);
    const rightIndex = GENIA_LIST_CLASS_ORDER.indexOf(right);
    if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  });
}

// Rows rendered before the reviewer asks for more. A relaxed filter on a
// multi-sample exome can match 10^5 transcript rows; putting all of them in
// the DOM at once froze the tab (audit H9). The count is a display bound
// only — filtering, sorting and exports still see every matching row.
const TABLE_RENDER_STEP = 500;

type VariantTableRowProps = {
  row: VariantRow;
  isSaved: boolean;
  isCompound: boolean;
  trio: TrioDefinition | null;
  trioThresholds: TrioThresholds;
  visibleInfo: Set<DisplayItem>;
  qcSettings: VariantQcSettings;
  onSelect: (row: VariantRow) => void;
  onToggleSaved: (key: string, saved: boolean) => void;
};

const VariantTableRow = memo(function VariantTableRow({ row, isSaved, isCompound, trio, trioThresholds, visibleInfo, qcSettings, onSelect, onToggleSaved }: VariantTableRowProps) {
  // Per-row derivations run once per row identity/props change instead of
  // once per parent render for every row.
  const deNovo = useMemo(() => (trio ? assessDeNovo(row, trio, trioThresholds) : null), [row, trio, trioThresholds]);
  const qcFailures = useMemo(() => variantQcFailures(row, qcSettings), [row, qcSettings]);
  const geniaClassifications = useMemo(() => geniaListClassifications(row.genia), [row.genia]);
  return <tr onClick={() => onSelect(row)}>
    <td><button className={`star-button ${isSaved ? "saved" : ""}`} title="Candidate stars are session-only; export the Saved view to keep them." aria-label={isSaved ? "Remove saved candidate" : "Save candidate for this session"} onClick={(event) => { event.stopPropagation(); onToggleSaved(row.key, !isSaved); }}><Icon name="star" /></button></td>
    <td><VariantIdentifier row={row}/><span className="cell-sub">{row.sample} · {row.genotype}</span></td>
    <td><strong className="gene" title={row.gene}>{row.gene}</strong>{isCompound && <span className="mini-badge amber">{trio ? "comp het candidate" : "comp het?"}</span>}</td>
    <td><span className="truncate-hgvs" title={row.hgvsP || row.hgvsC || undefined}>{row.hgvsP || row.hgvsC || "—"}</span><span className="cell-sub truncate-hgvs" title={row.hgvsP && row.hgvsC ? row.hgvsC : undefined}>{row.hgvsP ? row.hgvsC : ""}</span></td>
    <td><span className={`impact-pill ${row.impact.toLowerCase()}`}>{row.impact}</span><span className="cell-sub consequence">{row.consequence.replaceAll("_", " ")}</span></td>
      <td className={row.gnomadPopmax !== null && row.gnomadPopmax > 0.001 ? "muted-value" : ""}>{compactNumber(row.gnomadPopmax)}</td>
    <td><div className="predictor-pair">{visibleInfo.has("alphaMissense") && <Score label="AM" value={row.alphaMissense} strong={(row.alphaMissense ?? 0) >= 0.564} />}{visibleInfo.has("cadd") && <Score label="CADD" value={row.cadd} strong={(row.cadd ?? 0) >= 20} />}</div></td>
    <td>{visibleInfo.has("loftee") && row.haplotypeFrameStatus === "FRAME_RESTORED_CONFIRMED" ? <><span className="mini-badge amber">Not LoF after haplotype</span><span className="cell-sub">per-variant LOFTEE {row.loftee || "—"}</span></> : visibleInfo.has("loftee") && row.loftee ? <><span className={`mini-badge ${row.loftee === "HC" ? "teal" : ""}`}>{row.loftee}</span>{lofteeCodes(row.lofteeFilter)[0] && <span className="cell-sub" title={lofteeExplanation(lofteeCodes(row.lofteeFilter)[0], "filter")}>{cleanLabel(lofteeCodes(row.lofteeFilter)[0])}</span>}{row.haplotypeFrameStatus && <span className="cell-sub">{haplotypeFrameLabel(row.haplotypeFrameStatus)}</span>}</> : visibleInfo.has("loftee") && isReferenceDisruptedTranscript(row) ? <><span className="mini-badge amber">Not applicable</span><span className="cell-sub">reference-disrupted transcript</span></> : visibleInfo.has("loftee") && isStartLostVariant(row) ? <><span className="mini-badge">Not applicable</span><span className="cell-sub">start-loss outside LOFTEE scope</span></> : "—"}</td>
    <td>{row.clinvar ? <><span className={`clinvar ${isPathogenic(row.clinvar) || isClinvarConflictWithPathogenic(row.clinvar, row.clinvarConflictingEvidence) ? "pathogenic" : ""}`}>{row.clinvar.replaceAll("_", " ")}</span>{isClinvarConflictWithPathogenic(row.clinvar, row.clinvarConflictingEvidence) && <span className="cell-sub">includes ≥1 P / LP submission</span>}</> : "—"}</td>
    <td>{geniaClassifications.length > 0 ? <div className="genia-list-codes">{geniaClassifications.map((classification) => <span className={`class-${classification.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`} title={geniaClassificationLabel(classification)} key={classification}>{classification}</span>)}</div> : "—"}</td>
    <td><div className="flags">{qcFailures.length > 0 && <span className="qc-fail-flag" title={qcFailures.join("; ")}>QC fail</span>}{row.unscoredIndelReasons?.length ? <span className="qc-warn-flag" title={row.unscoredIndelReasons.map(unscoredIndelReasonLabel).join("; ")}>Unscored indel</span> : null}{row.duplicateRecord && <span className="qc-warn-flag" title={duplicateRecordLabel(row)}>{row.duplicateRecordKind === "liftover_collision" ? "Lift-over collision" : "Duplicate record"}</span>}{deNovo && ["high_confidence", "possible", "possible_parental_mosaicism"].includes(deNovo.status) && <span className={`family-flag ${deNovo.status}`}>{deNovoLabel(deNovo.status)}</span>}{row.haplotypeFrameStatus && <span>{haplotypeFrameLabel(row.haplotypeFrameStatus)}</span>}{isReferenceDisruptedTranscript(row) && <span className="qc-warn-flag" title="The reference transcript is protein_coding_LoF; its reference haplotype already has a disrupted ORF.">Reference-disrupted transcript</span>}{row.liftedFromGrch37 && <span>Lifted from GRCh37</span>}{row.assemblyAlleleSwap && <span title="A source allele became the GRCh38 reference; genotype and allele-indexed annotations were remapped">Assembly allele swap</span>}{row.mane && <span title={row.maneSelect ? "MANE Select transcript" : "MANE Plus Clinical transcript — an additional clinically designated transcript alongside MANE Select"}>{row.maneSelect ? "MANE Select" : "MANE Plus Clinical"}</span>}{row.picked && !row.mane && <span>PICK fallback</span>}{row.collapsedTranscriptRows?.length ? <span className="collapse-chip" title={"Also annotated on:\n" + row.collapsedTranscriptRows.map((other) => `${other.gene} · ${other.transcript || "—"}${other.maneSelect ? " · MANE Select" : other.mane ? " · MANE Plus Clinical" : ""}`).join("\n") + "\nOpen the variant for the full transcript table."}>+{row.collapsedTranscriptRows.length}</span> : null}{visibleInfo.has("loGoFunc") && row.loGoFuncPrediction && <span title={`Source ${row.loGoFuncSourceTranscript}`}>LoGoFunc {row.loGoFuncPrediction}</span>}{row.promoterAI !== null && <span>promoterAI</span>}{row.repeat && <span>Repeat</span>}{row.segdup && <span>SegDup</span>}</div></td>
  </tr>;
});

function VariantTable({ rows, hasImportedData, saved, setSaved, setSelected, compoundKeys, visibleInfo, trio, trioThresholds, trioCompoundVariantKeys, qcSettings }: { rows: VariantRow[]; hasImportedData: boolean; saved: Set<string>; setSaved: React.Dispatch<React.SetStateAction<Set<string>>>; setSelected: (row: VariantRow | null) => void; compoundKeys: Set<string>; visibleInfo: Set<DisplayItem>; trio: TrioDefinition | null; trioThresholds: TrioThresholds; trioCompoundVariantKeys: Set<string>; qcSettings: VariantQcSettings }) {
  // Bounded rendering: the limit resets whenever a different row set
  // arrives (derived during render, no effect needed).
  const [renderState, setRenderState] = useState<{ rows: VariantRow[]; limit: number }>({ rows, limit: TABLE_RENDER_STEP });
  const renderLimit = renderState.rows === rows ? renderState.limit : TABLE_RENDER_STEP;
  const onSelect = useCallback((row: VariantRow) => setSelected(row), [setSelected]);
  const onToggleSaved = useCallback((key: string, nextSaved: boolean) => setSaved((current) => toggleSet(current, key, nextSaved)), [setSaved]);
  if (!rows.length) return <div className="empty-state"><span className="empty-icon"><Icon name={hasImportedData ? "filter" : "upload"} /></span><h2>{hasImportedData ? "No variants match these filters" : "No variants loaded"}</h2><p>{hasImportedData ? "Relax one or more filters, or import another VEP-annotated VCF." : "Use Import & QC to annotate a VCF or review an existing VEP-annotated file."}</p></div>;
  const visibleRows = rows.length > renderLimit ? rows.slice(0, renderLimit) : rows;
  const hidden = rows.length - visibleRows.length;
  return <div className="table-frame"><div className="table-scroll"><table><thead><tr><th className="save-col" /><th>Variant / sample</th><th>Gene</th><th>HGVS</th><th>Consequence</th><th>gnomAD<br/>popmax</th><th>Predictors</th><th>LOFTEE</th><th>ClinVar</th><th>GenIA</th><th>Flags</th></tr></thead><tbody>{visibleRows.map((row) => (
    <VariantTableRow
      key={row.key}
      row={row}
      isSaved={saved.has(row.key)}
      isCompound={trio ? trioCompoundVariantKeys.has(row.key) : compoundKeys.has(`${row.sample}:${row.gene}`)}
      trio={trio}
      trioThresholds={trioThresholds}
      visibleInfo={visibleInfo}
      qcSettings={qcSettings}
      onSelect={onSelect}
      onToggleSaved={onToggleSaved}
    />
  ))}</tbody></table></div>{hidden > 0 && <div className="table-render-more" role="status">
    <span>Showing {visibleRows.length.toLocaleString()} of {rows.length.toLocaleString()} matching rows. Filters, sorting and exports apply to all of them.</span>
    <button type="button" className="secondary-button" onClick={() => setRenderState({ rows, limit: renderLimit + TABLE_RENDER_STEP })}>Show {Math.min(TABLE_RENDER_STEP, hidden).toLocaleString()} more</button>
    <button type="button" className="secondary-button" onClick={() => setRenderState({ rows, limit: renderLimit + Math.min(hidden, TABLE_RENDER_STEP * 10) })}>Show {Math.min(hidden, TABLE_RENDER_STEP * 10).toLocaleString()} more</button>
  </div>}</div>;
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
  oneRowPerVariant,
  setOneRowPerVariant,
}: {
  open: boolean;
  setOpen: (value: boolean) => void;
  visibleInfo: Set<DisplayItem>;
  setVisibleInfo: React.Dispatch<React.SetStateAction<Set<DisplayItem>>>;
  availableDbnsfpPredictors: Set<string>;
  visibleDbnsfpPredictors: Set<string>;
  setVisibleDbnsfpPredictors: React.Dispatch<React.SetStateAction<Set<string>>>;
  oneRowPerVariant?: boolean;
  setOneRowPerVariant?: (value: boolean) => void;
}) {
  const toggle = (item: DisplayItem, checked: boolean) => setVisibleInfo((current) => toggleSet(current, item, checked));
  const toggleDbnsfp = (id: string, checked: boolean) => setVisibleDbnsfpPredictors((current) => toggleSet(current, id, checked));
  const predictors: [DisplayItem, string][] = [
    ["alphaGenomeAvi", "AlphaGenome AVI — Phred (WGS)"], ["alphaGenomeAviRaw", "AlphaGenome AVI — raw (WGS)"],
    ["alphaMissense", "AlphaMissense"], ["cadd", "CADD"], ["spliceAI", "SpliceAI"], ["promoterAI", "promoterAI"], ["loGoFunc", "LoGoFunc mechanism"],
    ["revel", "REVEL"], ["metaRnn", "MetaRNN"], ["primateAi", "PrimateAI"], ["sift", "SIFT"], ["polyPhen", "PolyPhen"], ["loftee", "LOFTEE"],
    ["caddRaw", "CADD raw"], ["gerp", "GERP++ RS"], ["phyloP", "phyloP 100-way"], ["phastCons", "phastCons 100-way"],
  ];
  const funcVepModels: [DisplayItem, string][] = [
    ["funcVepCti", "CTI — default"],
    ["funcVepCte", "CTE — optional comparison"],
    ["funcVepSp", "SP — optional comparison"],
  ];
  const sections: [DisplayItem, string][] = [
    ["quality", "Call quality"], ["population", "Population & regions"], ["gnomadPopulations", "All gnomAD population frequencies"], ["clinvar", "ClinVar and curated variant evidence"], ["transcript", "Transcript"], ["geneConstraint", "Gene constraint"],
  ];
  const detectedDbnsfp = ADDITIONAL_DBNSFP_PREDICTORS.filter((item) => availableDbnsfpPredictors.has(item.id));
  return <div className="display-settings"><button className={`secondary-button ${open ? "active" : ""}`} onClick={() => setOpen(!open)}>Display settings</button>{open && <div className="settings-popover"><div className="settings-head"><div><strong>Information shown</strong><span>Saved on this workstation</span></div><button onClick={() => setOpen(false)}>×</button></div>{setOneRowPerVariant && <><h3>Variant list</h3><div className="settings-grid"><Check label="One row per variant" checked={Boolean(oneRowPerVariant)} onChange={setOneRowPerVariant} /></div><p className="settings-note">Each variant appears once, represented by its highest-priority transcript (MANE Select, then MANE Plus Clinical, then most severe consequence). A +N chip counts the collapsed transcript/gene rows; the variant page always lists every transcript.</p></>}<h3>Evidence sections</h3><div className="settings-grid">{sections.map(([key, label]) => <Check key={key} label={label} checked={visibleInfo.has(key)} onChange={(checked) => toggle(key, checked)} />)}</div><h3>Core predictors</h3><div className="settings-grid">{predictors.map(([key, label]) => <Check key={key} label={label} checked={visibleInfo.has(key)} onChange={(checked) => toggle(key, checked)} />)}</div><h3>FuncVEP models</h3><div className="settings-grid">{funcVepModels.map(([key, label]) => <Check key={key} label={label} checked={visibleInfo.has(key)} onChange={(checked) => toggle(key, checked)} />)}</div><p className="settings-note">CTI is shown by default. Enable CTE or SP here when you want to compare their different feature sets.</p><h3>Additional dbNSFP predictors <span className="detected-count">{detectedDbnsfp.length} detected</span></h3>{detectedDbnsfp.length ? <div className="settings-grid">{detectedDbnsfp.map((item) => <Check key={item.id} label={item.label} checked={visibleDbnsfpPredictors.has(item.id)} onChange={(checked) => toggleDbnsfp(item.id, checked)} />)}</div> : <p className="settings-empty">None were present in this VCF’s CSQ schema.</p>}<button className="settings-reset" onClick={() => { setVisibleInfo(new Set(DEFAULT_DISPLAY)); setVisibleDbnsfpPredictors(new Set()); }}>Restore defaults</button></div>}</div>;
}

const FUNCVEP_MODEL_DISPLAY: Array<{
  key: Extract<DisplayItem, "funcVepCti" | "funcVepCte" | "funcVepSp">;
  metricId: "cti" | "cte" | "sp";
  label: "CTI" | "CTE" | "SP";
  value: (row: VariantRow) => number | null | undefined;
}> = [
  {
    key: "funcVepCti",
    metricId: "cti",
    label: "CTI",
    value: (row) => row.funcVepCti,
  },
  {
    key: "funcVepCte",
    metricId: "cte",
    label: "CTE",
    value: (row) => row.funcVepCte,
  },
  {
    key: "funcVepSp",
    metricId: "sp",
    label: "SP",
    value: (row) => row.funcVepSp,
  },
];

function funcVepMissingScoreNote(observation?: PredictorObservation) {
  if (!observation) return "No FuncVEP score for this variant.";
  const sourceGenes = (observation.target?.ensembl_gene || "").split("&").filter(Boolean);
  const matchReason = observation?.provenance?.match;
  const reason = matchReason === "query_target_unavailable"
    ? "The VCF did not provide an Ensembl gene target"
    : matchReason === "multiple_exact_records"
      ? "More than one source record matched"
      : observation?.matchStatus === "ambiguous"
        ? "The source match was ambiguous"
        : "The allele was found, but the Ensembl gene did not match exactly";
  const source = sourceGenes.length ? ` Source gene${sourceGenes.length === 1 ? "" : "s"}: ${sourceGenes.join(", ")}.` : "";
  return `${reason}; no score was transferred.${source}`;
}

type PredictorCardItem = {
  key: string;
  label: string;
  value: number | null | undefined;
  note?: string;
  noteTitle?: string;
  noteWhenMissing?: boolean;
  strong?: boolean;
};

function PredictorCard({ item }: { item: PredictorCardItem }) {
  return <div className={`predictor-card ${item.strong ? "strong" : ""}`}>
    <span>{item.label}</span>
    <strong>{compactNumber(item.value ?? null, item.label.includes("CADD") ? 1 : 3)}</strong>
    <small title={item.noteTitle}>
      {(item.value === null || item.value === undefined) && !item.noteWhenMissing
        ? "No score for this variant"
        : item.note || "Available"}
    </small>
  </div>;
}

function ccreDistanceLabel(distance: number) {
  if (distance === 0) return "0 bp · TSS overlaps cCRE";
  const direction = distance > 0 ? "downstream" : "upstream";
  return `${distance > 0 ? "+" : "−"}${Math.abs(distance).toLocaleString()} bp · ${direction}`;
}

function isProteinCodingBiotype(biotype: string) {
  return biotype.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_") === "protein_coding";
}

// Props are the REF/ALT alleles. They are deliberately NOT named `ref`: a
// prop literally called `ref` is React's ref slot, works only because React
// 19 forwards it as a plain prop, breaks under the React Compiler, and made
// the lint rule treat the whole `selected` row as a ref (review M36).
function CcreContextPanel({
  chrom, pos, refAllele, altAllele, compact = false,
}: {
  chrom: string;
  pos: number;
  refAllele: string;
  altAllele: string;
  compact?: boolean;
}) {
  const ref = refAllele;
  const alt = altAllele;
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
      <div className="ccre-caveat"><strong>Important: proximity is not a target-gene assignment.</strong><span>The VEP or closest gene shown for this variant is not necessarily regulated by the cCRE. Every gene below is listed only because its gene-level TSS lies within ±{(context.gene_window_bp / 1000).toLocaleString()} kb of that cCRE; experimental or cell-specific regulatory evidence is needed to infer a target.</span><span>A genomic region can simultaneously have a transcript-specific coding consequence and be a regulatory element. The regulatory element may act on the same gene, another gene, or multiple genes; the cCRE overlap is not independent evidence of pathogenicity.</span></div>
      {context.gene_resource_available && <label className="ccre-gene-toggle"><input type="checkbox" checked={includeNoncodingGenes} onChange={(event) => setIncludeNoncodingGenes(event.target.checked)} /><span>Include non-protein-coding genes</span><small>Protein-coding genes are shown by default; the complete context remains available.</small></label>}
      <div className="ccre-overlap-list">{context.overlaps.map((overlap) => {
        const proteinCodingGenes = overlap.nearby_genes.filter((gene) => isProteinCodingBiotype(gene.biotype));
        const otherGenes = overlap.nearby_genes.filter((gene) => !isProteinCodingBiotype(gene.biotype));
        const displayedGenes = includeNoncodingGenes ? [...proteinCodingGenes, ...otherGenes] : proteinCodingGenes;
        return <article key={`${overlap.accession}:${overlap.start}:${overlap.end}`}>
          <header><div><strong>{overlap.accession}</strong><span>{overlap.class} · {overlap.class_label}</span></div><code>{overlap.chrom}:{overlap.start}-{overlap.end}</code></header>
          {context.gene_resource_available ? <><div className="ccre-gene-caption"><span>{includeNoncodingGenes ? "All" : "Protein-coding"} gene TSSs within ±{(context.gene_window_bp / 1000).toLocaleString()} kb</span><small>{displayedGenes.length.toLocaleString()} shown · {overlap.nearby_genes.length.toLocaleString()} total ({proteinCodingGenes.length.toLocaleString()} protein-coding, {otherGenes.length.toLocaleString()} other) · {context.gene_source} · signed by transcriptional direction</small></div>{displayedGenes.length ? <div className="ccre-gene-table-wrap"><table className="ccre-gene-table"><thead><tr><th>Gene</th><th>Ensembl ID</th><th>TSS / strand</th><th>Distance from cCRE to TSS</th><th>Biotype</th></tr></thead><tbody>{displayedGenes.map((gene) => <tr key={gene.gene_id}><td title={gene.symbol === gene.gene_id ? "No separate gene symbol in the Ensembl source" : gene.symbol}><strong className={gene.symbol === gene.gene_id ? "symbol-unavailable" : ""}>{gene.symbol === gene.gene_id ? "—" : gene.symbol}</strong></td><td className="mono" title={gene.gene_id}>{gene.gene_id}</td><td className="mono" title={`${overlap.chrom}:${gene.tss.toLocaleString()} · ${gene.strand}`}>{overlap.chrom}:{gene.tss.toLocaleString()} · {gene.strand}</td><td className="mono" title={ccreDistanceLabel(gene.distance_bp)}>{ccreDistanceLabel(gene.distance_bp)}</td><td title={cleanLabel(gene.biotype)}>{cleanLabel(gene.biotype)}</td></tr>)}</tbody></table></div> : <div className="ccre-no-coding-genes"><strong>No protein-coding gene TSSs in this window</strong><span>{otherGenes.length.toLocaleString()} non-protein-coding gene{otherGenes.length === 1 ? " is" : "s are"} available using the option above.</span></div>}</> : <div className="ccre-unavailable"><strong>Nearby-gene context unavailable</strong><span>The cCRE overlap is valid, but the release-matched Ensembl gene TSS table is not installed.</span></div>}
        </article>;
      })}</div>
    </>}
  </section>;
}

function VariantReviewWorkspace({ rows, selected, setSelected, saved, setSaved, compoundKeys, visibleInfo, settingsOpen, setSettingsOpen, setVisibleInfo, availableDbnsfpPredictors, visibleDbnsfpPredictors, setVisibleDbnsfpPredictors, trio, trioThresholds, qcSettings, analysisScope, screenCatalog, setScreenCatalog, activeRegulatorySetId, setActiveRegulatorySetId, regulatoryContextSets, setRegulatoryContextSets, onManagePhenotype }: { rows: VariantRow[]; selected: VariantRow; setSelected: (row: VariantRow | null) => void; saved: Set<string>; setSaved: React.Dispatch<React.SetStateAction<Set<string>>>; compoundKeys: Set<string>; visibleInfo: Set<DisplayItem>; settingsOpen: boolean; setSettingsOpen: (value: boolean) => void; setVisibleInfo: React.Dispatch<React.SetStateAction<Set<DisplayItem>>>; availableDbnsfpPredictors: Set<string>; visibleDbnsfpPredictors: Set<string>; setVisibleDbnsfpPredictors: React.Dispatch<React.SetStateAction<Set<string>>>; trio: TrioDefinition | null; trioThresholds: TrioThresholds; qcSettings: VariantQcSettings; analysisScope: AnalysisScope; screenCatalog: ScreenContextCatalog | null; setScreenCatalog: (catalog: ScreenContextCatalog) => void; activeRegulatorySetId: string; setActiveRegulatorySetId: (id: string) => void; regulatoryContextSets: RegulatoryContextSet[]; setRegulatoryContextSets: (sets: RegulatoryContextSet[]) => void; onManagePhenotype: () => void }) {
  const detailRef = useRef<HTMLElement>(null);
  const [reviewSection, setReviewSection] = useState<"overview" | "gene" | "phenotype" | "regulatory">("overview");
  const [screenEvidence, setScreenEvidence] = useState<ScreenContextEvidence | null>(null);
  const [screenEvidenceLoading, setScreenEvidenceLoading] = useState(true);
  const [screenEvidenceError, setScreenEvidenceError] = useState("");
  const [clingenEvidence, setClingenEvidence] = useState<ClinGenErepoVariant | null>(null);
  const [clingenEvidenceLoading, setClingenEvidenceLoading] = useState(true);
  const [clingenEvidenceError, setClingenEvidenceError] = useState("");
  const [geniaEvidence, setGeniaEvidence] = useState<GeniaVariant | null>(null);
  const [geniaEvidenceLoading, setGeniaEvidenceLoading] = useState(true);
  const [geniaEvidenceError, setGeniaEvidenceError] = useState("");
  const [geniaEvidenceKey, setGeniaEvidenceKey] = useState("");
  const selectedGeniaEvidenceKey = `${selected.chrom}:${selected.pos}:${selected.ref}:${selected.alt}`;
  const isSaved = saved.has(selected.key);
  const isCompound = compoundKeys.has(`${selected.sample}:${selected.gene}`);
  const deNovo = trio ? assessDeNovo(selected, trio, trioThresholds) : null;
  const selectedQcFailures = variantQcFailures(selected, qcSettings);
  const selectedFrequencySource = selected.gnomadPopmaxSource;
  const selectedLoGoFuncScore = selected.loGoFuncPrediction === "GOF"
    ? selected.loGoFuncGof
    : selected.loGoFuncPrediction === "LOF"
      ? selected.loGoFuncLof
      : selected.loGoFuncPrediction === "Neutral" ? selected.loGoFuncNeutral : null;
  const funcVepObservation = selected.predictions?.funcvep;
  const funcVepExactMatch = funcVepObservation?.matchStatus === "exact";
  const selectedManeAlphaMissenseMissing = selected.mane
    && (selected.alphaMissense === null || selected.alphaMissense === undefined);
  const predictorOptions: PredictorCardItem[] = [
    {
      key: "alphaMissense",
      label: "AlphaMissense",
      value: selected.alphaMissense,
      note: selectedManeAlphaMissenseMissing ? "No score for the selected MANE transcript" : selected.alphaPrediction,
      noteWhenMissing: selectedManeAlphaMissenseMissing,
      strong: (selected.alphaMissense ?? 0) >= 0.564,
    },
    { key: "cadd", label: "CADD phred", value: selected.cadd, strong: (selected.cadd ?? 0) >= 20 },
    { key: "spliceAI", label: "SpliceAI max", value: selected.spliceAI, strong: (selected.spliceAI ?? 0) >= 0.2 },
    { key: "promoterAI", label: "PromoterAI", value: selected.promoterAI, note: "signed promoter-effect score", strong: selected.promoterAI !== null && selected.promoterAI !== undefined && Math.abs(selected.promoterAI) >= 0.8 },
    { key: "alphaGenomeAvi", label: "AlphaGenome AVI", value: selected.alphaGenomeAviPhred, note: "Phred score", noteTitle: "Genome-wide variant-impact rank, not a clinical pathogenicity classification. AVI includes AlphaMissense and conservation inputs; these are not independent evidence." },
    { key: "alphaGenomeAviRaw", label: "AlphaGenome AVI raw", value: selected.alphaGenomeAviRaw, note: "Raw logit" },
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
  const predictorCards = predictorOptions.filter((item) => visibleInfo.has(item.key as DisplayItem)
    && (!["alphaGenomeAvi", "alphaGenomeAviRaw"].includes(item.key) || analysisScope === "whole_genome"));
  const funcVepPredictorCards: PredictorCardItem[] = FUNCVEP_MODEL_DISPLAY
    .filter((model) => visibleInfo.has(model.key))
    .map((model) => {
      const score = model.value(selected);
      const classification = funcVepExactMatch
        ? predictorBinaryClassification("funcvep", model.metricId, score)
        : null;
      return {
        key: model.key,
        label: `FuncVEP ${model.label}`,
        value: funcVepExactMatch ? score : null,
        note: funcVepExactMatch
          ? score === null || score === undefined
            ? `${model.label} score unavailable.`
            : classification?.label
          : funcVepMissingScoreNote(funcVepObservation),
        noteTitle: classification
          ? `${classification.label} at the published ${model.label} binary cutoff (${classification.comparison === "greater_than_or_equal" ? "≥" : classification.comparison === "less_than_or_equal" ? "≤" : "absolute value ≥"} ${classification.threshold}). ${classification.thresholdSet}. Functional-effect prediction, not a clinical pathogenicity classification.`
          : undefined,
        noteWhenMissing: true,
        strong: classification?.isPositive ?? false,
      };
    });
  const additionalPredictorCards: PredictorCardItem[] = ADDITIONAL_DBNSFP_PREDICTORS
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
  const allPredictorCards = [...predictorCards, ...funcVepPredictorCards, ...additionalPredictorCards];
  const gnomadPopulationItems: [string, React.ReactNode][] = Object.entries(selected.gnomadFrequencies ?? {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => [gnomadFrequencyLabel(key), compactNumber(value, 6)]);
  useEffect(() => {
    detailRef.current?.scrollTo({ top: 0 });
  }, [selected.key]);

  useEffect(() => {
    let active = true;
    setClingenEvidenceLoading(true);
    setClingenEvidenceError("");
    setClingenEvidence(null);
    getClinGenErepoVariant(selected.chrom, selected.pos, selected.ref, selected.alt)
      .then((value) => { if (active) setClingenEvidence(value); })
      .catch((reason: unknown) => { if (active) setClingenEvidenceError(reason instanceof Error ? reason.message : "ClinGen lookup failed"); })
      .finally(() => { if (active) setClingenEvidenceLoading(false); });
    return () => { active = false; };
  }, [selected.chrom, selected.pos, selected.ref, selected.alt]);

  useEffect(() => {
    let active = true;
    setGeniaEvidenceKey(selectedGeniaEvidenceKey);
    setGeniaEvidenceLoading(true);
    setGeniaEvidenceError("");
    setGeniaEvidence(null);
    getGeniaVariant(selected.chrom, selected.pos, selected.ref, selected.alt)
      .then((value) => { if (active) setGeniaEvidence(value); })
      .catch((reason: unknown) => { if (active) setGeniaEvidenceError(reason instanceof Error ? reason.message : "GenIA lookup failed"); })
      .finally(() => { if (active) setGeniaEvidenceLoading(false); });
    return () => { active = false; };
  }, [selected.chrom, selected.pos, selected.ref, selected.alt, selectedGeniaEvidenceKey]);

  useEffect(() => {
    if (analysisScope !== "whole_genome") {
      const frame = window.requestAnimationFrame(() => {
        setScreenEvidence(null);
        setScreenEvidenceError("");
        setScreenEvidenceLoading(false);
      });
      return () => window.cancelAnimationFrame(frame);
    }
    let active = true;
    Promise.resolve().then(() => {
      if (!active) return null;
      setScreenEvidenceLoading(true);
      setScreenEvidenceError("");
      setScreenEvidence(null);
      return getScreenContext({ chrom: selected.chrom, pos: selected.pos, ref: selected.ref, alt: selected.alt });
    })
      .then((value) => { if (active && value) setScreenEvidence(value); })
      .catch((reason: unknown) => { if (active) setScreenEvidenceError(reason instanceof Error ? reason.message : "SCREEN context lookup failed"); })
      .finally(() => { if (active) setScreenEvidenceLoading(false); });
    return () => { active = false; };
  }, [analysisScope, screenCatalog?.available, selected.chrom, selected.pos, selected.ref, selected.alt]);

  // The side list used to render every matching row (audit H9); show a
  // bounded window around the selected variant instead.
  const reviewListWindow = useMemo(() => {
    const limit = TABLE_RENDER_STEP;
    if (rows.length <= limit) return { rows, hidden: 0 };
    const selectedIndex = Math.max(0, rows.findIndex((row) => row.key === selected.key));
    const start = Math.max(0, Math.min(selectedIndex - Math.floor(limit / 2), rows.length - limit));
    return { rows: rows.slice(start, start + limit), hidden: rows.length - limit };
  }, [rows, selected.key]);

  return <div className="review-workspace">
    <aside className="review-list"><div className="review-list-head"><button onClick={() => setSelected(null)}>← Back to results</button><span>{rows.length} variants</span></div><div className="review-list-scroll">{reviewListWindow.rows.map((row) => <button key={row.key} className={`review-list-item ${row.key === selected.key ? "active" : ""}`} onClick={() => setSelected(row)}><div><strong>{row.gene}</strong><span className={`impact-pill ${row.impact.toLowerCase()}`}>{row.impact}</span></div><VariantIdentifier row={row}/><small>{row.hgvsP || row.hgvsC || row.consequence.replaceAll("_", " ")}</small><small>{row.sample} · {row.genotype}</small></button>)}{reviewListWindow.hidden > 0 && <p className="review-list-note">Showing {reviewListWindow.rows.length.toLocaleString()} of {rows.length.toLocaleString()} variants around the selected one; use the results table to browse the rest.</p>}</div></aside>
    <article className="review-detail" ref={detailRef}>
      <div className="review-toolbar"><button className="back-button" onClick={() => setSelected(null)}>← Results</button><div className="review-view-tabs" role="tablist" aria-label="Variant review workspace"><button role="tab" aria-selected={reviewSection === "overview"} className={reviewSection === "overview" ? "active" : ""} onClick={() => setReviewSection("overview")}>Variant</button><button role="tab" aria-selected={reviewSection === "gene"} className={reviewSection === "gene" ? "active" : ""} onClick={() => setReviewSection("gene")}>Gene</button>{!selected.carriers && <button role="tab" aria-selected={reviewSection === "phenotype"} className={reviewSection === "phenotype" ? "active" : ""} onClick={() => setReviewSection("phenotype")}>Phenotype</button>}{analysisScope === "whole_genome" && <button role="tab" aria-selected={reviewSection === "regulatory"} className={reviewSection === "regulatory" ? "active" : ""} onClick={() => setReviewSection("regulatory")}>Regulatory evidence</button>}</div><div><DisplaySettingsButton open={settingsOpen} setOpen={setSettingsOpen} visibleInfo={visibleInfo} setVisibleInfo={setVisibleInfo} availableDbnsfpPredictors={availableDbnsfpPredictors} visibleDbnsfpPredictors={visibleDbnsfpPredictors} setVisibleDbnsfpPredictors={setVisibleDbnsfpPredictors} /><button title="Candidate stars are session-only; export the Saved view to keep them." className={`secondary-button save-candidate ${isSaved ? "active" : ""}`} onClick={() => setSaved((current) => toggleSet(current, selected.key, !isSaved))}><Icon name="star" />{isSaved ? "Saved" : "Save candidate"}</button></div></div>
      <header className="review-hero"><div><div className="review-kickers"><span className={`impact-pill ${selected.impact.toLowerCase()}`}>{selected.impact}</span>{isStartLostVariant(selected) && <span className="review-badge amber">Start-loss</span>}{selectedQcFailures.length > 0 && <span className="review-badge qc-fail-flag">QC fail</span>}{selected.duplicateRecord && <span className="review-badge amber">{selected.duplicateRecordKind === "liftover_collision" ? "Lift-over collision" : "Duplicate record"}</span>}{deNovo && ["high_confidence", "possible", "possible_parental_mosaicism"].includes(deNovo.status) && <span className={`review-badge family-status ${deNovo.status}`}>{deNovoLabel(deNovo.status)}</span>}{isReferenceDisruptedTranscript(selected) && <span className="review-badge amber" title="This transcript's ORF is disrupted on the reference-genome haplotype and may be translated only on other haplotypes.">Reference-disrupted transcript</span>}{selected.liftedFromGrch37 && <span className="review-badge amber">Lifted from GRCh37</span>}{selected.assemblyAlleleSwap && <span className="review-badge amber" title="A source allele became the GRCh38 reference; genotype and allele-indexed annotations were remapped">Assembly allele swap</span>}{selected.mane && <span className="review-badge" title={selected.maneSelect ? "MANE Select transcript" : "MANE Plus Clinical — an additional clinically designated transcript alongside MANE Select"}>{selected.maneSelect ? "MANE Select" : "MANE Plus Clinical"}</span>}{selected.picked && !selected.mane && <span className="review-badge">PICK fallback</span>}{isCompound && <span className="review-badge amber">candidate comp het</span>}</div><button className="review-gene-link" onClick={() => setReviewSection("gene")} title="Open gene-level evidence">{selected.gene}</button><p>{selected.hgvsP || selected.hgvsC || fullVariantId(selected)}</p><div className="review-hero-locus"><VariantIdentifier row={selected}/><span>· {selected.sample} · {selected.genotype}</span></div>{selected.liftedFromGrch37 && selected.originalChrom && <span className="cell-sub mono">Original GRCh37: {selected.originalChrom}:{selected.originalPos} {selected.originalRef}›{selected.originalAlt}</span>}</div><div className="hero-score"><span title={frequencySourceLabel(selectedFrequencySource)}>{frequencySourceLabel(selectedFrequencySource, true)}</span><strong>{compactNumber(selected.gnomadPopmax)}</strong></div></header>

      {reviewSection === "gene" ? <GeneKnowledgePanel gene={selected.gene} constraintRow={selected} relatedRows={rows.filter((row) => row.gene.toUpperCase() === selected.gene.toUpperCase())}/> : reviewSection === "phenotype" ? <PhenotypeReviewPanel sample={selected.sample} onManage={onManagePhenotype}/> : analysisScope === "whole_genome" && reviewSection === "regulatory" ? <RegulatoryEvidencePanel catalog={screenCatalog} evidence={screenEvidence} loading={screenEvidenceLoading} error={screenEvidenceError} activeSetId={activeRegulatorySetId} setActiveSetId={setActiveRegulatorySetId} customSets={regulatoryContextSets} setCustomSets={setRegulatoryContextSets} onCatalogInstalled={setScreenCatalog}><CcreContextPanel compact chrom={selected.chrom} pos={selected.pos} refAllele={selected.ref} altAllele={selected.alt}/></RegulatoryEvidencePanel> : <>
      <section className="review-section"><div className="section-title"><div><p className="eyebrow">Computational evidence</p><h2>Predictors</h2></div><span>{allPredictorCards.length} shown</span></div><div className="predictor-card-grid">{allPredictorCards.map((item) => <PredictorCard item={item} key={item.key}/>)}</div>{visibleInfo.has("loGoFunc") && selected.loGoFuncAlleleAvailable && <div className="logofunc-evidence"><div><span>LoGoFunc missense mechanism</span><strong>{selected.loGoFuncPrediction || "Allele available; transcript/protein mismatch"}{selectedLoGoFuncScore !== null ? ` · ${compactNumber(selectedLoGoFuncScore, 3)}` : ""}</strong><small>Research prediction; not a clinical classification or LOFTEE result.</small></div><dl><div><dt>Neutral</dt><dd>{compactNumber(selected.loGoFuncNeutral, 3)}</dd></div><div><dt>GOF</dt><dd>{compactNumber(selected.loGoFuncGof, 3)}</dd></div><div><dt>LOF</dt><dd>{compactNumber(selected.loGoFuncLof, 3)}</dd></div></dl><p>Source {selected.loGoFuncSourceTranscript || "—"} · {selected.loGoFuncSourceHgvsp || "—"} · {cleanLabel(selected.loGoFuncMatch)}</p></div>}{visibleInfo.has("loftee") && <div className="loftee-line loftee-detail-line"><span>LOFTEE</span><div className="loftee-line-content"><strong>{selected.haplotypeFrameStatus === "FRAME_RESTORED_CONFIRMED" ? `Not LoF after confirmed haplotype reconstruction · per-variant ${lofteeDisplayLabel(selected)}` : lofteeDisplayLabel(selected)}</strong>{isReferenceDisruptedTranscript(selected) && !selected.loftee && <small>LOFTEE evaluates standard protein-coding transcripts. This transcript&apos;s reference-genome haplotype already has a disrupted open reading frame, although other human haplotypes may be translated.</small>}{isStartLostVariant(selected) && !selected.loftee && <small>Start-loss variants require separate assessment of downstream in-frame initiation sites and transcript context. No PVS1 conclusion is assigned here.</small>}{lofteeCodes(selected.lofteeFilter).map((code) => <small key={`filter:${code}`}><b>LC reason:</b> {lofteeExplanation(code, "filter")} <code>{code}</code></small>)}{lofteeCodes(selected.lofteeFlags).map((code) => <small key={`flag:${code}`}><b>Flag:</b> {lofteeExplanation(code, "flag")} <code>{code}</code></small>)}</div></div>}{visibleInfo.has("loftee") && selected.ptcCalcStatus && <div className="loftee-line loftee-detail-line"><span>Frameshift PTC calculation</span><div className="loftee-line-content"><strong>{isReferenceDisruptedTranscript(selected) ? "Not calculated · reference transcript CDS is already disrupted" : isSingleExonTranscript(selected.exon) || selected.ptcCalcStatus === "not_applicable_single_exon_transcript" ? "Not applicable · single-exon transcript" : ptcCalculationStatusLabel(selected.ptcCalcStatus)}</strong>{isReferenceDisruptedTranscript(selected) ? <><small>A reliable patient-specific PTC/NMD position cannot be calculated against an already-disrupted reference ORF.</small><small>Technical status: <code>{selected.ptcCalcStatus}</code></small></> : isSingleExonTranscript(selected.exon) || selected.ptcCalcStatus === "not_applicable_single_exon_transcript" ? <><small>This transcript has no downstream exon–exon junction, so the conventional 50–55-nt exon-junction NMD rule is not applicable.</small><small>Premature stops in single-exon transcripts may escape exon-junction-complex-dependent NMD; assess transcript-specific RNA and protein evidence separately.</small>{(selected.loftee50bp || selected.loftee50bpOriginal) && <small>Stored technical value: <code>{selected.loftee50bp || selected.loftee50bpOriginal}</code> · not interpreted for this transcript</small>}</> : <>{selected.ptcDistanceFromLastExon !== null && <small>{ptcDistanceLabel(selected.ptcDistanceFromLastExon)}</small>}{selected.loftee50bp && <small><b>{ptcRuleLabel(selected.loftee50bp)}</b></small>}{selected.loftee50bpChanged && <small>Replaced original LOFTEE coordinate-based result: {selected.loftee50bpOriginal || "not recorded"}</small>}</>}</div></div>}{selected.haplotypeFrameStatus && <div className="loftee-line"><span>Sample haplotype</span><strong>{haplotypeFrameLabel(selected.haplotypeFrameStatus)}{selected.haplotypeFramePartners?.length ? ` · partner ${selected.haplotypeFramePartners.join(", ")}` : ""}{selected.haplotypeProteinChange ? ` · ${selected.haplotypeProteinChange}` : ""}</strong></div>}</section>

      {isHighImpactSpliceVariant(selected) && <div className="splicing-evidence-row"><span>Splicing evidence</span><div><small>Site</small><strong>{spliceSiteLabel(selected)}</strong></div><div title={cleanLabel(selected.consequence)}><small>VEP</small><strong>{selected.impact} · {primarySpliceConsequenceLabel(selected.consequence)}</strong></div><div><small>LOFTEE</small><strong>{selected.loftee || "Not annotated"}</strong></div><div><small>SpliceAI</small><strong>{spliceAiDisplay(selected.spliceAI)}</strong></div></div>}

      {selected.spliceAI == null && selected.ref.length !== selected.alt.length && /^[ACGT]+$/.test(selected.ref) && /^[ACGT]+$/.test(selected.alt) && <SpliceAiOnlineLookup key={fullVariantId(selected)} variant={{ chrom: selected.chrom, pos: selected.pos, ref: selected.ref, alt: selected.alt }} />}

      <div className="evidence-layout">
        {selected.carriers && <EvidenceSection eyebrow="Cohort evidence" title={selected.cohortSampleCount ? `Carriers · ${selected.carriers.length} of ${selected.cohortSampleCount} individuals` : `Carriers · ${selected.carriers.length} individual${selected.carriers.length === 1 ? "" : "s"} (separately called)`}>
          <div className="cohort-carriers-table"><table><thead><tr><th>Individual</th><th>GT</th><th>DP</th><th>GQ</th><th>AD</th><th>AB</th><th>FT</th><th>QC</th></tr></thead><tbody>
            {selected.carriers.map((carrier) => { const carrierFailures = carrierQcFailures(carrier, selected, qcSettings); return <tr key={carrier.sample} className={carrierFailures.length ? "carrier-qc-fail" : ""}><td>{carrier.sample}</td><td className="mono">{carrier.evidence.gt}{carrier.evidence.partialCall ? <span className="qc-warn-flag" title="Partially called genotype"> partial</span> : null}</td><td>{carrier.evidence.dp ?? "—"}</td><td>{carrier.evidence.gq ?? "—"}</td><td>{carrier.evidence.adRef ?? "—"}, {carrier.evidence.adAlt ?? "—"}</td><td>{compactNumber(carrier.evidence.alleleBalance, 2)}</td><td>{carrier.evidence.genotypeFilter || "—"}</td><td>{carrierFailures.length ? <span className="qc-fail-flag" title={carrierFailures.join("; ")}>Fail</span> : "Pass"}</td></tr>; })}
          </tbody></table></div>
          <p className="constraint-note">{selected.cohortSampleCount
            ? "Jointly called file: non-carrying individuals are confirmed reference at this site. Sample-evidence fields elsewhere on this page describe the best-supported carrier."
            : "Separately called files: individuals without a record at this site are not confirmed reference — no carrier denominator is claimed. Sample-evidence fields elsewhere on this page describe the best-supported carrier."}</p>
        </EvidenceSection>}
        {trio && <TrioGenotypeEvidence row={selected} trio={trio} assessment={deNovo}/>}
        {visibleInfo.has("quality") && <EvidenceSection eyebrow="Sample evidence" title="Call quality"><EvidenceGrid items={[
          ["QC status", selectedQcFailures.length
            ? `Fail: ${selectedQcFailures.join("; ")}`
            : selected.carriers?.length
              ? (() => {
                  // The measurements below belong to ONE representative
                  // carrier; a bare "Pass" over a failing representative's
                  // numbers misread as that carrier passing. Judge the REAL
                  // representative from the carriers array — cohort rows
                  // carry a synthetic "N carry" genotype string that would
                  // evaluate nonsensically.
                  const passing = selected.carriers.filter((carrier) => carrierQcFailures(carrier, selected, qcSettings).length === 0).length;
                  const representative = selected.carriers.reduce((best, entry) =>
                    (entry.evidence.gq ?? -1) > (best.evidence.gq ?? -1) ? entry : best);
                  const representativeFails = carrierQcFailures(representative, selected, qcSettings).length > 0;
                  return `Pass — ${passing} of ${selected.carriers.length} carriers pass${representativeFails ? `; the representative shown below (${representative.sample}) fails` : ""}`;
                })()
              : "Pass"], ["Record warning", duplicateRecordLabel(selected) || "None"], ["QUAL", compactNumber(selected.qual ?? null, 1)], ["Site depth", selected.siteDepth ?? "—"], ["Depth (DP)", selected.dp ?? "—"], ["Genotype quality", selected.gq ?? "—"], ["Allele depths", selected.adRef !== undefined || selected.adAlt !== undefined ? `${selected.adRef ?? "—"}, ${selected.adAlt ?? "—"}` : "—"], ["Allele balance", compactNumber(selected.alleleBalance, 3)], ["Genotype FT", selected.genotypeFilter || "—"], ["Genotype", selected.genotype], ["Phase", selected.phaseSet ? `${selected.phase} · PS ${selected.phaseSet}` : selected.phase],
        ]} /></EvidenceSection>}
        {visibleInfo.has("clinvar") && <EvidenceSection eyebrow="Clinical evidence" title="ClinVar"><EvidenceGrid items={[
          ["Significance", cleanLabel(selected.clinvar)], ["Conflicting submissions", cleanLabel(selected.clinvarConflictingEvidence)], ["Conflict includes P / LP", isClinvarConflictWithPathogenic(selected.clinvar, selected.clinvarConflictingEvidence) ? "Yes" : "No"], ["Review status", cleanLabel(selected.clinvarReviewStatus)], ["Condition", cleanLabel(selected.clinvarDisease)],
        ]} /><ProteinMatchEvidenceBlock source="ClinVar" evidence={selected.clinvarProteinMatch ?? legacyClinVarProteinMatch(selected)} selectedTranscript={selected.transcript}/></EvidenceSection>}
        {visibleInfo.has("clinvar") && <ClinGenVariantEvidence evidence={clingenEvidence} loading={clingenEvidenceLoading} error={clingenEvidenceError} compact={selected.clingenErepo ?? []} proteinMatch={selected.clingenProteinMatch} selectedTranscript={selected.transcript}/>}
        {visibleInfo.has("clinvar") && <GeniaVariantEvidence evidence={geniaEvidenceKey === selectedGeniaEvidenceKey ? geniaEvidence : null} loading={geniaEvidenceKey === selectedGeniaEvidenceKey ? geniaEvidenceLoading : true} error={geniaEvidenceKey === selectedGeniaEvidenceKey ? geniaEvidenceError : ""} compact={selected.genia ?? []} proteinMatch={selected.geniaProteinMatch} selectedTranscript={selected.transcript}/>}
        {visibleInfo.has("transcript") && <EvidenceSection eyebrow="Molecular consequence" title="Transcript"><EvidenceGrid items={[
          ["HGVSc", selected.hgvsC || "—"], ["HGVSp", selected.hgvsP || "—"], ["Transcript", selected.transcript || "—"], ["Gene ID", selected.geneId || "—"], ["Biotype", cleanLabel(selected.biotype)], ["Transcript warning", isReferenceDisruptedTranscript(selected) ? "Reference ORF disrupted; not a conventional pLoF baseline" : "None"], ["Exon", selected.exon || "—"], ["Consequence", cleanLabel(selected.consequence)], ["MANE", selected.mane ? "Yes" : "No"], ["VEP PICK", selected.picked ? "Yes" : "No"],
        ]} />{selected.transcriptDetailStatus === "loading" ? <p className="constraint-note">Loading the complete transcript table from the managed VCF…</p> : null}{selected.transcriptDetailStatus === "unavailable" ? <p className="constraint-note">{selected.transcriptDetailError}</p> : null}{selected.collapsedTranscriptRows?.length ? <div className="alt-transcripts"><h4>{selected.transcriptDetailStatus === "loading" ? "Clinical transcript annotations shown while the complete table loads" : "All transcript and gene annotations of this variant"}</h4><div className="transcript-table-scroll" role="region" aria-label="All transcript and gene annotations" tabIndex={0}><table><thead><tr><th scope="col">Gene</th><th scope="col">Transcript</th><th scope="col">Consequence</th><th scope="col">HGVSc</th><th scope="col">HGVSp</th><th scope="col">Designation</th></tr></thead><tbody>
          <tr className="current"><td>{selected.gene}</td><td className="mono">{selected.transcript || "—"}</td><td>{cleanLabel(selected.consequence)}</td><td className="mono">{selected.hgvsC || "—"}</td><td className="mono">{selected.hgvsP || "—"}</td><td>{selected.maneSelect ? "MANE Select" : selected.mane ? "MANE Plus Clinical" : selected.picked ? "VEP PICK" : "—"} (shown)</td></tr>
          {selected.collapsedTranscriptRows.map((other) => <tr key={other.key}><td>{other.gene}</td><td className="mono">{other.transcript || "—"}</td><td>{cleanLabel(other.consequence)}</td><td className="mono">{other.hgvsC || "—"}</td><td className="mono">{other.hgvsP || "—"}</td><td>{other.maneSelect ? "MANE Select" : other.mane ? "MANE Plus Clinical" : other.picked ? "VEP PICK" : "—"}</td></tr>)}
        </tbody></table></div></div> : null}</EvidenceSection>}
        {visibleInfo.has("population") && <EvidenceSection eyebrow="Population & regions" title="Frequency context"><EvidenceGrid items={[
          [frequencySourceLabel(selectedFrequencySource), compactNumber(selected.gnomadPopmax)], ["Popmax population", cleanLabel(selected.gnomadPopmaxPopulation)], ["RepeatMasker", selected.repeat ? "Overlap" : "No overlap"], ["Segmental duplication", selected.segdup ? "Overlap" : "No overlap"], ["Unscored indel flag", selected.unscoredIndelReasons?.map(unscoredIndelReasonLabel).join("; ") || "None"], ["Variant ID", fullVariantId(selected)], ["Source VCF", selected.source],
          ["Input assembly", selected.liftedFromGrch37 ? "GRCh37 (lifted to GRCh38)" : "GRCh38"], ["Original locus", selected.liftedFromGrch37 && selected.originalChrom ? `${selected.originalChrom}:${selected.originalPos} ${selected.originalRef}›${selected.originalAlt}` : "—"],
        ]} /></EvidenceSection>}
        {visibleInfo.has("gnomadPopulations") && <EvidenceSection eyebrow="Optional population evidence" title="gnomAD frequencies"><EvidenceGrid items={gnomadPopulationItems.length ? gnomadPopulationItems : [["Available fields", selected.key.startsWith("cohort:") ? "Population-specific fields are unavailable in the compact cohort fallback" : "No populated population-specific gnomAD fields were available for this variant"]]} /></EvidenceSection>}
        {visibleInfo.has("geneConstraint") && <EvidenceSection eyebrow="Gene-level evidence" title={selected.gene}><EvidenceGrid items={[
          ["pLI", compactNumber(selected.pLi ?? null)], ["LOEUF", compactNumber(selected.loeuf ?? null)], ["Missense Z", compactNumber(selected.missenseZ ?? null)], ["LoF observed / expected", selected.constraintLofObserved !== null && selected.constraintLofObserved !== undefined ? `${compactNumber(selected.constraintLofObserved)} / ${compactNumber(selected.constraintLofExpected ?? null)}` : "—"],
          ["LoF o/e", compactNumber(selected.constraintLofOe ?? null)], ["Constraint transcript", selected.constraintTranscript || "—"], ["Stable gene ID", selected.constraintGeneId || selected.geneId || "—"], ["gnomAD release", selected.constraintRelease || "—"],
          ["Constraint flags", selected.constraintFlags?.join(", ") || "None"], ["Gene quality flags", selected.geneFlags?.join(", ") || "None"], ["Exome bases at AN90", compactPercent(selected.constraintExomeAn90)], ["Exome SegDup / LCR", `${compactPercent(selected.constraintExomeSegdup)} / ${compactPercent(selected.constraintExomeLcr)}`],
        ]} /><p className="constraint-note">{selected.constraintRelease ? `Loaded automatically from bundled gnomAD v${selected.constraintRelease} gene constraint data using its selected MANE/canonical transcript. gnomAD recommends LOEUF over pLI for current interpretation.` : "No matching gene was found in the bundled gnomAD constraint table. Constraint metrics remain unavailable for this gene."}</p></EvidenceSection>}
      </div>
      <GeneKnowledgeSummary gene={selected.gene} onOpen={() => setReviewSection("gene")}/>
      {analysisScope === "whole_genome" && <RegulatorySummary evidence={screenEvidence} loading={screenEvidenceLoading} error={screenEvidenceError} codingConsequence={hasCodingTranscriptConsequence(selected)} onOpen={() => setReviewSection("regulatory")}/>}
      <div className="interpretation-banner"><strong>Review aid, not a classification</strong><span>This workspace organizes evidence; it does not assign ACMG/AMP criteria or replace clinical interpretation.</span></div>
      </>}
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
    excluded_hemizygous: "Excluded · hemizygous region",
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
  const candidatePairs = pairs.filter((pair) => pair.phase !== "cis" && pair.phase !== "excluded_hemizygous");
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
        <div className="family-results-head"><div><p className="eyebrow">Both variants pass active filters</p><h2>Compound-heterozygous pairs</h2></div><Check label="Show cis and excluded pairs" checked={includeCis} onChange={setIncludeCis}/></div>
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

function PhenotypeReviewPanel({ sample, onManage }: { sample: string; onManage: () => void }) {
  const [records, setRecords] = useState<PhenotypeIndividual[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    Promise.all([getSampleLibrary(sample).catch(() => []), getPhenotypesBySample(sample)])
      .then(async ([datasets, legacy]) => {
        const exact = datasets.filter((dataset) => dataset.vcf_sample_name === sample);
        if (exact.length === 1) {
          const phenotype = await getSampleLibraryPhenotype(exact[0].id);
          if (active) setRecords(phenotype ? [phenotype] : []);
        } else if (active) {
          setRecords(legacy);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setRecords([]);
          setError(reason instanceof Error ? reason.message : "Phenotype lookup failed.");
        }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sample]);
  return <div className="phenotype-review-panel">
    <div className="phenotype-review-head"><div><p className="eyebrow">Individual context</p><h1>Phenotype</h1><p>Phenotype is linked to the individual through VCF sample <span className="mono">{sample}</span>.</p></div><button className="secondary-button" onClick={onManage}>Manage phenotype data</button></div>
    {loading && <div className="phenotype-review-state">Loading linked phenotype information…</div>}
    {!loading && error && <div className="alert error">{error}</div>}
    {!loading && !error && records.length === 0 && <div className="phenotype-review-empty"><strong>No phenotype record is linked to this sample</strong><span>The genotype remains reviewable. Add an individual record now or link this sample later.</span><button className="primary-button dark" onClick={onManage}>Add or link phenotype</button></div>}
    {!loading && records.length > 1 && <div className="alert">This VCF sample is linked to {records.length} individual records. Review the sample-to-individual mapping.</div>}
    {!loading && records.map((record) => <article className="phenotype-review-record" key={record.individual_id}>
      <header><div><p className="eyebrow">Individual</p><h2>{record.individual_id}</h2><span>{record.current_diagnosis || "No current diagnosis recorded"}</span></div><small>Updated {record.updated_at || "—"}</small></header>
      <EvidenceGrid items={[
        ["VCF sample", sample],
        ["Linked samples", record.sample_ids.join(", ") || "—"],
        ["Sex at birth", record.sex_at_birth || "—"],
        ["Age at evaluation", record.age_at_evaluation === null ? "—" : `${record.age_at_evaluation} ${record.age_at_evaluation_unit}`],
        ["Age at onset", record.age_at_onset === null ? "—" : `${record.age_at_onset} ${record.age_at_onset_unit}`],
        ["Reported race", record.reported_race.join(", ") || "—"],
        ["Reported ethnicity", record.reported_ethnicity.join(", ") || "—"],
        ["Source date", record.source_date || "—"],
      ]}/>
      <section><h3>Phenotype summary</h3><p>{record.phenotype_summary || "Not entered."}</p></section>
      <div className="phenotype-feature-grid"><section><h3>Present features</h3>{record.present_features.length ? <ul>{record.present_features.map((feature) => <li key={feature}>{feature}</li>)}</ul> : <p>Not entered.</p>}</section><section><h3>Explicitly absent features</h3>{record.absent_features.length ? <ul>{record.absent_features.map((feature) => <li key={feature}>{feature}</li>)}</ul> : <p>None explicitly recorded.</p>}</section></div>
      {record.notes && <section><h3>Notes</h3><p>{record.notes}</p></section>}
    </article>)}
  </div>;
}

function EvidenceSection({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return <section className="evidence-section"><p className="eyebrow">{eyebrow}</p><h2>{title}</h2>{children}</section>;
}

function legacyClinVarProteinMatch(row: VariantRow): ProteinMatchEvidence {
  const residueMatch = row.clinvarAaMatch === true;
  const changeMatch = row.clinvarAaChangeMatch === true;
  return {
    // Older in-memory rows did not retain INFO-header presence. A positive
    // flag remains usable; an all-false legacy pair cannot safely mean that
    // ClinVar was evaluated, so present it as not evaluated.
    evaluated: residueMatch || changeMatch,
    residueEvaluated: residueMatch,
    changeEvaluated: changeMatch,
    residueMatch,
    changeMatch,
    details: [],
  };
}

function stableTranscriptId(value: string) {
  return value.trim().split(".", 1)[0];
}

function sourceProteinChange(detail: ProteinMatchDetail) {
  if (!detail.proteinPosition) return "—";
  return `p.${detail.referenceAminoAcid || "?"}${detail.proteinPosition}${detail.alternateAminoAcid || "?"}`;
}

function ProteinMatchEvidenceBlock({ source, evidence, selectedTranscript }: {
  source: "ClinVar" | "ClinGen" | "GenIA";
  evidence: ProteinMatchEvidence | undefined;
  selectedTranscript: string | undefined;
}) {
  const details = evidence?.details ?? [];
  const selectedStable = stableTranscriptId(selectedTranscript ?? "");
  return <div className="protein-match-evidence">
    <EvidenceGrid items={[
      [<EvidenceHelpLabel key={`${source}-aa-change`} label={`${source} P/LP report with the same protein change`} help={CLINVAR_AA_MATCH_HELP}/>, proteinMatchDisplayStatus(evidence, "change", selectedTranscript)],
      [<EvidenceHelpLabel key={`${source}-aa-residue`} label={`${source} P/LP missense report at the same residue`} help={CLINVAR_AA_MATCH_HELP}/>, proteinMatchDisplayStatus(evidence, "residue", selectedTranscript)],
    ]}/>
    {details.length > 0 && <details className="protein-match-details"><summary>{details.length} matched P/LP report{details.length === 1 ? "" : "s"}</summary><div>{details.map((item, index) => {
      const sameTranscript = Boolean(selectedStable)
        && stableTranscriptId(item.transcript) === selectedStable;
      return <article key={`${item.kind}:${item.recordId}:${item.sourceAllele}:${item.transcript}:${index}`}>
        <header><strong>{item.kind === "change" ? "Same protein change" : "Different missense change at the same residue"}</strong><span>{cleanLabel(item.classification) || "P/LP"}</span></header>
        <dl>
          <div><dt>Source record</dt><dd>{item.recordId || "—"}</dd></div>
          <div><dt>Source variant</dt><dd className="mono">{item.sourceAllele || "—"}</dd></div>
          <div><dt>Protein change</dt><dd className="mono">{sourceProteinChange(item)}</dd></div>
          <div><dt>Transcript</dt><dd className="mono">{item.transcript || "—"}{sameTranscript ? " · selected" : ""}</dd></div>
          <div><dt>Gene</dt><dd>{item.gene || "—"}</dd></div>
          <div><dt>Condition</dt><dd>{item.disease || "Not supplied by this source"}</dd></div>
        </dl>
      </article>;
    })}</div></details>}
  </div>;
}

function ClinGenVariantEvidence({ evidence, loading, error, compact, proteinMatch, selectedTranscript }: {
  evidence: ClinGenErepoVariant | null;
  loading: boolean;
  error: string;
  compact: NonNullable<VariantRow["clingenErepo"]>;
  proteinMatch: ProteinMatchEvidence | undefined;
  selectedTranscript: string | undefined;
}) {
  const assertions = evidence?.assertions ?? [];
  return <EvidenceSection eyebrow="Expert-panel evidence" title="ClinGen variant curations">
    <ProteinMatchEvidenceBlock source="ClinGen" evidence={proteinMatch} selectedTranscript={selectedTranscript}/>
    {loading && <p className="clingen-empty">Checking the installed ClinGen Evidence Repository snapshot…</p>}
    {!loading && evidence?.available && assertions.length === 0 && <div className="clingen-empty"><strong>No ClinGen variant classification found.</strong></div>}
    {!loading && evidence?.available && assertions.length > 0 && <div className="clingen-assertions">{assertions.map((item) => <article key={item.uuid}>
      <header><div><strong>{item.disease || "Disease not specified"}</strong><span>{item.mondo_id || "No MONDO ID"} · {item.mode_of_inheritance || "MOI not specified"}</span></div><span className={`clingen-classification ${item.assertion.toLowerCase().replaceAll(" ", "-")}`}>{item.assertion}</span></header>
      <dl><div><dt>Expert panel</dt><dd>{item.expert_panel || "—"}</dd></div><div><dt>Approval date</dt><dd>{item.approval_date || "—"}</dd></div><div><dt>ClinGen Allele ID</dt><dd>{item.caid || "—"}</dd></div><div><dt>ClinVar Variation ID</dt><dd>{item.clinvar_variation_id || "—"}</dd></div></dl>
      {(item.evidence_met || item.evidence_not_met) && <div className="clingen-codes"><span><b>Criteria met:</b> {item.evidence_met || "Not listed"}</span><span><b>Criteria not met:</b> {item.evidence_not_met || "Not listed"}</span></div>}
      {item.interpretation_summary && <details><summary>Expert-panel interpretation summary</summary><p>{item.interpretation_summary}</p></details>}
      <footer>{item.evidence_repo_link && <a href={item.evidence_repo_link} target="_blank" rel="noreferrer">Evidence Repository record ↗</a>}{item.guideline && <a href={item.guideline} target="_blank" rel="noreferrer">Applied guideline ↗</a>}{item.pubmed && <span>PMIDs: {item.pubmed}</span>}</footer>
    </article>)}</div>}
    {!loading && (!evidence?.available || error) && compact.length > 0 && <div className="clingen-assertions compact-fallback"><div className="clingen-warning">Full local assertion details are unavailable. Showing the compact evidence embedded in this VCF.</div>{compact.map((item) => <article key={item.uuid}><header><div><strong>{item.disease}</strong><span>{item.mondoId || "No MONDO ID"} · {item.modeOfInheritance || "MOI not specified"}</span></div><span className="clingen-classification">{item.assertion}</span></header><footer><span>{item.expertPanel} · approved {item.approvalDate || "date unavailable"} · {item.caid}</span></footer></article>)}</div>}
    {!loading && (!evidence?.available || error) && compact.length === 0 && <div className="clingen-empty unavailable"><strong>ClinGen variant-curation snapshot unavailable</strong><span>{error || evidence?.error || "Install or update it from Annotation datasets."} Absence cannot be assessed.</span></div>}
  </EvidenceSection>;
}

const GENIA_CLASS_LABELS: Record<string, string> = {
  P: "Pathogenic",
  LP: "Likely pathogenic",
  VUS: "Uncertain significance",
  LB: "Likely benign",
  B: "Benign",
  NC: "Not classified",
  RF: "Risk factor",
};

function geniaClassificationLabel(code: string, supplied?: string) {
  const normalized = code.trim().toUpperCase();
  return GENIA_CLASS_LABELS[normalized] || supplied || `Unrecognized GenIA classification (${code || "blank"})`;
}

function geniaRelevantSubjects(value: number | null) {
  if (value === null) return "Not reported";
  if (value === 0) return "0 reported · not evidence that the variant is benign";
  return `${value.toLocaleString()} reported subject${value === 1 ? "" : "s"}`;
}

function GeniaVariantEvidence({ evidence, loading, error, compact, proteinMatch, selectedTranscript }: {
  evidence: GeniaVariant | null;
  loading: boolean;
  error: string;
  compact: NonNullable<VariantRow["genia"]>;
  proteinMatch: ProteinMatchEvidence | undefined;
  selectedTranscript: string | undefined;
}) {
  if (
    compact.length === 0
    && (loading || !evidence?.available)
    && !proteinMatch?.evaluated
  ) return null;
  const current = evidence?.records ?? [];
  const useCompact = current.length === 0 && compact.length > 0;
  const records = current.length ? current.map((item) => ({
    id: item.record_id,
    shortName: item.short_name,
    classCode: item.class_code,
    classLabel: item.class_label,
    relevantSubjects: item.relevant_subjects,
  })) : compact.map((item) => ({
    id: item.recordId,
    shortName: item.shortName,
    classCode: item.classCode,
    classLabel: geniaClassificationLabel(item.classCode),
    relevantSubjects: item.relevantSubjects,
  }));
  return <EvidenceSection eyebrow="Curated variant evidence" title="GenIA">
    <ProteinMatchEvidenceBlock source="GenIA" evidence={proteinMatch} selectedTranscript={selectedTranscript}/>
    {loading && <p className="clingen-empty">Checking the installed GenIA exact-allele index…</p>}
    {!loading && evidence?.available && current.length === 0 && compact.length === 0 && <div className="clingen-empty"><strong>No GenIA variant record found for this exact GRCh38 allele.</strong></div>}
    {!loading && useCompact && <div className="genia-evidence-note"><strong>{evidence?.available ? "No match in the currently installed GenIA export." : "Current GenIA variant index unavailable."}</strong><span>Showing the compact GenIA evidence embedded in this VCF.</span></div>}
    {!loading && records.length > 0 && <div className="genia-variant-records">{records.map((item, index) => {
      const code = item.classCode.trim().toUpperCase();
      const label = geniaClassificationLabel(code, item.classLabel);
      return <article key={`${item.id}:${index}`}><header><div><strong>{item.shortName || "Variant name not provided"}</strong><span>Exact genomic allele · GenIA record {item.id || "ID unavailable"}</span></div><span className={`genia-classification class-${code.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>{label}</span></header><dl><div><dt>Classification code</dt><dd>{code || "—"}</dd></div><div><dt>Disease-relevant subjects in GenIA</dt><dd>{geniaRelevantSubjects(item.relevantSubjects)}</dd></div></dl></article>;
    })}</div>}
  </EvidenceSection>;
}

function EvidenceHelpLabel({ label, help }: { label: string; help: string }) {
  return <span className="evidence-help-label">{label}<span className="evidence-help" role="note" tabIndex={0} title={help} data-tooltip={help} aria-label={help}>?</span></span>;
}

function EvidenceGrid({ items }: { items: [React.ReactNode, React.ReactNode][] }) {
  return <dl className="evidence-grid">{items.map(([label, value], index) => <div key={typeof label === "string" ? label : index}><dt>{label}</dt><dd>{value === null || value === undefined || value === "" ? "—" : value}</dd></div>)}</dl>;
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

function formatEta(seconds: number) {
  const total = Math.round(seconds);
  if (total < 90) return `${total} s`;
  const minutes = Math.round(total / 60);
  if (minutes < 90) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours} h${remainder ? ` ${remainder} min` : ""}`;
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

function PhenotypePanel({ initialIndividualId = null }: { initialIndividualId?: string | null }) {
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

  useEffect(() => {
    if (!initialIndividualId) return;
    getPhenotypeIndividuals(initialIndividualId).then((records) => {
      const exact = records.find((record) => record.individual_id === initialIndividualId);
      if (exact) { setForm(phenotypeToForm(exact)); setTab("manual"); }
      else { setForm({ ...EMPTY_PHENOTYPE_FORM, individual_id: initialIndividualId }); setTab("manual"); }
    }).catch(() => undefined);
  }, [initialIndividualId]);

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
        <div className="age-pair">{field("age_at_evaluation", "Age at evaluation", <input type="number" min="0" step="0.1" value={form.age_at_evaluation} onChange={(event) => setForm({ ...form, age_at_evaluation: event.target.value })}/>)}{field("age_at_evaluation_unit", "Unit", <select value={form.age_at_evaluation_unit} onChange={(event) => setForm({ ...form, age_at_evaluation_unit: event.target.value })}><option>years</option><option>months</option><option>weeks</option><option>days</option></select>)}</div>
        <div className="age-pair">{field("age_at_onset", "Age at onset", <input type="number" min="0" step="0.1" value={form.age_at_onset} onChange={(event) => setForm({ ...form, age_at_onset: event.target.value })}/>)}{field("age_at_onset_unit", "Unit", <select value={form.age_at_onset_unit} onChange={(event) => setForm({ ...form, age_at_onset_unit: event.target.value })}><option>years</option><option>months</option><option>weeks</option><option>days</option></select>)}</div>
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
        {validation && <div className="validation-summary"><strong>{validation.valid_individuals} individuals ready</strong><span className={validation.matched_sample_ids.length ? "ok" : ""}>{validation.matched_sample_ids.length} exact VCF sample matches</span><span className={validation.unmatched_sample_ids.length ? "warn" : ""}>{validation.unmatched_sample_ids.length} unmatched sample IDs</span><span className={validation.duplicate_individual_ids.length ? "bad" : ""}>{validation.duplicate_individual_ids.length} duplicate individual IDs</span><span className={Object.keys(validation.ambiguous_sample_ids).length ? "bad" : ""}>{Object.keys(validation.ambiguous_sample_ids).length} ambiguous sample IDs</span>{validation.unmatched_sample_ids.length > 0 && <p>Unmatched links are preserved and may match a VCF imported later: {validation.unmatched_sample_ids.slice(0, 10).join(", ")}{validation.unmatched_sample_ids.length > 10 ? "…" : ""}</p>}{Object.keys(validation.case_insensitive_suggestions).length > 0 && <p>Case-sensitive corrections suggested: {Object.entries(validation.case_insensitive_suggestions).map(([from, to]) => `${from} → ${to}`).join(", ")}</p>}{validation.duplicate_individual_ids.length > 0 && <p className="bad">Duplicate individual IDs — merge or renumber these before importing: {validation.duplicate_individual_ids.slice(0, 10).map((id) => `${id}${validation.duplicate_individual_rows?.[id]?.length ? ` (spreadsheet row${validation.duplicate_individual_rows[id].length === 1 ? "" : "s"} ${validation.duplicate_individual_rows[id].map((row) => row + headerRow).join(", ")})` : ""}`).join("; ")}{validation.duplicate_individual_ids.length > 10 ? " …" : ""}</p>}{Object.keys(validation.ambiguous_sample_ids).length > 0 && <p className="bad">Ambiguous sample IDs — each matches more than one cohort sample: {Object.entries(validation.ambiguous_sample_ids).slice(0, 8).map(([sample, candidates]) => `${sample} → ${candidates.join(" / ")}`).join("; ")}{Object.keys(validation.ambiguous_sample_ids).length > 8 ? " …" : ""}</p>}</div>}
        <div className="phenotype-actions"><button className="secondary-button" onClick={() => { setUpload(null); setPreview(null); setValidation(null); }}>Clear</button><button className="primary-button dark" disabled={working || !validation || validation.duplicate_individual_ids.length > 0 || Object.keys(validation.ambiguous_sample_ids).length > 0} onClick={importUpload}>{working ? "Importing…" : "Import individuals"}</button></div>
      </>}
    </section>}
  </div>;
}

function CohortPanel({ onReview }: {
  onReview: (rows: VariantRow[], summary: ImportSummary, analysisScope: AnalysisScope) => void;
}) {
  const [stats, setStats] = useState<CohortStats | null>(null);
  const [mode, setMode] = useState<"variant" | "gene" | "gene_list" | "region">("variant");
  const [regionQuery, setRegionQuery] = useState("");
  const [geneListText, setGeneListText] = useState("");
  const [savedListsOpen, setSavedListsOpen] = useState(false);
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
  const [excludeConfirmedFrameRestored, setExcludeConfirmedFrameRestored] = useState(true);
  const [maneOnly, setManeOnly] = useState(true);
  const [excludeRepeat, setExcludeRepeat] = useState(true);
  const [excludeSegdup, setExcludeSegdup] = useState(true);
  const [zygosity, setZygosity] = useState<"all" | "heterozygous" | "homozygous" | "hemizygous">("all");
  const [queryResult, setQueryResult] = useState<CohortQueryResult | null>(null);
  const [cohortProfiles, setCohortProfiles] = useState<CohortProfile[]>([]);
  const [queryScopes, setQueryScopes] = useState<Set<AnalysisScope>>(new Set());
  const [queryProfiles, setQueryProfiles] = useState<Set<string>>(new Set());
  const [selectedVariant, setSelectedVariant] = useState<CohortQueryRow | null>(null);
  const [variantDetail, setVariantDetail] = useState<CohortVariantDetail | null>(null);
  const [detailWorking, setDetailWorking] = useState(false);
  const detailRequestToken = useRef(0);
  const [reviewWorking, setReviewWorking] = useState(false);
  const [selectedCarrierIds, setSelectedCarrierIds] = useState<Set<number>>(new Set());
  const [managingSamples, setManagingSamples] = useState(false);
  const [cohortSamples, setCohortSamples] = useState<CohortSample[]>([]);
  const [sampleQuery, setSampleQuery] = useState("");
  const [selectedSampleIds, setSelectedSampleIds] = useState<Set<number>>(new Set());
  const [sampleOperation, setSampleOperation] = useState<"loading" | "removing" | null>(null);
  const [sampleMessage, setSampleMessage] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const sampleWorking = sampleOperation !== null;

  useEffect(() => {
    let active = true;
    Promise.all([getCohortStats(), getCohortProfiles()]).then(([value, profiles]) => {
      if (active) { setStats(value); setCohortProfiles(profiles); setError(""); }
    }).catch(() => {
      if (active) setError("The local cohort service is not running. Start the workbench to index or query VCFs.");
    });
    return () => { active = false; };
  }, []);

  async function loadSamples(query = sampleQuery) {
    setSampleOperation("loading");
    try {
      setCohortSamples(await getCohortSamples(query));
      setSelectedSampleIds(new Set());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Cohort samples could not be loaded.");
    } finally {
      setSampleOperation(null);
    }
  }

  async function removeSelectedSamples() {
    if (!selectedSampleIds.size) return;
    const selectedEntries = cohortSamples.filter((sample) => selectedSampleIds.has(sample.id));
    const names = selectedEntries.slice(0, 5).map((sample) => sample.name).join(", ");
    const more = selectedEntries.length > 5 ? ` and ${selectedEntries.length - 5} more` : "";
    if (!window.confirm(`Remove ${selectedEntries.length} indexed sample entr${selectedEntries.length === 1 ? "y" : "ies"} (${names}${more})? This removes their cohort genotypes; reimporting the source VCF restores them.`)) return;
    const carrierCalls = selectedEntries.reduce((total, sample) => total + sample.carrier_observations, 0);
    setSampleOperation("removing");
    setSampleMessage(`Removing ${selectedEntries.length.toLocaleString()} sample entr${selectedEntries.length === 1 ? "y" : "ies"} and ${carrierCalls.toLocaleString()} carrier calls…`);
    setError("");
    try {
      const result = await removeCohortSamples([...selectedSampleIds]);
      setStats(result.stats);
      setSampleMessage(`${result.removed_count} sample entr${result.removed_count === 1 ? "y" : "ies"} removed · ${result.orphan_variants_removed.toLocaleString()} orphan variants reclaimed.`);
      setQueryResult(null);
      setSelectedVariant(null);
      setVariantDetail(null);
      setSelectedCarrierIds(new Set());
      setCohortSamples(await getCohortSamples(sampleQuery));
      setSelectedSampleIds(new Set());
    } catch (reason) {
      setSampleMessage("");
      setError(reason instanceof Error ? reason.message : "Selected samples could not be removed.");
    } finally {
      setSampleOperation(null);
    }
  }


  function cohortFilterProblem(): string | null {
    // An unparseable threshold must block the search, not silently become
    // "no filter" — a dropped frequency cutoff changes the analysis.
    const checks: [string, string, number, number | null][] = [
      [maxPopmax, "gnomAD popmax", 0, 1],
      [minCadd, "CADD phred", 0, null],
      [minAlpha, "AlphaMissense", 0, 1],
      [minSplice, "SpliceAI", 0, 1],
      [loGoFuncClass ? minLoGoFunc : "", "LoGoFunc probability", 0, 1],
    ];
    for (const [raw, label, min, max] of checks) {
      if (!raw.trim()) continue;
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed < min || (max !== null && parsed > max)) {
        return `${label}: enter a number${max !== null ? ` from ${min} to ${max}` : ""} using a decimal point, for example 0.01.`;
      }
    }
    return null;
  }

  async function searchCohort() {
    const filterProblem = cohortFilterProblem();
    if (filterProblem) {
      setError(filterProblem);
      return;
    }
    if (mode === "variant" && !exactQuery.trim()) {
      setError("Enter a variant such as 4:1004329:C:T or an rsID.");
      return;
    }
    if (mode === "region" && !regionQuery.trim()) {
      setError("Enter a genomic window like 1:117000000-117500000.");
      return;
    }
    if (mode === "gene_list" && !parseGeneList(geneListText).size) {
      setError("Paste at least one gene symbol, or insert a saved list.");
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
        analysis_scopes: [...queryScopes],
        profile_hashes: [...queryProfiles],
        limit: 1000,
      } : {
        mode,
        ...(mode === "gene_list"
          ? { genes: [...parseGeneList(geneListText)] }
          : mode === "region"
            ? { region: regionQuery.trim() }
            : { gene: gene.trim().toUpperCase() }),
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
        analysis_scopes: [...queryScopes],
        profile_hashes: [...queryProfiles],
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

  const variantGroups = useMemo(() => {
    const groups = new Map<string, {
      row: CohortQueryRow;
      rows: CohortQueryRow[];
      carrierIds: Set<number>;
    }>();
    for (const row of queryResult?.rows ?? []) {
      const existing = groups.get(row.variant_key);
      if (existing) {
        existing.rows.push(row);
        existing.carrierIds.add(row.sample_entry_id);
      } else {
        groups.set(row.variant_key, {
          row,
          rows: [row],
          carrierIds: new Set([row.sample_entry_id]),
        });
      }
    }
    return [...groups.values()];
  }, [queryResult]);
  const resultCarrierIds = new Set(queryResult?.rows.map((row) => row.sample_entry_id) ?? []);
  const selectedReviewRows = queryResult?.rows.filter(
    (row) => selectedCarrierIds.has(row.sample_entry_id),
  ) ?? [];
  const allResultCarriersSelected = resultCarrierIds.size > 0
    && [...resultCarrierIds].every((sampleId) => selectedCarrierIds.has(sampleId));
  const detailCarrierIds = new Set(
    variantDetail?.rows.map((row) => row.sample_entry_id) ?? [],
  );
  const allDetailCarriersSelected = detailCarrierIds.size > 0
    && [...detailCarrierIds].every((sampleId) => selectedCarrierIds.has(sampleId));
  const selectedDetailRows = variantDetail?.rows.filter(
    (row) => selectedCarrierIds.has(row.sample_entry_id),
  ) ?? [];

  function toggleAllResultCarriers() {
    setSelectedCarrierIds(allResultCarriersSelected ? new Set() : new Set(resultCarrierIds));
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
          ], { intake: "server-records" });
          parserWarnings.push(...parsed.summary.warnings);
          if (!intakeQc.length) intakeQc.push(...parsed.summary.intakeQc);
          const selections = new Map(sourceFile.selections.map((selection) => [
            `${selection.sample}\t${selection.variant_key}`,
            selection,
          ]));
          parsed.rows.forEach((row) => {
            const selection = selections.get(`${row.sample}\t${normalizedVariantKey(row)}`);
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
          "Complete source records were restored from the indexed VCFs; every standard evidence field reflects the full record.",
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
      }, rows.some((row) => row.analysis_scope === "whole_genome" || row.import_profile === "prefiltered") ? "whole_genome" : "exome");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Source annotations could not be loaded for review.");
    } finally {
      setReviewWorking(false);
    }
  }

  async function loadIndividualsForReview(sampleIds: number[]) {
    if (!sampleIds.length) return;
    setReviewWorking(true);
    setError("");
    try {
      const source = await getCohortSampleReview(sampleIds);
      const reviewRows: VariantRow[] = [];
      const parserWarnings: string[] = [];
      const intakeQc: ImportSummary["intakeQc"] = [];
      const profileWarnings: string[] = [];

      for (const sourceFile of source.files) {
        const parsed = await parseVcfFiles([
          new File([sourceFile.vcf], sourceFile.name, { type: "text/vcf" }),
        ], { intake: "server-records" });
        reviewRows.push(...parsed.rows.map((row) => ({
          ...row,
          source: sourceFile.source_path,
        })));
        parserWarnings.push(...parsed.summary.warnings);
        intakeQc.push(...parsed.summary.intakeQc);
        const sampleNames = sourceFile.samples.map((sample) => sample.sample).join(", ");
        if (sourceFile.import_profile === "prefiltered") {
          const filters = sourceFile.prefilter_options as Partial<WgsPrefilterOptions>;
          profileWarnings.push(
            `${fileName(sourceFile.source_path)} (${sampleNames}): Compact WGS candidate profile · ${sourceFile.record_count.toLocaleString()} stored records · popmax ${filters.max_gnomad_popmax ?? "not limited"} · noncoding ${filters.noncoding_mode ?? "unspecified"}.`,
          );
        } else {
          profileWarnings.push(
            `${fileName(sourceFile.source_path)} (${sampleNames}): ${cohortProfileLabel(sourceFile.import_profile, sourceFile.analysis_scope)} · ${sourceFile.record_count.toLocaleString()} stored carrier records.`,
          );
        }
      }
      if (!reviewRows.length) {
        throw new Error("The selected individuals have no stored PASS carrier records to review.");
      }
      onReview(reviewRows, {
        files: source.files.length,
        samples: source.sample_entries,
        passRecords: source.records,
        excludedNonPass: 0,
        rows: reviewRows.length,
        warnings: [
          "Complete stored review sets were loaded using each source's original cohort import profile.",
          ...profileWarnings,
          ...source.warnings,
          ...parserWarnings,
        ],
        intakeQc,
      }, source.analysis_scope);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Selected individuals could not be loaded for review.");
    } finally {
      setReviewWorking(false);
    }
  }

  async function openVariantDetail(row: CohortQueryRow) {
    // Responses may arrive out of order when variants are clicked in quick
    // succession; only the most recent request may write state, or variant
    // B's header can end up over variant A's carriers.
    const token = ++detailRequestToken.current;
    setSelectedVariant(row);
    setVariantDetail(null);
    setDetailWorking(true);
    try {
      const detail = await getCohortVariantDetail(row.variant_key);
      if (detailRequestToken.current !== token) return;
      setVariantDetail(detail);
    } catch (reason) {
      if (detailRequestToken.current !== token) return;
      setError(reason instanceof Error ? reason.message : "Variant details could not be loaded.");
    } finally {
      if (detailRequestToken.current === token) setDetailWorking(false);
    }
  }

  return <div className="cohort-page">
    <div className="content-header"><div><p className="eyebrow">Genotype-first discovery</p><h1>Search the local cohort</h1><p className="subtitle">Find every individual carrying an exact variant, or carriers of qualifying variants in a gene.</p></div><div className="cohort-header-actions">{queryResult?.rows.length ? <button className="secondary-button" onClick={() => { if (confirmResearchUseExport()) downloadCohortTsv(queryResult.rows); }}>Export carriers</button> : null}</div></div>

    <div className="cohort-stats">
      <Stat value={stats?.individuals ?? 0} label="individuals"/>
      <Stat value={stats?.files ?? 0} label="indexed VCFs"/>
      <Stat value={stats?.variants ?? 0} label="unique variants"/>
      <Stat value={stats?.carrier_observations ?? 0} label="carrier calls"/>
    </div>
    {stats && <div className={`cohort-profile-banner ${stats.prefiltered_files ? "prefiltered" : "full"}`}><strong>{stats.prefiltered_files ? stats.full_files ? "Mixed cohort index" : "Compact clinical cohort index" : "Full cohort index"}</strong><span>{stats.full_files.toLocaleString()} full file{stats.full_files === 1 ? "" : "s"} · {stats.prefiltered_files.toLocaleString()} prefiltered file{stats.prefiltered_files === 1 ? "" : "s"}{stats.prefiltered_files ? " · absent noncoding variants may have been excluded during import" : " · exact searches are exhaustive for indexed PASS carrier calls"}</span></div>}


    <div className="cohort-control-grid">
      <section className="cohort-card">
        <div className="cohort-card-head"><div><p className="eyebrow">Cohort data</p><h2>Cohort membership</h2></div><div className="cohort-card-head-actions"><button className="secondary-button" onClick={() => { const opening = !managingSamples; setManagingSamples(opening); setSampleMessage(""); if (opening) void loadSamples(""); }}>{managingSamples ? "Close sample manager" : "Manage samples"}</button><span className="local-only-badge">SQLite · local only</span></div></div>
        <p>Samples enter and leave Cohort search through the <strong>Sample Library</strong>: keep a review in the library with “Include qualifying variants in Cohort Search” enabled, or use the library cards and bulk actions (Add to Cohort Search, Repair, Rebuild, Remove). Every indexed record stays on this workstation.</p>
        {managingSamples && <section className="cohort-sample-manager">
          <div className="cohort-results-head"><div><p className="eyebrow">Indexed entries</p><h2>Manage cohort samples</h2></div><span>Removal is recoverable by reimporting the source VCF.</span></div>
          <div className="sample-manager-toolbar"><label className="search"><Icon name="search"/><input value={sampleQuery} onChange={(event) => setSampleQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void loadSamples()} placeholder="Find sample ID…"/></label><button className="secondary-button" disabled={sampleWorking} onClick={() => void loadSamples()}>{sampleOperation === "removing" ? "Removing…" : sampleOperation === "loading" ? "Loading…" : "Search"}</button><button className="secondary-button" disabled={!cohortSamples.length} onClick={() => setSelectedSampleIds(selectedSampleIds.size === cohortSamples.length ? new Set() : new Set(cohortSamples.map((sample) => sample.id)))}>{selectedSampleIds.size === cohortSamples.length && cohortSamples.length ? "Clear all" : "Select all shown"}</button><button className="danger-button" disabled={sampleWorking || !selectedSampleIds.size} onClick={() => void removeSelectedSamples()}>Remove selected ({selectedSampleIds.size})</button></div>
          {sampleMessage && <div className={sampleOperation === "removing" ? "alert" : "alert success"}>{sampleMessage}</div>}
          {cohortSamples.length ? <div className="cohort-sample-list">{cohortSamples.map((sample) => <label key={sample.id}><input type="checkbox" checked={selectedSampleIds.has(sample.id)} onChange={(event) => setSelectedSampleIds((current) => toggleSet(current, sample.id, event.target.checked))}/><span><strong>{sample.name}</strong><small title={sample.source_path}>{fileName(sample.source_path)} · {sample.carrier_observations.toLocaleString()} carrier calls</small></span><em className={sample.import_profile}>{cohortProfileLabel(sample.import_profile, sample.analysis_scope)}</em></label>)}</div> : !sampleWorking && <div className="empty-state compact"><h2>No indexed samples</h2><p>Add an annotated VCF to populate the cohort.</p></div>}
        </section>}
      </section>

      <section className="cohort-card query-card">
        <div className="cohort-card-head"><div><p className="eyebrow">Carrier query</p><h2>Who carries it?</h2></div></div>
        <div className="query-mode cohort-query-tabs" role="group" aria-label="Cohort query type"><button className={mode === "variant" ? "active" : ""} onClick={() => setMode("variant")}>Exact variant</button><button className={mode === "gene" ? "active" : ""} onClick={() => setMode("gene")}>Qualifying variants in gene</button><button className={mode === "gene_list" ? "active" : ""} onClick={() => setMode("gene_list")}>Gene list</button><button className={mode === "region" ? "active" : ""} onClick={() => { setMode("region"); setImpacts(new Set(IMPACTS)); }}>Genomic region</button></div>
        {mode === "variant" ? <><label className="form-field"><span>Variant, locus, or rsID</span><input value={exactQuery} onChange={(event) => setExactQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && searchCohort()} placeholder="4:1004329:C:T or rs121918472" /></label><p className="query-note">Exact searches return all indexed non-reference carriers; pathogenicity and frequency filters are not applied.{stats?.prefiltered_files ? " Compact-profile files may not contain noncoding variants excluded during import." : ""}</p></> : <>
          {mode === "region" ? <><label className="form-field"><span>Genomic window (GRCh38)</span><input value={regionQuery} onChange={(event) => setRegionQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && searchCohort()} placeholder="1:117,000,000-117,500,000 (up to 5 Mb)" /></label><p className="query-note">For non-coding work: all IMPACT tiers were selected when you opened this tab — non-coding records are MODIFIER and would otherwise be hidden. Narrow the chips below to focus.</p></>
          : mode === "gene" ? <label className="form-field"><span>Gene symbol</span><input value={gene} onChange={(event) => setGene(event.target.value.toUpperCase())} onKeyDown={(event) => event.key === "Enter" && searchCohort()} placeholder="NFKB1" /></label>
          : <div className="gene-list-query"><label className="form-field"><span>Gene symbols <small>{parseGeneList(geneListText).size} unique</small></span><textarea value={geneListText} onChange={(event) => setGeneListText(event.target.value)} rows={5} placeholder={"NFKB1\nCTLA4\nSTAT3 — or insert a saved list"} spellCheck={false} /></label><div className="gene-list-query-actions"><button className="secondary-button" onClick={() => setSavedListsOpen((current) => !current)}>Insert saved list</button>{savedListsOpen && <div className="gene-list-menu">{storedCustomGeneLists().length === 0 && <span>No saved lists yet — create them under Gene lists.</span>}{storedCustomGeneLists().map((list) => <button key={list.id} onClick={() => { setGeneListText((current) => [current.trim(), [...list.genes].sort().join("\n")].filter(Boolean).join("\n")); setSavedListsOpen(false); }}>{list.name} · {list.genes.size}</button>)}</div>}</div></div>}
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
        <details className="cohort-profile-filters"><summary>Assay and import profile filters {queryScopes.size + queryProfiles.size ? `(${queryScopes.size + queryProfiles.size})` : ""}</summary><p>Leave blank to search every indexed carrier. These filters improve comparability but do not turn absence into a negative genotype call.</p><div className="qualifying-checks"><Check label="WES / exome" checked={queryScopes.has("exome")} onChange={(checked) => setQueryScopes((current) => toggleSet(current, "exome", checked))}/><Check label="Whole genome" checked={queryScopes.has("whole_genome")} onChange={(checked) => setQueryScopes((current) => toggleSet(current, "whole_genome", checked))}/></div><div className="cohort-profile-list">{cohortProfiles.map((profile) => <Check key={profile.profile_hash} label={profile.profile_label} checked={queryProfiles.has(profile.profile_hash)} onChange={(checked) => setQueryProfiles((current) => toggleSet(current, profile.profile_hash, checked))} note={`${profile.sample_entries} sample entr${profile.sample_entries === 1 ? "y" : "ies"}`}/>)}</div></details>
        <div className="cohort-query-footer"><label>Genotype<select value={zygosity} onChange={(event) => setZygosity(event.target.value as typeof zygosity)}><option value="all">All carriers</option><option value="heterozygous">Heterozygous</option><option value="homozygous">Homozygous (incl. single-copy X/Y)</option><option value="hemizygous">Hemizygous (incl. single-copy X/Y unless recorded female)</option></select></label><button className="primary-button dark" disabled={working} onClick={searchCohort}><Icon name="search" />{working ? "Searching…" : "Find carriers"}</button></div>
      </section>
    </div>

    {error && <div className="alert error cohort-alert">{error}</div>}
    {queryResult && <section className="cohort-results">
      <div className="cohort-results-head">
        <div><p className="eyebrow">Query results</p><h2>{queryResult.variants.toLocaleString()} unique variant{queryResult.variants === 1 ? "" : "s"}</h2></div>
        <div className="cohort-result-actions">
          <span>{queryResult.individuals.toLocaleString()} individuals · {queryResult.total.toLocaleString()} matched carrier calls{queryResult.truncated ? ` · first ${queryResult.limit.toLocaleString()} calls shown` : ""}{selectedCarrierIds.size ? ` · ${selectedCarrierIds.size.toLocaleString()} selected` : ""}</span>
          {queryResult.rows.length > 0 && <>
            <button className="secondary-button" disabled={reviewWorking} onClick={toggleAllResultCarriers}>{allResultCarriersSelected ? "Clear selection" : "Select all shown individuals"}</button>
            <button className="secondary-button" disabled={reviewWorking || !selectedReviewRows.length} onClick={() => void reviewRows(selectedReviewRows)}>{reviewWorking ? "LOADING…" : "Review selected findings"}</button>
            <button className="primary-button dark" disabled={reviewWorking || !selectedCarrierIds.size} onClick={() => void loadIndividualsForReview([...selectedCarrierIds])}>{reviewWorking ? "LOADING STORED REVIEW SETS…" : `LOAD SELECTED INDIVIDUALS (${selectedCarrierIds.size})`}</button>
          </>}
        </div>
      </div>
      <div className="profile-caveat"><strong>{queryResult.represented_profiles.length} import profile{queryResult.represented_profiles.length === 1 ? "" : "s"} represented</strong><span>{queryResult.comparability_warning}</span></div>
      {variantGroups.length ? <div className="cohort-table-scroll"><table className="cohort-table cohort-variant-table cohort-selectable-table"><thead><tr><th>Variant</th><th>Gene / consequence</th><th>Matched carriers</th><th>Annotations</th><th>Indexed profile</th></tr></thead><tbody>{variantGroups.map((group) => {
        const row = group.row;
        const selectedRow = selectedVariant?.variant_key === row.variant_key;
        const sampleNames = [...new Set(group.rows.map((carrier) => carrier.sample))];
        const selectedInGroup = [...group.carrierIds].filter((sampleId) => selectedCarrierIds.has(sampleId)).length;
        const sourceLabels = new Set(group.rows.map((carrier) => carrier.profile_label || cohortProfileLabel(carrier.import_profile, carrier.analysis_scope)));
        const profileLabel = sourceLabels.size > 1 ? "Mixed source profiles" : [...sourceLabels][0];
        return <tr key={row.variant_key} className={selectedRow ? "selected" : ""} tabIndex={0} onClick={() => void openVariantDetail(row)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") void openVariantDetail(row); }}>
          <td><VariantIdentifier row={row}/>{row.rsid && <span className="cell-sub">{row.rsid}</span>}{row.original_assembly && <span className="cell-sub mono">GRCh37: {row.original_chrom}:{row.original_pos} {row.original_ref}›{row.original_alt}</span>}</td>
          <td><strong>{row.gene}</strong><span className={`impact-pill ${row.impact.toLowerCase()}`}>{row.impact}</span><span className="cell-sub">{row.hgvsp || row.hgvsc || cleanLabel(row.consequence)} · {row.mane ? "MANE" : row.picked ? "PICK fallback" : "alternate transcript"}</span></td>
          <td><strong>{group.carrierIds.size.toLocaleString()} individual{group.carrierIds.size === 1 ? "" : "s"}</strong><span className="cell-sub">{sampleNames.slice(0, 4).join(", ")}{sampleNames.length > 4 ? ` +${sampleNames.length - 4}` : ""}</span>{selectedInGroup > 0 && <small className="carrier-selected-count">{selectedInGroup} selected</small>}</td>
          <td><span className={isPathogenic(row.clinvar) || isClinvarConflictWithPathogenic(row.clinvar, row.clinvar_conflicting) ? "clinvar pathogenic" : "clinvar"}>{cleanLabel(row.clinvar)}</span><span className="cell-sub">popmax {compactNumber(row.gnomad_popmax)} · CADD {compactNumber(row.cadd, 1)} · AM {compactNumber(row.alpha_missense)} · SpliceAI {compactNumber(row.spliceai)} · promoterAI {compactNumber(row.promoterai)}{row.logofunc_prediction ? ` · LoGoFunc ${row.logofunc_prediction}` : ""}</span></td>
          <td><strong>{profileLabel}</strong><span className="cell-sub">Click to inspect carriers and source files</span></td>
        </tr>;
      })}</tbody></table></div> : <div className="empty-state compact"><span className="empty-icon"><Icon name="search" /></span><h2>No carriers found</h2><p>{mode === "variant" ? "Check the GRCh38 locus and alleles, or try the rsID." : "Relax one or more qualification filters."}</p></div>}
      {selectedVariant && <div className="cohort-variant-detail">
        <div className="cohort-detail-head"><div><p className="eyebrow">Variant details</p><h2><VariantIdentifier row={selectedVariant} full/> {selectedVariant.rsid ? <small>{selectedVariant.rsid}</small> : null}</h2></div><div><button className="primary-button dark" disabled={detailWorking || reviewWorking} onClick={() => void reviewRows(variantDetail?.rows ?? queryResult.rows.filter((row) => row.variant_key === selectedVariant.variant_key))}>{reviewWorking ? "LOADING SOURCE ANNOTATIONS…" : "REVIEW THIS VARIANT"}</button><button className="secondary-button" onClick={() => { setSelectedVariant(null); setVariantDetail(null); }}>Close</button></div></div>
        {detailWorking && <div className="cohort-detail-loading">Loading every stored transcript and carrier…</div>}
        <div className="cohort-detail-grid">
          <section className="cohort-carrier-detail"><h3>Carriers {variantDetail ? `(${variantDetail.individuals})` : ""}</h3>{variantDetail ? <><div className="cohort-detail-carriers">{variantDetail.rows.map((carrier) => <label key={carrier.sample_entry_id}><input type="checkbox" checked={selectedCarrierIds.has(carrier.sample_entry_id)} onChange={(event) => setSelectedCarrierIds((current) => toggleSet(current, carrier.sample_entry_id, event.target.checked))}/><span><strong>{carrier.sample}</strong><small className="mono">{carrier.genotype} · DP {carrier.dp ?? "—"} · GQ {compactNumber(carrier.gq, 1)} · AB {compactNumber(carrier.allele_balance)}</small><small title={carrier.source_path}>{fileName(carrier.source_path)} · {cohortProfileLabel(carrier.import_profile, carrier.analysis_scope)}</small></span></label>)}</div><div className="cohort-detail-review-actions"><button className="secondary-button" disabled={reviewWorking} onClick={() => setSelectedCarrierIds((current) => { const next = new Set(current); for (const sampleId of detailCarrierIds) { if (allDetailCarriersSelected) next.delete(sampleId); else next.add(sampleId); } return next; })}>{allDetailCarriersSelected ? "Clear these carriers" : "Select all carriers"}</button><button className="secondary-button" disabled={reviewWorking || !selectedDetailRows.length} onClick={() => void reviewRows(selectedDetailRows)}>{reviewWorking ? "LOADING…" : "Review selected findings"}</button><button className="primary-button dark" disabled={reviewWorking || !selectedDetailRows.length} onClick={() => void loadIndividualsForReview([...new Set(selectedDetailRows.map((row) => row.sample_entry_id))])}>{reviewWorking ? "LOADING…" : `Load selected individuals (${selectedDetailRows.length})`}</button></div></> : <dl><div><dt>Sample</dt><dd>{selectedVariant.sample}</dd></div><div><dt>Genotype</dt><dd className="mono">{selectedVariant.genotype} · {cleanLabel(selectedVariant.zygosity)}</dd></div><div><dt>Evidence</dt><dd>DP {selectedVariant.dp ?? "—"} · GQ {compactNumber(selectedVariant.gq, 1)} · AB {compactNumber(selectedVariant.allele_balance)} · QUAL {compactNumber(selectedVariant.qual, 1)}</dd></div></dl>}</section>
          <section className="cohort-transcript-detail"><h3>Stored transcript annotations {variantDetail ? `(${variantDetail.annotations.length})` : ""}</h3>{variantDetail ? <div className="cohort-transcript-list">{variantDetail.annotations.map((annotation, index) => <article key={`${annotation.gene}:${annotation.transcript}:${annotation.consequence}:${index}`}><div><strong>{annotation.gene}</strong><span className={`impact-pill ${annotation.impact.toLowerCase()}`}>{annotation.impact}</span><em>{annotation.mane ? "MANE" : annotation.picked ? "PICK" : "alternate"}</em></div><span className="mono">{annotation.transcript || "—"}</span><span>{annotation.hgvsp || annotation.hgvsc || cleanLabel(annotation.consequence)}</span><small>{cleanLabel(annotation.consequence)} · popmax {compactNumber(annotation.gnomad_popmax)} · CADD {compactNumber(annotation.cadd, 1)} · SpliceAI {compactNumber(annotation.spliceai)} · promoterAI {compactNumber(annotation.promoterai)}{annotation.logofunc_prediction ? ` · LoGoFunc ${annotation.logofunc_prediction}` : ""}</small></article>)}</div> : <dl><div><dt>Gene</dt><dd>{selectedVariant.gene}</dd></div><div><dt>Transcript</dt><dd className="mono">{selectedVariant.transcript || "—"}</dd></div><div><dt>HGVS</dt><dd>{selectedVariant.hgvsp || selectedVariant.hgvsc || "—"}</dd></div></dl>}</section>
          <section><h3>Clinical and prediction evidence</h3><dl><div><dt>ClinVar</dt><dd>{cleanLabel(selectedVariant.clinvar)}{selectedVariant.clinvar_conflicting ? ` · ${cleanLabel(selectedVariant.clinvar_conflicting)}` : ""}</dd></div><div><dt>Scores</dt><dd>popmax {compactNumber(selectedVariant.gnomad_popmax)} · CADD {compactNumber(selectedVariant.cadd, 1)} · AlphaMissense {compactNumber(selectedVariant.alpha_missense)} · SpliceAI {compactNumber(selectedVariant.spliceai)} · promoterAI {compactNumber(selectedVariant.promoterai)}</dd></div><div><dt>LoGoFunc</dt><dd>{selectedVariant.logofunc_prediction || (selectedVariant.logofunc_allele_available ? "Allele available; transcript/protein mismatch" : "Not annotated")}{selectedVariant.logofunc_prediction ? ` · N ${compactNumber(selectedVariant.logofunc_neutral)} · GOF ${compactNumber(selectedVariant.logofunc_gof)} · LOF ${compactNumber(selectedVariant.logofunc_lof)}` : ""}</dd></div><div><dt>LOFTEE</dt><dd>{cleanLabel(selectedVariant.loftee)} · 50 bp {cleanLabel(selectedVariant.loftee_50bp)}{selectedVariant.ptc_distance !== null ? ` · PTC distance ${selectedVariant.ptc_distance}` : ""}</dd></div><div><dt>Regions</dt><dd>{selectedVariant.repeat_masker ? "RepeatMasker" : "Not RepeatMasker"} · {selectedVariant.segdup ? "SegDup" : "Not SegDup"}</dd></div></dl></section>
          <section><h3>Source and coordinates</h3><dl><div><dt>GRCh38</dt><dd className="mono"><VariantIdentifier row={selectedVariant} full/></dd></div>{selectedVariant.original_assembly && <div><dt>Original</dt><dd className="mono">{selectedVariant.original_assembly}: {selectedVariant.original_chrom}:{selectedVariant.original_pos} {selectedVariant.original_ref}›{selectedVariant.original_alt}</dd></div>}<div><dt>Selected source</dt><dd title={selectedVariant.source_path}>{selectedVariant.source_path}</dd></div><div><dt>Index profile</dt><dd>{cohortProfileLabel(selectedVariant.import_profile, selectedVariant.analysis_scope)}</dd></div></dl></section>
        </div>
        <CcreContextPanel compact chrom={selectedVariant.chrom} pos={selectedVariant.pos} refAllele={selectedVariant.ref} altAllele={selectedVariant.alt}/>
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
        <GeneSet label="IEI autosomal-dominant" genes={geneSets.dominant} defaultGenes={bundledGeneSets.dominant} detail="IUIS 2024 · autosomal-dominant (AD) inheritance" setter={(genes) => setGeneSet("dominant", genes)} reset={() => resetGeneSet("dominant")}/>
        <GeneSet label="IEI haploinsufficiency" genes={geneSets.hi} defaultGenes={bundledGeneSets.hi} detail="manually curated from IUIS 2024 · default" setter={(genes) => setGeneSet("hi", genes)} reset={() => resetGeneSet("hi")}/>
        <div className="derived-gene-set"><span className="file-badge"><Icon name="dna" /></span><div><strong>LoF constrained</strong><span>{lofConstrainedGenes.size.toLocaleString()} genes · derived from bundled gnomAD constraint</span><small>pLI ≥ 0.9 or LOEUF &lt; 0.6.</small></div><span className="read-only-badge">Automatic</span></div>
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

function AboutPanel() {
  const [status, setStatus] = useState<SoftwareUpdateStatus | null>(null);
  const [check, setCheck] = useState<SoftwareUpdateCheck | null>(null);
  const [installResult, setInstallResult] = useState<SoftwareUpdateResult | null>(null);
  const [working, setWorking] = useState<"" | "check" | "install" | "rollback" | "restart">("");
  const [error, setError] = useState("");
  useEffect(() => {
    getSoftwareUpdateStatus().then(setStatus).catch((reason) => setError(reason instanceof Error ? reason.message : "The local service could not be reached."));
  }, []);
  async function runCheck() {
    setWorking("check"); setError(""); setCheck(null); setInstallResult(null);
    try { setCheck(await checkForSoftwareUpdate()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The update check failed."); }
    finally { setWorking(""); }
  }
  async function runInstall() {
    if (!check?.latest_version) return;
    if (!window.confirm(`Install GUIDE-IEI ${check.latest_version}? Your datasets, sample library, and edited configuration are not touched. The workbench restarts afterwards.`)) return;
    setWorking("install"); setError("");
    try {
      const result = await installSoftwareUpdate();
      setInstallResult(result);
      setCheck(null);
      setStatus(await getSoftwareUpdateStatus().catch(() => status));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The update could not be installed."); }
    finally { setWorking(""); }
  }
  async function runRollback() {
    if (!status?.rollback_version) return;
    if (!window.confirm(`Return to GUIDE-IEI ${status.rollback_version}? Your configuration file and data stay exactly as they are; the workbench restarts afterwards.`)) return;
    setWorking("rollback"); setError("");
    try {
      const result = await rollbackSoftwareUpdate();
      setInstallResult(result);
      setStatus(await getSoftwareUpdateStatus().catch(() => status));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The previous version could not be restored."); }
    finally { setWorking(""); }
  }
  async function runRepair() {
    // A half-applied tree self-reports the NEW version, so check() offers
    // nothing to install — but the backend deliberately accepts a
    // same-version reinstall while its interrupted-update sentinel stands.
    if (!window.confirm("Repair GUIDE-IEI by reinstalling the current release? Your datasets, sample library, and edited configuration are not touched.")) return;
    setWorking("install"); setError("");
    try {
      const result = await installSoftwareUpdate();
      setInstallResult(result);
      setCheck(null);
      setStatus(await getSoftwareUpdateStatus().catch(() => status));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The repair could not be completed."); }
    finally { setWorking(""); }
  }
  async function restartNow() {
    setWorking("restart"); setError("");
    try {
      await restartWorkbenchService();
      const deadline = Date.now() + 120_000;
      let healthy = false;
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        try { await getServiceHealth(); healthy = true; break; } catch { /* service restarting */ }
      }
      if (!healthy) throw new Error("The service did not come back within two minutes. Close the launcher window and start GUIDE-IEI again.");
      window.location.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The workbench could not be restarted.");
      setWorking("");
    }
  }
  const upToDate = check?.ok && check.update_available === false && !check.error;
  return <div className="gene-knowledge-settings about-panel">
    <div className="content-header"><div><p className="eyebrow">About</p><h1>GUIDE-IEI</h1><p className="subtitle"><strong>G</strong>enomic <strong>U</strong>ser-friendly <strong>I</strong>n-depth <strong>D</strong>iagnostic-analysis <strong>E</strong>nvironment for <strong>I</strong>nborn <strong>E</strong>rrors of <strong>I</strong>mmunity</p><p className="subtitle">Version {status?.current_version ?? "…"}</p></div></div>
    {error && <div className="alert error">{error}</div>}
    {status?.incomplete_update && !installResult && <div className="alert error software-update-done"><div><strong>The previous update was interrupted.</strong><span>Do not start a new analysis until GUIDE-IEI is repaired — the software may be running a mix of two versions. Your datasets and configuration were not changed.{status.rollback_available ? " Returning to the previous version below also repairs it." : ""}</span></div><button className="primary-button dark" disabled={Boolean(working)} onClick={() => void runRepair()}>{working === "install" ? "Repairing…" : "Repair update"}</button></div>}
    {status?.restart_pending && !installResult && <div className="alert software-update-done"><div><strong>An update was installed but is not running yet.</strong><span>{status.full_relaunch_required ? "This update changed the interface, which an in-app restart cannot apply: close the GUIDE-IEI launcher window (the Terminal) completely, then start GUIDE-IEI again." : "Restart to finish it."}</span></div>{!status.full_relaunch_required && <button className="primary-button dark" disabled={Boolean(working)} onClick={() => void restartNow()}>{working === "restart" ? "Restarting…" : "Restart the workbench"}</button>}</div>}
    <section className="gene-resource-section"><div className="section-title"><div><p className="eyebrow">Software updates</p><h2>Keep GUIDE-IEI current</h2></div><button className="secondary-button" disabled={Boolean(working)} onClick={() => void runCheck()}>{working === "check" ? "Checking…" : "Check for updates"}</button></div>
      {check?.error && <div className="alert">{check.error}</div>}
      {upToDate && <div className="alert">GUIDE-IEI {check.current_version} is the newest release.</div>}
      {check?.ok && check.update_available && <article className="software-update-card">
        <div className="section-title"><div><p className="eyebrow">New release</p><h2>GUIDE-IEI {check.latest_version}</h2></div><span className="mini-badge teal">{check.published_at ? new Date(check.published_at).toLocaleDateString() : "available"}</span></div>
        {check.notes && <pre className="release-notes">{check.notes}</pre>}
        {status?.desktop_app || check.desktop_app ? <><a className="primary-button dark" href="https://github.com/yimingluo-md/guide-iei/releases/latest" target="_blank" rel="noreferrer">Open Mac app downloads</a><p className="constraint-note">Download the new Mac app, quit GUIDE-IEI, and replace the app in Applications. Your databases, Sample Library and settings remain separate.</p></> : <><button className="primary-button dark" disabled={Boolean(working)} onClick={() => void runInstall()}>{working === "install" ? "Downloading and installing…" : `Install ${check.latest_version}`}</button><p className="constraint-note">Installation replaces only the software&apos;s own files. Annotation datasets, the sample library, review data, and your edited configuration are never touched, and the current version is kept for rollback.</p></>}
      </article>}
      {installResult && <div className="alert software-update-done">
        <div>
          <strong>{installResult.restored_version ? `GUIDE-IEI ${installResult.restored_version} restored.` : `GUIDE-IEI ${installResult.installed_version} installed.`}</strong>
          <span>
            {installResult.config_review_needed?.length ? " This release updated the annotation configuration; your file was kept and the new version was saved beside it as annotation.config.yaml.new for review." : ""}
            {installResult.container_changed ? " The annotation engine changed. Close the launcher window completely, then open GUIDE-IEI again; it will rebuild the engine automatically before another annotation can start." : ""}
            {installResult.wsl_origin_synced === false ? " The update could not be mirrored to the Windows-side GUIDE-IEI folder — do not use the launcher's -Update option until a later update reports success, or it may reinstate the old version." : ""}
            {installResult.dependencies_changed
              ? " Interface components changed: close the launcher window (the Terminal) completely, then start GUIDE-IEI again — the first start installs the new components and takes about a minute longer."
              : installResult.web_build_required
                ? " The interface changed: close the launcher window (the Terminal) completely, then start GUIDE-IEI again — an in-app restart would keep serving the previous interface. The first start rebuilds it and takes a little longer."
                : ` Restart to run the ${installResult.restored_version ? "restored" : "new"} version.`}
          </span>
        </div>
        {!installResult.dependencies_changed && !installResult.web_build_required && !installResult.container_changed && <button className="primary-button dark" disabled={Boolean(working)} onClick={() => void restartNow()}>{working === "restart" ? "Restarting…" : "Restart the workbench"}</button>}
      </div>}
      {status?.rollback_available && !installResult && <p className="constraint-note">The previously installed version ({status.rollback_version}) is kept. <button className="link-button" disabled={Boolean(working)} onClick={() => void runRollback()}>{working === "rollback" ? "Restoring…" : "Return to it"}</button> if the current one misbehaves. Your configuration file stays exactly as it is now.</p>}
      <p className="constraint-note">When GUIDE-IEI checks GitHub for updates, no patient, sample, variant, phenotype, or analysis data are sent. GitHub receives ordinary web-request metadata, such as your IP address. GUIDE-IEI reaches the network only when you ask it to: this release lookup, annotation-dataset downloads you start, and the optional per-variant SpliceAI lookup, which sends only the variant coordinates you request.</p>
    </section>
  </div>;
}

function StoragePanel() {
  const [stats, setStats] = useState<StorageStats | null>(null);
  const [configuration, setConfiguration] = useState<StorageConfiguration | null>(null);
  const [migrations, setMigrations] = useState<StorageMigrationJob[]>([]);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState<StorageLocationKind | null>(null);
  const [pathDraft, setPathDraft] = useState("");
  const [locationTest, setLocationTest] = useState<StorageLocationTest | null>(null);
  const refresh = async (includeUsage = true) => {
    try {
      if (includeUsage) {
        const [nextStats, nextMigrations] = await Promise.all([
          getStorageStats(), getStorageMigrations(),
        ]);
        setStats(nextStats); setConfiguration(nextStats.storage_configuration); setMigrations(nextMigrations); setError(nextStats.storage_error || "");
      } else {
        const [nextConfiguration, nextMigrations] = await Promise.all([
          getStorageConfiguration(), getStorageMigrations(),
        ]);
        setConfiguration((current) => ({
          ...nextConfiguration,
          locations: nextConfiguration.locations.map((location) => ({
            ...location,
            used_bytes: location.used_bytes ?? current?.locations.find((item) => item.id === location.id)?.used_bytes ?? null,
          })),
        }));
        setMigrations(nextMigrations);
        if (!nextMigrations.some((job) => job.status === "queued" || job.status === "running")) {
          const nextStats = await getStorageStats();
          setStats(nextStats); setConfiguration(nextStats.storage_configuration); setError(nextStats.storage_error || "");
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Storage information is unavailable.");
    }
  };
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!migrations.some((job) => job.status === "queued" || job.status === "running")) return;
    const timer = window.setInterval(() => { void refresh(false); }, 1000);
    return () => window.clearInterval(timer);
  }, [migrations]);
  const locationLabel: Record<StorageLocationKind, string> = {
    annotation: "Annotation datasets",
    data: "Sample Library & Cohort",
    temporary: "Temporary workspace",
  };
  const locationSupplement = (kind: StorageLocationKind) => {
    const location = configuration?.locations.find((item) => item.id === kind);
    if (!location) return "";
    const usage = location.included_with_data
      ? " Included with Sample Library storage."
      : ` ${compactFileSize(location.used_bytes ?? 0)} used by the workbench.`;
    return `${usage}${location.restart_required ? ` Current session: ${location.active_path}.` : ""}${location.windows_path ? ` Windows: ${location.windows_path}.` : ""}`;
  };
  const locationDescription: Record<StorageLocationKind, string> = {
    annotation: `VEP cache, FASTA, dbNSFP, CADD, SpliceAI, ClinVar, SCREEN, PromoterAI, and LoGoFunc.${locationSupplement("annotation")}`,
    data: `Managed review VCFs, cohort SQLite index, phenotype records, and import provenance.${locationSupplement("data")}`,
    temporary: `Browser uploads, prepared VCFs, staging files, and rebuildable whole-genome review caches.${locationSupplement("temporary")}`,
  };
  const activeMigration = migrations.find((job) => job.status === "queued" || job.status === "running");
  const currentEditingPath = editing ? configuration?.locations.find((item) => item.id === editing)?.path : undefined;
  const draftIsCurrentPath = Boolean(editing && pathDraft.trim() && pathDraft.trim() === currentEditingPath);
  const [restarting, setRestarting] = useState(false);
  async function restartService() {
    if (!window.confirm("Restart the local workbench service now? This page stays open; the service is unavailable for a few seconds while pending storage locations become active.")) return;
    setRestarting(true); setError(""); setMessage("Restarting the workbench service…");
    try {
      await restartWorkbenchService();
      const deadline = Date.now() + 90_000;
      let healthy = false;
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        try { await getServiceHealth(); healthy = true; break; } catch { /* service still restarting */ }
      }
      if (!healthy) throw new Error("The service did not come back within 90 seconds. Check the terminal running start_workbench.sh.");
      setMessage("Workbench service restarted; the new storage locations are active.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The service could not be restarted.");
      setMessage("");
    } finally { setRestarting(false); }
  }
  const beginEdit = (location: StorageLocation) => {
    setEditing(location.id); setPathDraft(location.follows_data_root ? "" : location.path); setLocationTest(null); setError(""); setMessage("");
  };
  async function browseForLocation() {
    if (!editing) return;
    setWorking(true); setError("");
    try {
      const selection = await chooseLocalResourceSource(`storage_${editing}`);
      if (!selection.cancelled && selection.path) { setPathDraft(selection.path); setLocationTest(null); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The folder chooser could not be opened. Enter the path manually.");
    } finally { setWorking(false); }
  }
  async function testPath() {
    if (!editing || !pathDraft.trim()) return;
    setWorking(true); setError(""); setLocationTest(null);
    try { setLocationTest(await testStorageLocation(editing, pathDraft.trim())); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The folder could not be tested."); }
    finally { setWorking(false); }
  }
  async function applyFutureLocation() {
    if (!editing) return;
    if (editing === "temporary" && !pathDraft.trim()) {
      setWorking(true); setError("");
      try {
        const result = await setStorageLocation({ kind: "temporary", follow_data_root: true });
        setConfiguration(result.storage); setMessage(result.message); setEditing(null);
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Storage location could not be saved."); }
      finally { setWorking(false); }
      return;
    }
    if (!pathDraft.trim()) return;
    const warning = editing === "data"
      ? "Use this folder for a new Sample Library after restart? Existing samples remain at the current location and will not be available from the new library unless you use Copy existing data instead."
      : `Use this folder for ${locationLabel[editing]} after restart? Existing files are left in the current location.`;
    if (!window.confirm(warning)) return;
    setWorking(true); setError("");
    try {
      const result = await setStorageLocation({ kind: editing, path: pathDraft.trim() });
      setConfiguration(result.storage); setMessage(result.message); setEditing(null); setLocationTest(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Storage location could not be saved."); }
    finally { setWorking(false); }
  }
  async function migrateLocation() {
    if (!editing || !pathDraft.trim()) return;
    const scope = editing === "data" ? "all managed Sample Library, cohort, phenotype, and provenance data" : editing === "annotation" ? "all annotation resources under the current annotation root" : "rebuildable uploads and local review caches";
    if (!window.confirm(`Copy ${scope} to this new folder, verify each copied file, and preserve the original location? This can take a long time for large datasets. You will need to restart the workbench when it finishes.`)) return;
    setWorking(true); setError("");
    try {
      const job = await startStorageMigration(editing, pathDraft.trim());
      setMigrations((current) => [job, ...current]);
      setMessage("Safe storage migration started. The current location remains active until restart.");
      setEditing(null); setLocationTest(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Storage migration could not start."); }
    finally { setWorking(false); }
  }
  async function clean() {
    if (!window.confirm("Remove temporary uploads and rebuildable cache files (including per-sample review projections, which are rebuilt on the next review)? Managed Sample Library VCFs and original external VCFs are preserved.")) return;
    setWorking(true); setError("");
    try { const result = await cleanupStorage(["uploads", "cohort_cache", "wgs_review_cache", "partials", "configs", "projections"]); setStats(result.storage); setConfiguration(result.storage.storage_configuration); setMessage(`${result.removed_files} files removed · ${compactFileSize(result.freed_bytes)} reclaimed.`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Storage cleanup failed."); }
    finally { setWorking(false); }
  }
  async function compact() {
    if (!window.confirm("Compact the SQLite database now? This can take several minutes and cohort operations will pause. No samples or variants are deleted.")) return;
    setWorking(true); setError(""); setMessage("Compacting the database…");
    try { setStats(await compactStorage()); setMessage("Database compacted; unused pages were returned to disk."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Database compaction failed."); }
    finally { setWorking(false); }
  }
  return <div className="data-page storage-page"><div className="data-page-header"><div><p className="eyebrow">Workstation maintenance</p><h1>Storage</h1><p>Choose dedicated local folders for large annotation resources and retained sample data. Managed review VCFs are deduplicated by checksum.</p></div>{stats && <div className="library-summary"><strong>{compactFileSize(stats.total_bytes)}</strong><span>managed workbench data</span></div>}</div>
    {configuration && <section className="storage-locations"><div className="section-title"><div><p className="eyebrow">Locations</p><h2>Where data live</h2></div><span>Changes take effect after restart</span></div><div className="storage-location-list">{configuration.locations.map((location) => <article key={location.id} className={`storage-location-card ${location.available ? "" : "unavailable"}`}><div className="storage-location-title"><div><h3>{locationLabel[location.id]}</h3><p>{locationDescription[location.id]}</p></div><span className={`mini-badge ${location.available ? "teal" : "amber"}`}>{location.available ? "Available" : "Unavailable"}</span></div><code>{location.path}</code><div className="storage-location-meta"><span>{location.follows_data_root ? "Follows Sample Library location" : location.uses_default ? "Software default" : "Custom location"}</span><span>{location.free_bytes === null ? "Space unavailable" : `${compactFileSize(location.free_bytes)} free`}</span>{location.restart_required && <span className="storage-restart">Restart required</span>}</div>{location.warning && <p className="storage-location-warning">{location.warning}</p>}<div className="storage-location-actions"><button className="secondary-button" disabled={working || Boolean(activeMigration)} onClick={() => beginEdit(location)}>Change location</button><button className="secondary-button" disabled={!location.exists || working} onClick={() => void openStorageLocation(location.id).catch((reason) => setError(reason instanceof Error ? reason.message : "Folder could not be opened."))}>Open folder</button></div></article>)}</div>{configuration.locations.some((location) => location.restart_required) && <div className="alert storage-restart-alert"><div><strong>A location change is saved but not active.</strong><span>Restart applies it; this page stays open and reconnects. Running jobs block the restart.</span></div><button className="primary-button dark" disabled={working || restarting || Boolean(activeMigration)} onClick={() => void restartService()}>{restarting ? "Restarting…" : "Restart workbench now"}</button></div>}</section>}
    {editing && <section className="storage-location-editor"><div><p className="eyebrow">Change location</p><h2>{locationLabel[editing]}</h2><p>Enter a dedicated absolute folder path. For a migration, the destination must be a new folder path; the old location is never deleted automatically.</p></div>{editing === "temporary" && <label className="check-row"><input type="checkbox" checked={!pathDraft} onChange={(event) => { setPathDraft(event.target.checked ? "" : configuration?.locations.find((item) => item.id === "temporary")?.path || ""); setLocationTest(null); }} /> <span>Follow Sample Library & Cohort location</span></label>}{!(editing === "temporary" && !pathDraft) && <label className="form-field"><span>Folder path</span><div className="storage-path-row"><input value={pathDraft} onChange={(event) => { setPathDraft(event.target.value); setLocationTest(null); }} placeholder="/Volumes/IEI-data/annotation-resources" spellCheck={false} /><button type="button" className="secondary-button" disabled={working} onClick={() => void browseForLocation()}>Browse…</button></div><small>Browse opens the workstation folder chooser (a new folder can be created there); the path stays editable afterwards.</small></label>}{locationTest && <div className={`location-test ${locationTest.warning ? "warning" : ""}`}><strong>Folder check passed</strong><span>{locationTest.exists ? "Existing folder" : "New folder will be created"} · {locationTest.free_bytes === null ? "available space could not be measured" : `${compactFileSize(locationTest.free_bytes)} free`}</span>{locationTest.warning && <small>{locationTest.warning}</small>}</div>}<div className="storage-editor-actions">{editing !== "temporary" || pathDraft ? <button className="secondary-button" disabled={working || !pathDraft.trim()} onClick={() => void testPath()}>Test location</button> : null}<button className="secondary-button" disabled={working || Boolean(activeMigration) || (editing !== "temporary" && !pathDraft.trim())} onClick={() => void applyFutureLocation()}>Use for future data</button><button className="primary-button dark" disabled={working || Boolean(activeMigration) || !pathDraft.trim() || draftIsCurrentPath} onClick={() => void migrateLocation()}>Copy existing data</button><button className="secondary-button" disabled={working} onClick={() => { setEditing(null); setLocationTest(null); }}>Cancel</button></div>{draftIsCurrentPath && <small className="storage-editor-note">This is the current location. To copy the data elsewhere, choose a destination folder that does not exist yet (for example a new folder on another drive).</small>}{error && <div className="alert error">{error}</div>}{editing === "data" && <small className="storage-editor-note">Use for future data starts an empty Sample Library, or reopens a previously managed folder carrying its identity marker, after restart. Use Copy existing data to retain access to the current samples, cohort search, and phenotype links.</small>}{editing === "annotation" && <small className="storage-editor-note">Individual resources already configured with an absolute custom path, such as an existing dbNSFP file, remain at that path. Existing read-only annotation folders are supported.</small>}</section>}
    {migrations.length > 0 && <section className="storage-migrations"><div className="section-title"><div><p className="eyebrow">Safe copy history</p><h2>Storage migrations</h2></div><span>{activeMigration ? "Copy in progress" : "Original locations retained"}</span></div>{migrations.slice(0, 6).map((job) => <article key={job.id} className={`storage-migration ${job.status}`}><div><strong>{locationLabel[job.kind]}</strong><span>{job.status === "succeeded" ? "Verified copy complete" : cleanLabel(job.status)}</span></div><code>{job.source} → {job.destination}</code><div className="migration-progress"><span style={{ width: `${Math.max(2, job.progress)}%` }} /></div><small>{job.message}{job.bytes_total ? ` · ${compactFileSize(job.bytes_copied)} of ${compactFileSize(job.bytes_total)}` : ""}</small>{job.error && <small className="error-text">{job.error}</small>}</article>)}</section>}
    {stats && <><div className="storage-grid">{Object.entries({ ...stats.locations, annotation_datasets: stats.annotation_bytes }).map(([key, value]) => <article key={key}><span>{cleanLabel(key)}</span><strong>{compactFileSize(value)}</strong></article>)}</div><div className="storage-detail"><span>{stats.current_datasets} current sample dataset{stats.current_datasets === 1 ? "" : "s"} in {stats.callsets} source dataset{stats.callsets === 1 ? "" : "s"} · {stats.versions} retained version{stats.versions === 1 ? "" : "s"} · {stats.managed_unique_files} managed VCF file{stats.managed_unique_files === 1 ? "" : "s"}</span><span>SQLite reclaimable space: <strong>{compactFileSize(stats.database_reclaimable_bytes)}</strong></span><code>{stats.state_dir}</code>{stats.workspace_dir !== stats.state_dir && <code>Temporary workspace: {stats.workspace_dir}</code>}</div></>}{message && <div className="alert">{message}</div>}{error && <div className="alert error">{error}</div>}<div className="storage-actions"><article><h2>Clean rebuildable files</h2><p>Removes old browser uploads, cohort preparation copies, WGS review cache, and partial files. Persistent managed VCFs are retained.</p><button className="secondary-button" disabled={working || Boolean(activeMigration)} onClick={() => void clean()}>Clean temporary data</button></article><article><h2>Return unused database space</h2><p>Removing cohort samples makes SQLite pages reusable but does not shrink the file. Compact only when substantial reclaimable space is shown.</p><button className="secondary-button" disabled={working || Boolean(activeMigration) || !stats?.database_reclaimable_bytes} onClick={() => void compact()}>Compact database</button></article></div></div>;
}

function BulkIntakePanel({ onLibraryChanged }: { onLibraryChanged: () => void }) {
  const [job, setJob] = useState<BulkIntakeJob | null>(null);
  const [pathsText, setPathsText] = useState("");
  const [scope, setScope] = useState<"exome" | "whole_genome">("whole_genome");
  const [popmax, setPopmax] = useState("0.01");
  const [includeInCohort, setIncludeInCohort] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const jobActive = job !== null && (job.status === "queued" || job.status === "running");
  const finishedCount = job ? job.succeeded + job.failed + job.skipped : 0;
  // Pick up a queue that is already running (including one resumed by the
  // service after a restart), and poll while it works.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const state = await getBulkIntake();
        if (!cancelled) setJob(state.job);
      } catch { /* service offline; the surrounding page reports that */ }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 4000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);
  const lastStatus = useRef<string | null>(null);
  useEffect(() => {
    if (job && lastStatus.current && lastStatus.current !== job.status &&
        (job.status === "completed" || job.status === "cancelled" || job.status === "failed")) onLibraryChanged();
    lastStatus.current = job?.status ?? null;
  }, [job, onLibraryChanged]);
  async function start() {
    const paths = pathsText.split("\n").map((line) => line.trim()).filter(Boolean);
    if (!paths.length) { setError("List at least one VCF file or folder."); return; }
    setStarting(true); setError("");
    try {
      // Number() rejects "0,01" outright instead of parseFloat's silent 0,
      // and an invalid value blocks the import rather than reverting to a
      // default the user never chose.
      const parsedPopmax = Number(popmax);
      if (!popmax.trim() || !Number.isFinite(parsedPopmax) || parsedPopmax < 0 || parsedPopmax > 1) {
        setError("gnomAD popmax: enter a number from 0 to 1 using a decimal point, for example 0.01.");
        return;
      }
      const popmaxValue = parsedPopmax;
      const filters = scope === "exome"
        ? { max_gnomad_popmax: popmaxValue, min_spliceai: null, min_promoterai_abs: null, noncoding_mode: "none" }
        : { max_gnomad_popmax: popmaxValue };
      const state = await startBulkIntake({ paths, analysis_scope: scope, filters, include_in_cohort: includeInCohort });
      setJob(state.job);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Bulk import could not start."); }
    finally { setStarting(false); }
  }
  async function cancel() {
    if (!job) return;
    if (!window.confirm("Cancel the bulk import? Files already imported stay in the library; remaining files are skipped.")) return;
    try { setJob((await cancelBulkIntake(job.id)).job); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The queue could not be cancelled."); }
  }
  return <details className="bulk-intake" open={jobActive}>
    <summary><span><strong>Bulk import</strong><small>Queue a folder of annotated VCFs — each file is candidate-filtered and added to the library one at a time; the queue survives restarts and resumes on its own.</small></span>{jobActive && <em>{finishedCount} of {job.total} done</em>}</summary>
    <div className="bulk-intake-body">
      {!jobActive && <>
        <label className="bulk-intake-paths"><span>VCF files or folders on this computer, one per line</span>
          <textarea value={pathsText} onChange={(event) => setPathsText(event.target.value)} rows={3} placeholder={"/data/annotated-genomes\n/data/batch2/case17.vep.vcf.gz"} spellCheck={false}/>
          <small>Folders are searched, subfolders included, for .vcf and .vcf.gz files. Each file must be a VEP-annotated single-sample VCF from this pipeline.</small>
        </label>
        <div className="bulk-intake-options">
          <label><span>Assay</span><select value={scope} onChange={(event) => setScope(event.target.value === "exome" ? "exome" : "whole_genome")}><option value="whole_genome">Whole genome</option><option value="exome">Exome</option></select></label>
          <label><span>gnomAD popmax ≤</span><input value={popmax} onChange={(event) => setPopmax(event.target.value)} inputMode="decimal"/></label>
          <label className="bulk-intake-check"><input type="checkbox" checked={includeInCohort} onChange={(event) => setIncludeInCohort(event.target.checked)}/><span>Add each sample to Cohort Search as it lands</span></label>
          <button className="primary-button dark" disabled={starting} onClick={() => void start()}>{starting ? "Starting…" : "Start bulk import"}</button>
        </div>
        <small className="bulk-intake-note">{scope === "whole_genome" ? "Whole-genome files pass through the same candidate reduction as a single import: PASS or unfiltered sites, the population-frequency ceiling above, and the coding / splice / promoter / regulatory-element routes." : "Exome files keep protein-coding candidates under the population-frequency ceiling above; no whole-genome routes are applied."}</small>
      </>}
      {job && (jobActive || job.failed > 0 || !lastStatusIsOld(job)) && <div className={`bulk-intake-status ${job.status}`}>
        <div className="bulk-intake-progress">
          <strong>{job.status === "completed" ? "Bulk import complete" : job.status === "cancelled" ? "Bulk import cancelled" : job.status === "failed" ? "Bulk import stopped on an error" : "Importing…"}</strong>
          <span>{job.succeeded} imported · {job.failed} failed · {job.skipped} skipped · {job.queued + job.running} remaining of {job.total}</span>
          {jobActive && job.current_path && <small className="mono" title={job.current_path}>Working on {fileName(job.current_path)}</small>}
          {jobActive && <small>Safe to close this page or the app — the queue resumes when the service restarts.</small>}
        </div>
        {job.failures.length > 0 && <details className="bulk-intake-failures"><summary>{job.failed} file{job.failed === 1 ? "" : "s"} failed — review before re-running</summary><ul>{job.failures.map((failure) => <li key={failure.path}><span className="mono" title={failure.path}>{fileName(failure.path)}</span><small>{failure.error}</small></li>)}</ul></details>}
        {jobActive && <button className="danger-text-button" onClick={() => void cancel()}>Cancel remaining files</button>}
      </div>}
      {error && <div className="alert error">{error}</div>}
    </div>
  </details>;
}

// A finished job stays visible for the session in which it ran; a stale
// completed job from a previous week should not reopen the panel.
function lastStatusIsOld(job: BulkIntakeJob): boolean {
  return Date.now() - Date.parse(job.updated_at) > 24 * 60 * 60 * 1000;
}

function SampleLibraryPanel({ onReview, onManagePhenotype }: { onReview: (rows: VariantRow[], summary: ImportSummary, scope: AnalysisScope) => void; onManagePhenotype: (individualId?: string | null) => void }) {
  const [datasets, setDatasets] = useState<SampleLibraryDataset[]>([]);
  const [query, setQuery] = useState("");
  const callsets = useMemo<LibraryCallsetGroup[]>(() => {
    const grouped = new Map<string, Map<string, SampleLibraryDataset[]>>();
    for (const dataset of datasets) {
      const callsetId = dataset.callset_id || dataset.managed_checksum;
      const versionId = dataset.version_id || dataset.managed_checksum;
      const versions = grouped.get(callsetId) ?? new Map<string, SampleLibraryDataset[]>();
      versions.set(versionId, [...(versions.get(versionId) ?? []), dataset]);
      grouped.set(callsetId, versions);
    }
    return [...grouped.entries()].map(([id, versionMap]) => {
      const versions = [...versionMap.entries()].map(([versionId, members]) => ({
        id: versionId,
        versionNumber: Math.max(...members.map((item) => item.version_number || 1)),
        current: members.some((item) => item.is_current),
        datasets: [...members].sort((left, right) => left.sample_label.localeCompare(right.sample_label)),
      })).sort((left, right) => right.versionNumber - left.versionNumber);
      return { id, versions, current: versions.find((version) => version.current) ?? null };
    }).sort((left, right) => {
      const leftDate = left.current?.datasets[0]?.imported_at ?? left.versions[0]?.datasets[0]?.imported_at ?? "";
      const rightDate = right.current?.datasets[0]?.imported_at ?? right.versions[0]?.datasets[0]?.imported_at ?? "";
      return rightDate.localeCompare(leftDate);
    });
  }, [datasets]);
  const activeDatasets = useMemo(
    () => callsets.flatMap((callset) => callset.current?.datasets ?? []),
    [callsets],
  );
  // Paginate source callsets, not individual sample rows. A jointly called
  // 88-sample VCF is one library card and expands locally when needed.
  const LIBRARY_PAGE_SIZE = 25;
  const [libraryPage, setLibraryPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(callsets.length / LIBRARY_PAGE_SIZE));
  const currentPage = Math.min(libraryPage, pageCount - 1);
  const pagedCallsets = callsets.slice(
    currentPage * LIBRARY_PAGE_SIZE, (currentPage + 1) * LIBRARY_PAGE_SIZE,
  );
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [selectedDatasets, setSelectedDatasets] = useState<Set<string>>(new Set());
  async function refresh(value = query) {
    try {
      const next = await getSampleLibrary(value);
      setDatasets(next);
      setLibraryPage(0);
      // A selection must never outlive its visibility: datasets selected
      // under one search and hidden by the next would otherwise still be
      // removed by "Remove selected" while invisible.
      const visible = new Set(next.map((dataset) => dataset.id));
      setSelectedDatasets((current) => new Set([...current].filter((id) => visible.has(id))));
      setError("");
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Sample Library is unavailable."); }
  }
  useEffect(() => { void refresh(""); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function openCombined(datasetIds: string[], label: string) {
    setWorking("combined"); setError("");
    setMessage(`Preparing a combined review for ${datasetIds.length} individuals…`);
    try {
      // One projection per managed source file: a within-file selection keeps
      // joint-callset semantics; selections spanning files are aggregated by
      // the parser as separately called (no carrier denominator).
      const chosen = datasets.filter((dataset) => datasetIds.includes(dataset.id));
      const groups = new Map<string, string[]>();
      for (const dataset of chosen) {
        const key = dataset.managed_checksum || dataset.id;
        groups.set(key, [...(groups.get(key) ?? []), dataset.id]);
      }
      const files: File[] = [];
      let index = 0;
      for (const ids of groups.values()) {
        index += 1;
        if (groups.size > 1) setMessage(`Preparing projection ${index} of ${groups.size}…`);
        files.push(await openSampleLibraryReviewSelection(ids, `combined-${index}.review.vcf.gz`));
      }
      const scopes = new Set(chosen.map((dataset) => dataset.analysis_scope));
      if (scopes.size > 1) {
        setError("The selection mixes exome and whole-genome datasets; open one assay type at a time so review settings match the data.");
        return;
      }
      const parsed = await parseVcfFiles(files, {
        intake: "prepared-review",
        clinicalTranscriptsOnly: true,
      });
      const identities = new Map(chosen.map((dataset) => [dataset.vcf_sample_name, dataset]));
      parsed.rows = parsed.rows.map((row) => attachLibraryIdentity(row, identities));
      parsed.summary.warnings.unshift(`Combined review · ${label}${groups.size > 1 ? ` · ${groups.size} source files` : ""}`);
      onReview(parsed.rows, parsed.summary, chosen[0]?.analysis_scope ?? "exome");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Combined review could not be opened."); }
    finally { setWorking(""); setMessage(""); }
  }

  async function openDataset(dataset: SampleLibraryDataset) {
    setWorking(dataset.id); setError(""); setMessage("Opening the managed review VCF…");
    try {
      const parsed = await parseVcfFiles(
        [await openSampleLibraryFile(dataset.id, dataset.original_name)],
        { intake: "prepared-review", clinicalTranscriptsOnly: true },
      );
      parsed.summary.warnings.unshift(`Reopened from Sample Library · ${dataset.profile_label}`);
      onReview(parsed.rows.map((row) => ({
        ...row,
        libraryDatasetId: dataset.id,
        librarySampleId: dataset.sample_id,
        libraryIndividualId: dataset.individual_id,
        cohortSampleEntryId: dataset.cohort_sample_entry_id ?? undefined,
      })), parsed.summary, dataset.analysis_scope);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Dataset could not be opened."); }
    finally { setWorking(""); setMessage(""); }
  }
  async function indexDataset(dataset: SampleLibraryDataset, action: "add" | "repair" | "rebuild" | "full") {
    const fullWgs = action === "full" || (dataset.analysis_scope === "whole_genome" && dataset.index_scope === "full");
    if (fullWgs && !window.confirm("Full WGS indexing stores every PASS carrier call from the original WGS VCF. A single genome can require roughly 10 GB in SQLite and take substantial time. Continue?")) return;
    const activeMessage = action === "add" ? "Adding this sample to Cohort Search…" : action === "repair" ? "Repairing this sample's Cohort Search entry…" : fullWgs ? "Building the advanced full-WGS cohort index…" : "Rebuilding this sample's Cohort Search entry…";
    const successMessage = action === "add" ? "Sample added to Cohort Search." : action === "repair" ? "Cohort Search entry repaired." : "Cohort Search index rebuilt.";
    setWorking(dataset.id); setError(""); setMessage(activeMessage);
    try { await reindexSampleLibraryDataset(dataset.id, fullWgs); await refresh(); setMessage(successMessage); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Dataset could not be indexed."); }
    finally { setWorking(""); }
  }
  async function removeFromCohort(dataset: SampleLibraryDataset) {
    if (!window.confirm(`Remove ${dataset.sample_label} from Cohort Search? It will remain available in the Sample Library.`)) return;
    setWorking(dataset.id); setError(""); setMessage("Removing this sample from Cohort Search…");
    try { await removeSampleLibraryDatasetFromCohort(dataset.id); await refresh(); setMessage("Sample removed from Cohort Search and retained in the Sample Library."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Cohort Search entry could not be removed."); }
    finally { setWorking(""); }
  }
  async function addSelectedToCohort() {
    const ids = [...selectedDatasets];
    if (!ids.length) return;
    setWorking("bulk-add"); setError("");
    try {
      const report = await bulkSampleLibraryAction(ids, "cohort_add");
      await refresh();
      if (report.failures.length) setError(`${report.failures.length} of ${report.requested} additions failed: ${report.failures[0].error}`);
      else setMessage(`${report.succeeded} added to Cohort Search${report.skipped ? `, ${report.skipped} already present` : ""}.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Bulk addition failed."); }
    setWorking("");
  }
  async function removeSelected() {
    const ids = [...selectedDatasets];
    if (!ids.length) return;
    if (!window.confirm(`Remove ${ids.length} dataset${ids.length > 1 ? "s" : ""} from the Sample Library and Cohort Search? Original source VCFs are not deleted.`)) return;
    setWorking("bulk-remove"); setError("");
    try {
      const report = await bulkSampleLibraryAction(ids, "remove");
      await refresh();
      setSelectedDatasets(new Set());
      if (report.failures.length) setError(`${report.failures.length} of ${report.requested} removals failed: ${report.failures[0].error}`);
      else setMessage(`${report.succeeded} dataset${report.succeeded === 1 ? "" : "s"} removed. Original source VCFs were not deleted.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Bulk removal failed."); }
    setWorking("");
  }
  async function remove(dataset: SampleLibraryDataset) {
    if (!window.confirm(`Remove ${dataset.sample_label} / ${dataset.original_name} from the Sample Library and Cohort Search? The original VCF is not deleted.`)) return;
    setWorking(dataset.id); setError("");
    try { await removeSampleLibraryDataset(dataset.id); await refresh(); setMessage("Dataset removed. The original source VCF was not deleted."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Dataset could not be removed."); }
    finally { setWorking(""); }
  }
  async function editMetadata(dataset: SampleLibraryDataset) {
    const label = window.prompt("Sample/specimen label", dataset.sample_label);
    if (label === null) return;
    try { await updateSampleLibraryMetadata(dataset.id, { sample_label: label }); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Metadata could not be updated."); }
  }
  async function restoreVersion(version: LibraryVersionGroup) {
    const representative = version.datasets[0];
    if (!representative) return;
    if (!window.confirm(`Restore version ${version.versionNumber} of ${representative.original_name}? It will become the active version for all ${version.datasets.length} sample${version.datasets.length === 1 ? "" : "s"}, and the current version will move to Previous versions.`)) return;
    setWorking(`restore:${version.id}`); setError(""); setMessage("Restoring the selected version and updating Cohort Search…");
    try {
      const result = await activateSampleLibraryVersion(representative.id);
      await refresh();
      if (result.warning) setError(result.warning);
      else setMessage(`Version ${version.versionNumber} restored as the current version.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The previous version could not be restored."); }
    finally { setWorking(""); }
  }
  function renderSample(dataset: SampleLibraryDataset, historical = false) {
    const cohortStatus = dataset.cohort_index_status ?? (dataset.include_in_cohort ? "needs_repair" : "not_included");
    return <article className={`library-card library-sample-card ${historical ? "historical" : ""}`} key={dataset.id}>
      <header>{!historical && <label className="library-select" title="Select for combined review"><input type="checkbox" checked={selectedDatasets.has(dataset.id)} onChange={(event) => setSelectedDatasets((current) => { const next = new Set(current); if (event.target.checked) next.add(dataset.id); else next.delete(dataset.id); return next; })}/></label>}<div><strong>{dataset.sample_label}</strong><span>{dataset.individual_id ? `Individual ${dataset.individual_id}` : "Phenotype not linked"} · VCF sample {dataset.vcf_sample_name}</span></div><div className="profile-badges"><span>{dataset.analysis_scope === "whole_genome" ? "WGS" : "WES/exome"}</span>{historical ? <span className="muted">Previous version</span> : <span className={cohortStatus === "needs_repair" ? "warning" : cohortStatus === "not_included" ? "muted" : ""}>{cohortStatus === "ready" ? "Included in Cohort Search" : cohortStatus === "needs_repair" ? "Cohort index needs repair" : "Library only"}</span>}</div></header>
      <footer><button className={historical ? "secondary-button" : "primary-button dark"} disabled={working === dataset.id} onClick={() => void openDataset(dataset)}>{working === dataset.id ? "Working…" : "Open review"}</button><button className="secondary-button" onClick={() => onManagePhenotype(dataset.individual_id)}>{dataset.individual_id ? "View phenotype" : "Add phenotype"}</button>{!historical && cohortStatus === "not_included" && <button className="secondary-button" disabled={working === dataset.id} onClick={() => void indexDataset(dataset, "add")}>Add to Cohort Search</button>}{!historical && cohortStatus === "needs_repair" && <button className="secondary-button repair-button" disabled={working === dataset.id} onClick={() => void indexDataset(dataset, "repair")}>Repair Cohort Search</button>}<details className="library-advanced"><summary>More actions</summary><div className="library-maintenance-actions"><button className="secondary-button" onClick={() => void editMetadata(dataset)}>Edit sample label</button>{!historical && cohortStatus === "ready" && <button className="secondary-button" disabled={working === dataset.id} onClick={() => void indexDataset(dataset, "rebuild")}>Rebuild search index</button>}{!historical && cohortStatus === "ready" && <button className="danger-text-button" disabled={working === dataset.id} onClick={() => void removeFromCohort(dataset)}>Remove from Cohort Search</button>}{!historical && dataset.analysis_scope === "whole_genome" && dataset.index_scope !== "full" && <><button className="danger-text-button" disabled={working === dataset.id} onClick={() => void indexDataset(dataset, "full")}>Build full WGS index</button><small>Indexes every PASS carrier call from the original VCF; not recommended for routine workstation use.</small></>}<details className="library-provenance"><summary>Provenance and technical details</summary><dl><div><dt>Source path</dt><dd><code>{dataset.original_path}</code></dd></div><div><dt>Managed review copy</dt><dd><code>{dataset.managed_path}</code>{dataset.managed_index_path && <code>{dataset.managed_index_path}</code>}</dd></div><div><dt>Settings hash</dt><dd><code>{dataset.settings_hash}</code></dd></div><div><dt>Managed file SHA-256</dt><dd><code>{dataset.managed_checksum}</code></dd></div></dl></details><button className="danger-text-button" disabled={working === dataset.id} onClick={() => void remove(dataset)}>Remove from Sample Library</button></div></details></footer>
    </article>;
  }
  return <div className="data-page library-page"><div className="data-page-header"><div><p className="eyebrow">Persistent source of truth</p><h1>Sample Library</h1><p>Reopen review sets, connect phenotype records, and rebuild the derived Cohort Search index.</p></div><div className="library-summary"><strong>{callsets.length}</strong><span>source dataset{callsets.length === 1 ? "" : "s"} · {activeDatasets.length} current sample{activeDatasets.length === 1 ? "" : "s"}</span></div></div>
    <div className="library-toolbar"><label className="search"><Icon name="search"/><input value={query} placeholder="Find sample, individual, or VCF…" onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void refresh(); }}/></label><button className="secondary-button" onClick={() => void refresh()}>Search</button></div>
    <BulkIntakePanel onLibraryChanged={() => void refresh()}/>
    <div className="profile-caveat"><strong>Positive carrier findings remain useful across profiles.</strong><span>Absence from a candidate index is not evidence that the individual lacks a variant. Compare assay and profile badges before interpreting coverage.</span></div>
    {message && <div className="alert">{message}</div>}{error && <div className="alert error">{error}</div>}
    {activeDatasets.length > 1 && <div className="library-combined-bar">
      <span>{selectedDatasets.size ? `${selectedDatasets.size} selected` : "Select individuals to review together or remove in bulk"}</span>
      <div>
        <button className="secondary-button" disabled={working !== ""} onClick={() => setSelectedDatasets(selectedDatasets.size === activeDatasets.length ? new Set() : new Set(activeDatasets.map((d) => d.id)))}>{selectedDatasets.size === activeDatasets.length ? "Clear selection" : "Select all current samples"}</button>
        <button className="secondary-button" disabled={selectedDatasets.size < 2 || working !== ""} onClick={() => void openCombined([...selectedDatasets], `${selectedDatasets.size} selected individuals`)}>Open combined review ({selectedDatasets.size || 0})</button>
        <button className="secondary-button" disabled={selectedDatasets.size === 0 || working !== ""} onClick={() => void addSelectedToCohort()}>{working === "bulk-add" ? "Adding…" : "Add to Cohort Search"}</button>
        <button className="secondary-button danger-button" disabled={selectedDatasets.size === 0 || working !== ""} onClick={() => void removeSelected()}>{working === "bulk-remove" ? "Removing…" : `Remove selected (${selectedDatasets.size || 0})`}</button>
      </div>
    </div>}
    {callsets.length > LIBRARY_PAGE_SIZE && <div className="library-pagination"><span>Showing {currentPage * LIBRARY_PAGE_SIZE + 1}–{Math.min((currentPage + 1) * LIBRARY_PAGE_SIZE, callsets.length)} of {callsets.length} source datasets</span><div><button className="secondary-button" disabled={currentPage === 0} onClick={() => setLibraryPage(currentPage - 1)}>Previous</button><span>Page {currentPage + 1} of {pageCount}</span><button className="secondary-button" disabled={currentPage >= pageCount - 1} onClick={() => setLibraryPage(currentPage + 1)}>Next</button></div></div>}
    <div className="library-list">{pagedCallsets.map((callset) => {
      const activeVersion = callset.current ?? callset.versions[0];
      const current = activeVersion?.datasets ?? [];
      const representative = current[0];
      if (!representative) return null;
      const previous = callset.versions.filter((version) => version.id !== activeVersion.id);
      const allSelected = current.length > 0 && current.every((dataset) => selectedDatasets.has(dataset.id));
      const ready = current.filter((dataset) => dataset.cohort_index_status === "ready").length;
      return <article className="library-callset-card" key={callset.id}>
        <header><label className="library-select" title="Select every current sample in this callset"><input type="checkbox" checked={allSelected} onChange={(event) => setSelectedDatasets((selected) => { const next = new Set(selected); current.forEach((dataset) => { if (event.target.checked) next.add(dataset.id); else next.delete(dataset.id); }); return next; })}/></label><div><strong>{representative.original_name}</strong><span>{current.length} sample{current.length === 1 ? "" : "s"} · version {activeVersion.versionNumber} current{previous.length ? ` · ${previous.length} previous version${previous.length === 1 ? "" : "s"}` : ""}</span></div><div className="profile-badges"><span>{representative.analysis_scope === "whole_genome" ? "WGS" : "WES/exome"}</span><span>{ready === current.length ? "Cohort Search current" : ready ? `${ready}/${current.length} in Cohort Search` : "Library only"}</span></div></header>
        <div className="library-callset-summary"><div><span>Import profile</span><strong>{representative.profile_label}</strong><small>{new Date(representative.imported_at).toLocaleString()}</small></div><div><span>Retained review file</span><strong>{compactFileSize(representative.managed_size_bytes)}</strong><small>Stored once for all {current.length} sample{current.length === 1 ? "" : "s"}</small></div><div className="library-callset-actions"><button className="primary-button dark" disabled={Boolean(working)} onClick={() => current.length === 1 ? void openDataset(representative) : void openCombined(current.map((dataset) => dataset.id), `${representative.original_name} · ${current.length} samples`)}>{current.length === 1 ? "Open review" : `Open all ${current.length} samples`}</button><button className="secondary-button" onClick={() => setSelectedDatasets((selected) => { const next = new Set(selected); current.forEach((dataset) => next.add(dataset.id)); return next; })}>Select samples</button></div></div>
        <details className="library-callset-samples" open={current.length <= 5}><summary>{current.length === 1 ? "Sample" : `${current.length} samples`}</summary><div className="library-callset-sample-list">{current.map((dataset) => renderSample(dataset))}</div></details>
        {previous.length > 0 && <details className="library-version-history"><summary>Previous versions ({previous.length})</summary><div>{previous.map((version) => { const item = version.datasets[0]; return <section key={version.id} className="library-version"><header><div><strong>Version {version.versionNumber}</strong><span>{item.original_name} · {version.datasets.length} sample{version.datasets.length === 1 ? "" : "s"} · {new Date(item.imported_at).toLocaleString()}</span></div><div><button className="secondary-button" disabled={Boolean(working)} onClick={() => version.datasets.length === 1 ? void openDataset(item) : void openCombined(version.datasets.map((dataset) => dataset.id), `${item.original_name} · version ${version.versionNumber}`)}>Open previous review</button><button className="secondary-button" disabled={Boolean(working)} onClick={() => void restoreVersion(version)}>{working === `restore:${version.id}` ? "Restoring…" : "Restore this version"}</button></div></header><details><summary>View {version.datasets.length} sample{version.datasets.length === 1 ? "" : "s"}</summary>{version.datasets.map((dataset) => renderSample(dataset, true))}</details></section>; })}</div></details>}
      </article>;
    })}</div>
    {!datasets.length && !error && <div className="empty-state"><span className="empty-icon"><Icon name="file"/></span><h2>No retained samples</h2><p>Import a VCF and keep the recommended Sample Library option enabled.</p></div>}
  </div>;
}

function DatasetVersionDialog({ inspection, onChoose }: {
  inspection: SampleLibraryInspection;
  onChoose: (choice: { action: "replace" | "separate"; callsetId?: string } | null) => void;
}) {
  const [callsetId, setCallsetId] = useState(inspection.matches[0]?.callset_id ?? "");
  const selected = inspection.matches.find((match) => match.callset_id === callsetId);
  return <div className="dialog-backdrop"><section className="identity-dialog version-dialog" role="dialog" aria-modal="true" aria-label="Resolve a possible dataset update">
    <p className="eyebrow">Possible dataset update</p>
    <h2>{inspection.source_name}</h2>
    <p>This VCF contains {inspection.sample_count.toLocaleString()} sample{inspection.sample_count === 1 ? "" : "s"} whose names overlap an existing dataset, but the called variants or genotypes are not identical. Confirm whether this is a rerun or a separate specimen/dataset.</p>
    <div className="version-match-list">{inspection.matches.map((match) => <label className={callsetId === match.callset_id ? "active" : ""} key={match.callset_id}>
      <input type="radio" checked={callsetId === match.callset_id} onChange={() => setCallsetId(match.callset_id)}/>
      <span><strong>{match.original_name}</strong><small>Version {match.version_number} · imported {new Date(match.imported_at).toLocaleString()} · {match.matching_sample_count} of {inspection.sample_count} sample names match{match.same_sample_set ? " (same sample set)" : ""}</small></span>
    </label>)}</div>
    <div className="version-decision-note"><strong>Recommended for an updated annotation or calling run</strong><span>Replace the current version. The existing version remains available under Previous versions and is removed from Cohort Search.</span></div>
    <footer><button className="secondary-button" onClick={() => onChoose(null)}>Cancel import</button><button className="secondary-button" onClick={() => onChoose({ action: "separate" })}>Keep as separate dataset</button><button className="primary-button dark" disabled={!selected} onClick={() => onChoose({ action: "replace", callsetId })}>Replace current version</button></footer>
  </section></div>;
}

function IdentityMappingDialog({ datasets, onComplete }: { datasets: PendingIdentity[]; onComplete: () => void }) {
  const [index, setIndex] = useState(0);
  const [mode, setMode] = useState<"existing" | "create" | "unavailable">("unavailable");
  const [individualId, setIndividualId] = useState("");
  const [individuals, setIndividuals] = useState<PhenotypeIndividual[]>([]);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const dataset = datasets[index];
  useEffect(() => { getPhenotypeIndividuals().then(setIndividuals).catch(() => setIndividuals([])); }, []);
  useEffect(() => {
    setIndividualId(dataset?.individual_id ?? "");
    setMode(dataset?.individual_id ? "existing" : "unavailable");
  }, [dataset]);
  if (!dataset) return null;
  async function save() {
    if (mode !== "unavailable" && !individualId.trim()) { setError("Enter or select an individual ID."); return; }
    setWorking(true); setError("");
    try {
      await mapSampleLibraryIdentity(dataset.id, { mode, individual_id: mode === "unavailable" ? undefined : individualId.trim() });
      if (index + 1 < datasets.length) setIndex(index + 1); else onComplete();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Identity mapping could not be saved."); }
    finally { setWorking(false); }
  }
  return <div className="dialog-backdrop"><section className="identity-dialog" role="dialog" aria-modal="true" aria-label="Link sample to an individual"><p className="eyebrow">Sample Library · {index + 1} of {datasets.length}</p><h2>Who does {dataset.vcf_sample_name} belong to?</h2><p>Phenotype and demographics belong to an individual. This VCF import remains a separate genomic dataset.</p><div className="identity-options">
    <label className={mode === "existing" ? "active" : ""}><input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")}/><span><strong>Link existing individual</strong><small>Use an existing phenotype record.</small></span></label>
    {mode === "existing" && <select value={individualId} onChange={(event) => setIndividualId(event.target.value)}><option value="">Select individual…</option>{individuals.map((item) => <option key={item.individual_id} value={item.individual_id}>{item.individual_id}</option>)}</select>}
    <label className={mode === "create" ? "active" : ""}><input type="radio" checked={mode === "create"} onChange={() => setMode("create")}/><span><strong>Create individual</strong><small>Create an ID now; phenotype can be entered later.</small></span></label>
    {mode === "create" && <input value={individualId} placeholder="Individual ID" onChange={(event) => setIndividualId(event.target.value)}/>}
    <label className={mode === "unavailable" ? "active" : ""}><input type="radio" checked={mode === "unavailable"} onChange={() => setMode("unavailable")}/><span><strong>Phenotype unavailable for now</strong><small>The dataset stays in the library and can be linked later.</small></span></label>
  </div>{error && <div className="alert error">{error}</div>}<footer><button className="secondary-button" onClick={onComplete}>Finish later</button><button className="primary-button dark" disabled={working} onClick={save}>{working ? "Saving…" : index + 1 < datasets.length ? "Save and continue" : "Finish"}</button></footer></section></div>;
}

function ImportPanel({ importing, importProgress, wgsImportJob, error, summary, pendingFiles, onStageFiles, onImportFiles, qcSettings, setQcSettings, qcPreset, setQcPreset, includeQcFailing, setIncludeQcFailing }: { importing: boolean; importProgress: string; wgsImportJob: WgsReviewJob | null; error: string; summary: ImportSummary | null; pendingFiles: File[]; onStageFiles: (files: File[]) => void; onImportFiles: (files: File[], analysisScope: AnalysisScope, filters: WgsPrefilterOptions, workstationPaths: string[], libraryOptions: LibraryImportOptions, exomeCohortPopmax?: number | null) => void; qcSettings: VariantQcSettings; setQcSettings: React.Dispatch<React.SetStateAction<VariantQcSettings>>; qcPreset: "standard" | "none" | "custom"; setQcPreset: (value: "standard" | "none" | "custom") => void; includeQcFailing: boolean; setIncludeQcFailing: (value: boolean) => void }) {
  const [mode, setMode] = useState<"review" | "annotate">(
    pendingFiles.length ? "review" : "annotate",
  );
  const [analysisScope, setAnalysisScope] = useState<AnalysisScope>("exome");
  const [exomeCohortPopmax, setExomeCohortPopmax] = useState<number | null>(0.01);
  const [wgsFilters, setWgsFilters] = useState<WgsPrefilterOptions>({ ...DEFAULT_WGS_PREFILTER });
  const [wgsWorkstationPaths, setWgsWorkstationPaths] = useState("");
  const [keepInLibrary, setKeepInLibrary] = useState(true);
  const [includeInCohort, setIncludeInCohort] = useState(true);
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
      <p>Patient VCF, phenotype, and analysis data are processed locally. Optional online downloads and lookups occur only after your action and identify what will be requested.</p>
      <div className="analysis-scope-switch" role="radiogroup" aria-label="Analysis region">
        <button role="radio" aria-checked={analysisScope === "exome"} className={analysisScope === "exome" ? "active" : ""} onClick={() => setAnalysisScope("exome")}><strong>Exome region only</strong><span>Coding exons and splice-region padding</span></button>
        <button role="radio" aria-checked={analysisScope === "whole_genome"} className={analysisScope === "whole_genome" ? "active" : ""} onClick={() => setAnalysisScope("whole_genome")}><strong>Whole genome</strong><span>Local indexed intake with candidate prefiltering</span></button>
      </div>
      <div className="intake-mode-switch" role="tablist" aria-label="VCF intake route">
        <button role="tab" aria-selected={mode === "annotate"} className={mode === "annotate" ? "active" : ""} onClick={() => setMode("annotate")}><strong>Annotate VCF</strong><span>Start with a raw or hard-filtered VCF</span></button>
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
      {analysisScope === "exome" && <section className="wgs-prefilter-panel"><div className="settings-section-head"><div><h3>Cohort exome candidate import</h3><p>A file with 16+ samples is prepared on the local service before browser review: PASS-or-unfiltered records in coding regions, filtered by this population-frequency threshold. Single-patient and family files are read directly and are not affected.</p></div><span className="readiness ready">16+ samples only</span></div><div className="qc-field-grid"><QcNumberField label="gnomAD popmax ≤" value={exomeCohortPopmax} step="0.005" onChange={setExomeCohortPopmax}/></div><p className="wgs-filter-logic"><strong>Logic:</strong> PASS-or-unfiltered/QC AND (popmax ≤ threshold OR popmax unavailable) AND exome region. Clear the field to keep common variants.</p></section>}
      {analysisScope === "whole_genome" && <section className="wgs-prefilter-panel"><div className="settings-section-head"><div><h3>Whole-genome candidate import</h3><p>The local service prepares a candidate subset before browser review.</p></div><span className="readiness ready">cCRE default</span></div><div className="qc-field-grid">
        <QcNumberField label="gnomAD popmax ≤" value={wgsFilters.max_gnomad_popmax} step="0.001" onChange={(value) => setWgsFilters((current) => ({ ...current, max_gnomad_popmax: value }))}/>
        <QcNumberField label="SpliceAI ≥" value={wgsFilters.min_spliceai} step="0.05" onChange={(value) => setWgsFilters((current) => ({ ...current, min_spliceai: value }))}/>
        <QcNumberField label="|promoterAI| ≥" value={wgsFilters.min_promoterai_abs} step="0.05" onChange={(value) => setWgsFilters((current) => ({ ...current, min_promoterai_abs: value }))}/>
      </div><NoncodingModePicker value={wgsFilters.noncoding_mode} onChange={(noncoding_mode) => setWgsFilters((current) => ({ ...current, noncoding_mode }))}/><details className="advanced-paths wgs-local-paths"><summary>Recommended for very large files: use existing workstation paths</summary><label className="form-field"><span>Annotated WGS VCF path(s), one per line</span><textarea rows={3} value={wgsWorkstationPaths} onChange={(event) => setWgsWorkstationPaths(event.target.value)} placeholder={"/absolute/path/case.annotated.vcf.gz"}/><small>A direct path avoids copying a multi-gigabyte browser-selected file into local staging.</small></label></details><details className="advanced-paths wgs-technical-details"><summary>Technical details</summary><p>The local service creates or reuses BGZF/tabix indexes and filters chromosome shards with four readers before browser review.</p><p className="wgs-filter-logic"><strong>Logic:</strong> PASS-or-unfiltered/QC AND (popmax ≤ threshold OR popmax unavailable) AND (exonic/essential-splice OR SpliceAI ≥ threshold OR |promoterAI| ≥ threshold OR selected noncoding regions OR flagged unscored intronic/promoter indel). The indel exception applies only when the relevant precomputed score is unavailable, not when a populated score is below threshold.</p></details></section>}
      <div className="plain-defaults"><span>PASS or unfiltered (.) records</span><span>MANE + clinical transcript fallback</span><span>Repeat/SegDup excluded by default</span></div>
      <QcSettingsPanel settings={qcSettings} setSettings={setQcSettings} preset={qcPreset} setPreset={setQcPreset} includeFailing={includeQcFailing} setIncludeFailing={setIncludeQcFailing} />
      <section className="library-intake-options"><div className="settings-section-head"><div><p className="eyebrow">Import destination</p><h3>Keep this sample available</h3><p>The Sample Library is persistent; Cohort Search is a rebuildable genotype index.</p></div><span className="readiness ready">Recommended</span></div>
        <div className="library-retention-choice" role="radiogroup" aria-label="Import retention">
          <label className={keepInLibrary ? "active" : ""}><input type="radio" name="library-retention" checked={keepInLibrary} onChange={() => { setKeepInLibrary(true); setIncludeInCohort(true); }}/><span><strong>Keep in Sample Library</strong><small>Save the managed review VCF and provenance on this workstation before opening it.</small></span></label>
          <label className={!keepInLibrary ? "active" : ""}><input type="radio" name="library-retention" checked={!keepInLibrary} onChange={() => { setKeepInLibrary(false); setIncludeInCohort(false); }}/><span><strong>Review once</strong><small>Open now without retaining a managed copy or cohort entry.</small></span></label>
        </div>
        <Check label="Include qualifying variants in Cohort Search" checked={keepInLibrary && includeInCohort} onChange={setIncludeInCohort} note={keepInLibrary ? "On by default; can be rebuilt from the library later" : "Available only for retained samples"}/>
      </section>
      {error && <div className="alert error">{error}</div>}
      {analysisScope === "whole_genome" && importing && <div className={`cohort-import-progress wgs-import-progress ${wgsImportJob?.status === "failed" ? "failed" : ""}`}><div><strong>{wgsImportJob?.message || importProgress || "Preparing whole-genome input…"}</strong><span>{wgsPercent.toFixed(1)}%</span></div><div className="progress-track" role="progressbar" aria-label="Whole-genome indexing and prefiltering progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(wgsPercent)}><span style={{ width: `${wgsPercent}%` }} /></div><small>{wgsImportJob ? `${wgsImportJob.records_scanned.toLocaleString()} records scanned · ${wgsImportJob.records_retained.toLocaleString()} retained · ${wgsImportJob.reader_count} reader${wgsImportJob.reader_count === 1 ? "" : "s"}` : "Staging the selected WGS VCF on this workstation"}</small></div>}
      <div className="review-import-actions"><span>{importing && importProgress ? importProgress : pendingFiles.length || activeWgsPaths.length ? analysisScope === "whole_genome" ? "The indexed WGS prefilter runs locally before browser review." : "The selected files will be read using the thresholds above." : "Select one or more annotated VCFs to continue."}</span><button className="primary-button dark" disabled={importing || (pendingFiles.length === 0 && activeWgsPaths.length === 0)} onClick={() => onImportFiles(pendingFiles, analysisScope, submittedWgsFilters, activeWgsPaths, { keep: keepInLibrary, includeInCohort: keepInLibrary && includeInCohort }, exomeCohortPopmax)}>{importing ? analysisScope === "whole_genome" ? "Preparing WGS…" : "Importing annotations…" : "Import and review variants"}</button></div>
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

function annotationSourceIsLocked(source: AnnotationSource) {
  return source.required || source.setup_mode === "bundled" || source.id === "dbnsfp";
}

function annotationSourceIsEnabled(
  source: AnnotationSource,
  analysisScope: AnalysisScope,
  selections: Record<string, boolean>,
) {
  const supported = (source.available_in ?? ["exome", "whole_genome"]).includes(analysisScope);
  if (!supported) return false;
  if (annotationSourceIsLocked(source)) return source.installed;
  if (selections[source.id] !== undefined) return source.available && selections[source.id];
  if (!source.available) return false;
  return source.installed || source.enabled;
}

function resourceProgressMessage(
  job: ResourceDownloadJob | undefined,
  fallback: string,
) {
  return job?.message?.trim() || fallback;
}

const OMIM_FILE_NAMES = ["mim2gene.txt", "mimTitles.txt", "genemap2.txt", "morbidmap.txt"] as const;

const GENIA_COMPONENTS: {
  id: GeniaComponentId;
  label: string;
  capability: string;
}[] = [
  { id: "gei_disease", label: "GEI gene–disease list", capability: "Curated-status gene–disease evidence" },
  { id: "disease_catalog", label: "GenIA disease catalog", capability: "Disease identifiers and cross-references" },
  { id: "disease_phenotypes", label: "Disease–phenotype associations", capability: "Gene-linked phenotype frequencies" },
  { id: "phenotype_vocabulary", label: "GenIA phenotype vocabulary", capability: "Phenotype descriptions and hierarchy" },
  { id: "variant_vcf", label: "GenIA GRCh38 variants", capability: "Exact-allele variant evidence" },
];

function DatasetSetupCard({
  source,
  analysisScope,
  enabled,
  onEnabled,
  downloadJob,
  onDownload,
  preparationPath,
  onPreparationPath,
  choosingPreparationPath,
  onChoosePreparationPath,
  onPrepare,
  licenseAccepted = false,
  onLicenseAccepted,
}: {
  source: AnnotationSource;
  analysisScope: AnalysisScope;
  enabled: boolean;
  onEnabled: (value: boolean) => void;
  downloadJob?: ResourceDownloadJob;
  onDownload: (resourceId: ResourceDownloadJob["resource_id"]) => void;
  preparationPath: string;
  onPreparationPath: (value: string) => void;
  choosingPreparationPath: boolean;
  onChoosePreparationPath: () => void;
  onPrepare: () => void;
  licenseAccepted?: boolean;
  onLicenseAccepted?: (value: boolean) => void;
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
    : source.setup_mode === "bundled" ? (source.download_id ? "Not installed — one click below restores it" : "Rebuilt automatically with each ClinVar download")
    : source.setup_mode === "deferred" ? "Not configured in this release"
    : source.required ? "Action needed" : "Optional · not installed";
  const lockedInRun = annotationSourceIsLocked(source);
  const canToggle = supported && !lockedInRun && source.available && source.setup_mode !== "deferred";
  const selectionLabel = !supported
    ? analysisScope === "exome" ? "Not used for exome" : "Not used for whole genome"
    : !source.available
    ? source.setup_mode === "deferred" ? "Unavailable in this release" : "Install to enable"
    : lockedInRun
    ? "Included in every run"
    : enabled ? "On" : "Off";
  const downloadId = source.download_id as ResourceDownloadJob["resource_id"] | undefined;
  const buttonLabel = source.id === "clinvar"
    ? "Download latest"
    : source.id === "clingen_erepo" ? source.installed ? "Check and update snapshot" : "Install latest snapshot"
    : source.setup_mode === "bundled" ? "Download bundled files"
    : source.id === "cadd_wgs" ? source.installed ? "Verify 83 GiB files" : "Download / resume 83 GiB"
    : source.id === "logofunc" ? source.installed ? "Re-download from Zenodo" : "Download from Zenodo"
    : source.installed ? "Verify files" : "Download / resume";
  const preparationLabel = source.prepare_id === "logofunc"
    ? "Choose the downloaded LoGoFunc file"
    : source.prepare_id === "funcvep"
    ? "Optional: use an official ZIP already on this computer"
    : "Choose the licensed PromoterAI source folder";
  const chosenSourceName = preparationPath
    ? preparationPath.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "Selected source"
    : "";
  const preparationButton = source.prepare_id === "logofunc"
    ? "Import LoGoFunc"
    : source.prepare_id === "funcvep"
    ? preparationPath
      ? source.installed ? "Prepare selected ZIP and replace FuncVEP" : "Prepare selected ZIP and install FuncVEP"
      : source.installed ? "Download and replace FuncVEP" : "Download and install FuncVEP"
    : source.installed ? "Prepare and replace" : "Prepare and install";
  const preparationHelp = source.prepare_id === "logofunc"
    ? "The checksum-verified 3.66 GB table and index are moved into Annotation datasets storage; they are removed from the selected download location."
    : source.prepare_id === "funcvep"
    ? preparationPath
      ? "GUIDE-IEI verifies the selected official archive and prepares only FuncVEP_CTI, FuncVEP_CTE, and FuncVEP_SP locally. Your ZIP remains exactly where you selected it."
      : "GUIDE-IEI resumably downloads the pinned 4.24 GB ZIP from official Zenodo storage, verifies its published checksum, and prepares only FuncVEP_CTI, FuncVEP_CTE, and FuncVEP_SP. The source ZIP is retained in Annotation datasets storage."
    : "Prepared files are written to Annotation datasets storage. After successful installation, the two large licensed source files are removed from the selected folder. Nothing is uploaded.";
  return <article className={`dataset-card ${source.installed ? "installed" : "missing"} ${source.setup_mode} ${supported ? "" : "profile-unavailable"} ${supported && enabled ? "selected" : ""}`}>
    <div className="dataset-card-top">
      <div className="dataset-card-title"><strong>{source.label}{source.id !== "dbnsfp" && !["funcvep", "logofunc", "clingen_erepo"].includes(source.id) && source.version ? ` ${source.version}` : ""}</strong><small><GlossaryText text={source.description} /></small></div>
      <span className={`dataset-status ${activeDownload ? "working" : source.installed ? "ready" : "missing"}`}>{status}</span>
    </div>
    <div className={`dataset-use-control ${lockedInRun ? "locked" : "optional"} ${supported && enabled ? "enabled" : "disabled"}`}>
      {lockedInRun ? <><span className="dataset-use-icon" aria-hidden="true">{supported && enabled ? "✓" : "○"}</span><strong>{selectionLabel}</strong></> : <label className={canToggle ? "dataset-use-toggle" : "dataset-use-toggle disabled"}><span>Use in this run</span><input type="checkbox" checked={supported && enabled} disabled={!canToggle} onChange={(event) => onEnabled(event.target.checked)}/><span className="dataset-toggle-track" aria-hidden="true"><span/></span><strong>{selectionLabel}</strong></label>}
    </div>
    <div className="dataset-meta"><span>{setupLabels[source.setup_mode]}</span>{source.id === "clingen_erepo" && source.version && <span>Updated {source.version}</span>}{source.access === "registration" && <span>Registration required</span>}{source.access === "license" && <span>License required</span>}{source.access === "terms" && <span>Usage terms apply</span>}{source.recommendation === "optional" && <span>Optional</span>}{source.size_hint && <span>{source.size_hint}</span>}</div>
    {activeDownload && <div className="resource-progress"><progress max={100} value={downloadJob?.progress ?? undefined}/><span role="status" aria-live="polite">{resourceProgressMessage(downloadJob, downloadJob?.operation === "preparation" ? "Preparing local dataset…" : "Starting dataset download…")}</span></div>}
    {downloadJob?.status === "failed" && <div className="resource-download-error"><strong>{downloadJob.error || downloadJob.message}</strong><details><summary>Download log</summary><pre>{downloadJob.log || "No log output was captured."}</pre></details></div>}
    {source.prepare_id === "dbnsfp" && <div className="dataset-preparation"><label className="dataset-download-link"><span>Paste the private dbNSFP GRCh38 (.gz) download link</span><input type="url" value={preparationPath} autoComplete="off" spellCheck={false} disabled={activeDownload} placeholder="https://…/dbNSFP…_grch38.gz" onChange={(event) => onPreparationPath(event.target.value)}/></label><button type="button" disabled={activeDownload || !preparationPath.trim()} onClick={onPrepare}>{activeDownload ? "Downloading and installing…" : source.installed ? "Download and replace dbNSFP" : "Download and install dbNSFP"}</button><small>The filename must end in _grch38.gz — not _grch37.gz. GUIDE-IEI accepts any dbNSFP release version and Outlook Safe Links, derives the .tbi and .md5 links, resumes interrupted transfers with eight connections, and keeps the private link out of logs and configuration.</small></div>}
    {source.prepare_id && source.prepare_id !== "dbnsfp" && <div className="dataset-preparation">
      {source.prepare_id === "funcvep" && <div className="dataset-license-panel">
        <strong>License and download</strong>
        <p>The FuncVEP project states that it uses the PolyForm Strict 1.0.0 license. GUIDE-IEI does not include or redistribute the archive. Review the upstream terms before allowing GUIDE-IEI to obtain the ZIP from the official Zenodo record.</p>
        <div className="dataset-license-actions">
          <a href="https://polyformproject.org/licenses/strict/1.0.0" target="_blank" rel="noreferrer">Read upstream license ↗</a>
          <a href="https://zenodo.org/records/20595206" target="_blank" rel="noreferrer">Open official download page ↗</a>
        </div>
        <label className="check-row"><input type="checkbox" checked={licenseAccepted} disabled={activeDownload} onChange={(event) => onLicenseAccepted?.(event.target.checked)}/><span className="custom-check"/><span>I have reviewed the upstream terms and confirm that my intended use of FuncVEP is permitted.</span></label>
      </div>}
      {source.prepare_id === "funcvep" && <small className="dataset-local-processing">After acknowledgement, GUIDE-IEI can download the archive directly and process it on this computer. You can alternatively choose an existing ZIP below.</small>}
      <span className="dataset-preparation-label">{preparationLabel}</span>
      <div className="dataset-source-picker"><button type="button" disabled={activeDownload || choosingPreparationPath} onClick={onChoosePreparationPath}>{choosingPreparationPath ? "Opening chooser…" : preparationPath ? "Choose another" : source.prepare_id === "promoterai" ? "Choose folder" : "Choose file"}</button>{chosenSourceName ? <span title={preparationPath}><strong>{chosenSourceName}</strong><small>Selected from this computer</small></span> : source.prepare_id === "funcvep" ? <span><strong>Automatic Zenodo download</strong><small>No local ZIP needs to be selected</small></span> : <span><strong>No source selected</strong><small>Download it anywhere, then select it here</small></span>}</div>
      {source.prepare_id === "funcvep" && preparationPath && <button className="dataset-source-clear" type="button" disabled={activeDownload} onClick={() => onPreparationPath("")}>Use automatic Zenodo download instead</button>}
      <button type="button" disabled={activeDownload || choosingPreparationPath || (source.prepare_id !== "funcvep" && !preparationPath.trim()) || (source.prepare_id === "funcvep" && !licenseAccepted)} onClick={onPrepare}>{activeDownload ? source.prepare_id === "funcvep" && !preparationPath ? "Downloading and preparing…" : "Preparing…" : preparationButton}</button>
      <small>{preparationHelp}</small>
    </div>}
    <div className="dataset-actions">
      {downloadId && (source.setup_mode !== "bundled" || !source.installed) && <button type="button" disabled={activeDownload} onClick={() => onDownload(downloadId)}>{activeDownload ? "Downloading…" : buttonLabel}</button>}
      {source.reference_url && <a href={source.reference_url} target="_blank" rel="noreferrer">{source.reference_label || "Official reference"} ↗</a>}
      {source.id === "alphagenome_avi" && <a href="https://deepmind.google.com/science/alphagenome/downloads" target="_blank" rel="noreferrer">Official downloads and terms ↗</a>}
    </div>
    <details className="dataset-instructions"><summary>Dataset details</summary>{source.access === "registration" && <strong className="dataset-detail-heading">Registration and setup instructions</strong>}{source.access === "license" && <strong className="dataset-detail-heading">License and local setup instructions</strong>}<ol>{(source.instructions ?? []).map((instruction) => <li key={instruction}><GlossaryText text={instruction} /></li>)}</ol>{downloadJob && <div className="dataset-technical-status"><span>Technical status</span><code>{downloadJob.status}</code>{downloadJob.message && <p>{downloadJob.message}</p>}</div>}{(source.configured_paths?.length ?? 0) > 0 && <div className="configured-locations"><span>Configured location{source.configured_paths?.length === 1 ? "" : "s"}</span>{source.configured_paths?.map((path) => <code key={path}>{path}</code>)}</div>}</details>
  </article>;
}

function OmimDatasetSetupCard({
  downloadJob,
  onJobStarted,
}: {
  downloadJob?: ResourceDownloadJob;
  onJobStarted: (job: ResourceDownloadJob) => void;
}) {
  const [status, setStatus] = useState<GeneKnowledgeStatus["omim"] | null>(null);
  const [linkBlock, setLinkBlock] = useState("");
  const [sourceDir, setSourceDir] = useState("");
  const [working, setWorking] = useState<"" | "download" | "choose" | "folder">("");
  const [message, setMessage] = useState("");
  const active = downloadJob?.status === "queued" || downloadJob?.status === "running";
  useEffect(() => {
    let mounted = true;
    getGeneKnowledgeStatus()
      .then((next) => {
        if (!mounted) return;
        setStatus(next.omim);
        if (downloadJob?.status === "succeeded") {
          const counts = downloadJob.result?.counts;
          setMessage(counts
            ? `Installed ${counts.genes.toLocaleString()} OMIM genes and ${counts.phenotypes.toLocaleString()} phenotype associations.`
            : "OMIM download and installation complete.");
          window.dispatchEvent(new Event("gene-knowledge-updated"));
        }
      })
      .catch((error: unknown) => { if (mounted) setMessage(error instanceof Error ? error.message : "OMIM status unavailable"); });
    return () => { mounted = false; };
  }, [downloadJob?.id, downloadJob?.status]);
  const pastedFileNames = OMIM_FILE_NAMES.filter((filename) =>
    linkBlock.toLowerCase().includes(filename.toLowerCase()),
  );
  const recognizedFileNames = linkBlock.trim() ? pastedFileNames : downloadJob?.files ?? [];
  async function downloadAndInstall() {
    setWorking("download"); setMessage("");
    try {
      const job = await startOmimGeneKnowledgeDownload(linkBlock);
      onJobStarted(job);
      setLinkBlock("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not start the private OMIM download");
    } finally { setWorking(""); }
  }
  async function chooseFolder() {
    setWorking("choose"); setMessage("");
    try {
      const selection = await chooseLocalResourceSource("omim");
      if (!selection.cancelled && selection.path) setSourceDir(selection.path);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not open the OMIM folder chooser");
    } finally { setWorking(""); }
  }
  async function installFolder() {
    setWorking("folder"); setMessage("");
    try {
      const installed = await installOmimGeneKnowledge(sourceDir);
      setStatus((current) => ({
        installed: true,
        installed_at: new Date().toISOString(),
        genes: installed.counts.genes,
        phenotypes: installed.counts.phenotypes,
        license: current?.license ?? "User-provided OMIM data",
      }));
      setMessage(`Installed ${installed.counts.genes.toLocaleString()} OMIM genes and ${installed.counts.phenotypes.toLocaleString()} phenotype associations.`);
      window.dispatchEvent(new Event("gene-knowledge-updated"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "OMIM installation failed");
    } finally { setWorking(""); }
  }
  return <article className={`dataset-card omim-dataset-card ${status?.installed ? "installed" : "missing"}`}>
    <div className="dataset-card-top"><div className="dataset-card-title"><strong>OMIM</strong><small>Optional licensed gene–disease knowledge, indexed locally for review and gene filtering.</small></div><span className={`dataset-status ${active ? "working" : status?.installed ? "ready" : "missing"}`}>{active ? "Installing" : status?.installed ? "Installed" : "Optional · not installed"}</span></div>
    <div className="dataset-meta"><span>License required</span><span>User-provided links</span><span>Gene-level resource</span></div>
    <div className="dataset-preparation omim-dataset-preparation"><label htmlFor="omim-dataset-link-block"><span>Paste the complete OMIM email or four-link block</span><textarea id="omim-dataset-link-block" value={linkBlock} disabled={Boolean(working) || active} autoComplete="off" spellCheck={false} placeholder="Paste the OMIM data-account email or its four file links here…" onChange={(event) => setLinkBlock(event.target.value)}/></label><div className="omim-file-checklist">{OMIM_FILE_NAMES.map((filename) => { const found = recognizedFileNames.includes(filename); return <span className={found ? "found" : "missing"} key={filename}>{found ? "✓" : "○"} {filename}</span>; })}</div><button type="button" disabled={Boolean(working) || active || !linkBlock.trim()} onClick={() => void downloadAndInstall()}>{working === "download" || active ? "Downloading and installing…" : status?.installed ? "Download and replace OMIM" : "Download and install OMIM"}</button><small>Direct OMIM links and Outlook Safe Links are accepted. The private links stay memory-only; temporary raw files are removed after local validation and indexing.</small><details className="omim-folder-fallback"><summary>Already downloaded the four files?</summary><div className="dataset-source-picker"><button type="button" disabled={Boolean(working) || active} onClick={() => void chooseFolder()}>{working === "choose" ? "Opening chooser…" : sourceDir ? "Choose another folder" : "Choose downloaded folder"}</button><span><strong>{sourceDir ? sourceDir.replace(/[\\/]+$/, "").split(/[\\/]/).pop() : "No folder selected"}</strong><small>{sourceDir ? "Selected from this computer" : "Folder containing all four OMIM .txt files"}</small></span></div><button type="button" disabled={Boolean(working) || active || !sourceDir.trim()} onClick={() => void installFolder()}>{working === "folder" ? "Validating and indexing…" : "Install from downloaded files"}</button></details></div>
    {active && <div className="resource-progress"><progress max={100} value={downloadJob?.progress ?? undefined}/><span role="status" aria-live="polite">{downloadJob?.message || "Starting private OMIM installation…"}</span></div>}
    {downloadJob?.status === "failed" && <div className="resource-download-error"><strong>{downloadJob.error || downloadJob.message}</strong><details><summary>Installation log</summary><pre>{downloadJob.log || "No log output was captured."}</pre></details></div>}
    {message && <div className="alert">{message}</div>}
    {status?.installed && <p className="constraint-note">{status.genes?.toLocaleString()} genes · {status.phenotypes?.toLocaleString()} phenotype associations</p>}
    <div className="dataset-actions"><a href="https://omim.org/" target="_blank" rel="noreferrer">OMIM website ↗</a></div>
  </article>;
}

type SelectedGeniaFile = {
  role: GeniaComponentId;
  label: string;
  name: string;
  path: string;
};

function compactInstalledDate(value: string | undefined) {
  if (!value) return "date unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function GeniaDatasetSetupCard({
  source,
  analysisScope,
  enabled,
  onEnabled,
  onInstalled,
}: {
  source: AnnotationSource;
  analysisScope: AnalysisScope;
  enabled: boolean;
  onEnabled: (value: boolean) => void;
  onInstalled: () => void;
}) {
  const [status, setStatus] = useState<GeneKnowledgeStatus["genia"] | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [selected, setSelected] = useState<SelectedGeniaFile[]>([]);
  const [replaceUnreadable, setReplaceUnreadable] = useState(false);
  const [working, setWorking] = useState<"" | "choose" | "install">("");
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const supported = (source.available_in ?? ["exome", "whole_genome"]).includes(analysisScope);
  const installedComponents = status ? Object.keys(status.components).length : 0;
  const selectedByRole = new Map(selected.map((item) => [item.role, item]));

  useEffect(() => {
    let mounted = true;
    getGeneKnowledgeStatus()
      .then((next) => { if (mounted) setStatus(next.genia); })
      .catch((error: unknown) => {
        if (mounted) setMessage(error instanceof Error ? error.message : "GenIA status unavailable");
      })
      .finally(() => { if (mounted) setStatusLoading(false); });
    return () => { mounted = false; };
  }, []);

  async function chooseFiles() {
    setWorking("choose");
    setMessage("");
    setWarnings([]);
    try {
      const selection = await chooseLocalResourceSource("genia");
      if (selection.cancelled) return;
      const paths = selection.paths ?? [];
      const detected = selection.detected ?? [];
      if (!paths.length || !detected.length) throw new Error("No recognized GenIA export was selected.");
      setSelected(detected.map((item) => ({
        role: item.role,
        label: item.label,
        name: item.name,
        path: item.path,
      })));
      setReplaceUnreadable(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not inspect the selected GenIA files");
    } finally {
      setWorking("");
    }
  }

  async function installFiles() {
    setWorking("install");
    setMessage("");
    setWarnings([]);
    try {
      const replacingUnreadableIndex = Boolean(status?.error && replaceUnreadable);
      const installed = await installGeniaFiles(
        selected.map((item) => item.path),
        replacingUnreadableIndex,
      );
      setStatus(installed);
      setWarnings(installed.warnings ?? []);
      const labels = GENIA_COMPONENTS
        .filter((item) => installed.updated_components.includes(item.id))
        .map((item) => item.label);
      setMessage(replacingUnreadableIndex
        ? `Replaced the unreadable GenIA index with ${labels.join(", ")}. Only the selected components remain.`
        : `Installed ${labels.join(", ")}. Other installed GenIA components were preserved.`);
      setSelected([]);
      setReplaceUnreadable(false);
      window.dispatchEvent(new Event("gene-knowledge-updated"));
      onInstalled();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "GenIA installation failed");
    } finally {
      setWorking("");
    }
  }

  const canToggle = supported && source.available;
  const selectionLabel = !supported
    ? analysisScope === "exome" ? "Not used for exome" : "Not used for whole genome"
    : !source.available
      ? "Install the variant component to enable"
      : enabled ? "On" : "Off";
  return <article className={`dataset-card genia-dataset-card ${status?.installed ? "installed" : "missing"} ${supported && enabled ? "selected" : ""}`}>
    <div className="dataset-card-top"><div className="dataset-card-title"><strong>GenIA</strong><small>Optional registered-user gene, phenotype, and exact-allele evidence. Install any one file or any subset.</small></div><span className={`dataset-status ${working || statusLoading ? "working" : status?.installed ? "ready" : "missing"}`} role="status" aria-live="polite">{working === "choose" ? "Inspecting" : working === "install" ? "Installing" : statusLoading ? "Checking" : status?.installed ? `${installedComponents} of 5 installed` : "Optional · not installed"}</span></div>
    <div className={`dataset-use-control optional ${supported && enabled ? "enabled" : "disabled"}`}><label className={canToggle ? "dataset-use-toggle" : "dataset-use-toggle disabled"}><span>Use variant evidence in this run</span><input type="checkbox" checked={supported && enabled} disabled={!canToggle} onChange={(event) => onEnabled(event.target.checked)}/><span className="dataset-toggle-track" aria-hidden="true"><span/></span><strong>{selectionLabel}</strong></label></div>
    <div className="dataset-meta"><span>Registration required</span><span>User-provided files</span><span>Gene and variant evidence</span></div>
    <div className="dataset-preparation genia-dataset-preparation">
      <div className="genia-component-list">{GENIA_COMPONENTS.map((definition) => {
        const chosen = selectedByRole.get(definition.id);
        const installed = status?.components[definition.id];
        const excluded = definition.id === "variant_vcf" ? installed?.rejected_record_count ?? 0 : 0;
        return <div className={`genia-component ${chosen ? "chosen" : installed ? "installed" : "missing"}`} key={definition.id}><span className="genia-component-mark" aria-hidden="true">{chosen ? "↑" : installed ? "✓" : "○"}</span><div><strong>{definition.label}</strong><small>{chosen ? chosen.name : installed ? `${installed.source_name} · ${installed.record_count.toLocaleString()} records · installed ${compactInstalledDate(installed.installed_at)}${excluded > 0 ? ` · ${excluded.toLocaleString()} source records excluded from exact matching` : ""}` : definition.capability}</small></div><em>{chosen ? installed ? "Selected to update" : "Selected to install" : installed ? "Installed" : statusLoading ? "Checking" : "Not installed"}</em></div>;
      })}</div>
      <button type="button" disabled={Boolean(working) || statusLoading} onClick={() => void chooseFiles()}>{statusLoading ? "Checking existing GenIA components…" : working === "choose" ? "Opening file chooser…" : selected.length ? "Choose a different subset" : "Add or update GenIA files"}</button>
      <small>Select one or more GenIA CSV, TSV, or VCF exports. GUIDE-IEI recognizes each file by its schema, not its filename. Unselected installed components remain unchanged, and the GenIA VCF index is not required.</small>
      {selected.length > 0 && <div className="genia-install-confirmation">{status?.error && <label className="check-row genia-repair-confirmation"><input type="checkbox" checked={replaceUnreadable} disabled={Boolean(working)} onChange={(event) => setReplaceUnreadable(event.target.checked)}/><span className="custom-check"/><span><strong>Replace the unreadable derived GenIA index</strong><small>Only the selected components will remain. Any unselected components from the prior index will be removed.</small></span></label>}<button type="button" disabled={Boolean(working) || Boolean(status?.error && !replaceUnreadable)} onClick={() => void installFiles()}>{working === "install" ? "Validating and installing…" : status?.error ? `Repair with ${selected.length} selected component${selected.length === 1 ? "" : "s"}` : `Install ${selected.length} selected component${selected.length === 1 ? "" : "s"}`}</button></div>}
    </div>
    {message && <div className="alert" role="status" aria-live="polite">{message}</div>}
    {status?.error && <div className="resource-download-error genia-install-warning" role="alert"><strong>GenIA installation needs repair</strong><span>{status.error}</span><span>Select all five exports for a complete reinstall, or select an available subset. Confirm replacement below; a subset repair keeps only the selected components and removes omitted components.</span></div>}
    {warnings.length > 0 && <div className="genia-validation-note genia-install-warning" role="status" aria-live="polite"><strong>Some GenIA variants were not indexed</strong>{warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
    <div className="dataset-actions"><a href="https://geniadb.org/" target="_blank" rel="noreferrer">GenIA website and registration ↗</a><a href="https://doi.org/10.1016/j.jaci.2023.11.022" target="_blank" rel="noreferrer">GenIA published manuscript ↗</a></div>
  </article>;
}

function annotationAssemblySummary(job: AnnotationJob) {
  const liftover = job.progress?.stages.find((stage) => stage.id === "liftover")?.state;
  const pastDetection = Boolean(job.progress && !["preflight", "input"].includes(job.progress.stage));
  // Keep the UI compatible with a service that was already running when the
  // workbench assets updated: the stage list can still reveal the resolution.
  const resolved = job.progress?.resolved_assembly
    ?? (job.input_assembly === "auto" && liftover ? "GRCh37" : undefined)
    ?? (job.input_assembly === "auto" && pastDetection ? "GRCh38" : undefined);
  if (job.input_assembly === "auto") {
    if (!resolved) return job.status === "running" ? "Detecting genome assembly" : "Genome assembly detected automatically";
    if (resolved === "GRCh38") return "Detected GRCh38";
    if (liftover === "done") return "Detected GRCh37 · converted to GRCh38";
    if (liftover === "active") return "Detected GRCh37 · converting to GRCh38";
    return "Detected GRCh37 · GRCh38 conversion queued";
  }
  if (job.input_assembly === "GRCh37") {
    if (job.status === "succeeded") return "GRCh37 input · converted to GRCh38";
    return liftover === "active" ? "GRCh37 input · converting to GRCh38" : "GRCh37 input · GRCh38 liftover";
  }
  return "GRCh38 input";
}

function AnnotationJobProgress({ job }: { job: AnnotationJob }) {
  const progress = job.progress;
  if (!progress) return null;
  const activePhase = progress.stage === "vep" ? 1 : progress.post_processing || progress.stage === "done" ? 2 : 0;
  const phases = ["Prepare", "Annotate", "Finalize"];
  const determinate = progress.stage === "vep" && progress.vep_percent !== null;
  const activityLabel = progress.stage === "done"
    ? "Finalizing output"
    : progress.post_processing
      ? `Finalizing · ${progress.stage_label}`
      : progress.stage_label;
  return <div className="job-progress">
    <ol className="job-phase-track" aria-label="Annotation phases">
      {phases.map((label, index) => <li className={index < activePhase ? "done" : index === activePhase ? "active" : "pending"} aria-current={index === activePhase ? "step" : undefined} key={label}><span className="job-phase-dot" aria-hidden="true"/><span>{label}</span></li>)}
    </ol>
    {determinate ? <>
      <div className="progress-track" role="progressbar" aria-label="VEP annotation progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress.vep_percent!)}><span style={{ width: `${progress.vep_percent}%` }} /></div>
      <small>{(progress.variants_done ?? 0).toLocaleString()} of {(progress.variants_total ?? 0).toLocaleString()} variants annotated{typeof progress.eta_seconds === "number" ? ` · about ${formatEta(progress.eta_seconds)} left` : ""}</small>
    </> : <div className="job-stage-line" role="status" aria-live="polite"><span className="job-activity-spinner" aria-hidden="true"/><small>{activityLabel}</small></div>}
  </div>;
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
  const [dbnsfpDownloadUrl, setDbnsfpDownloadUrl] = useState("");
  const [promoterAiSourceDir, setPromoterAiSourceDir] = useState("");
  const [loGoFuncSourcePath, setLoGoFuncSourcePath] = useState("");
  const [funcVepSourcePath, setFuncVepSourcePath] = useState("");
  const [funcVepLicenseAccepted, setFuncVepLicenseAccepted] = useState(false);
  const [choosingResourceSource, setChoosingResourceSource] = useState<string | null>(null);
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
          setOutputDirectory(nextCapabilities.defaults.output_directory ?? `${nextCapabilities.pipeline_root}/results`);
          setFork(nextCapabilities.hardware.recommended_vep_workers);
          setWorkerMode("automatic");
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
                annotationSourceIsEnabled(source, analysisScope, sourceEnabled),
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

  async function downloadResource(resourceId: ResourceDownloadJob["resource_id"]) {
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

  async function prepareEngine() {
    if (!window.confirm("Set up the annotation engine?\n\nGUIDE-IEI will install missing user-space container tools on this Mac and prepare or update the VEP image. This can download several GB. Existing compatible tools are reused. No annotation datasets or patient files are downloaded or uploaded.\n\nWait for setup to finish before quitting. Dataset selection remains on this page.")) return;
    setServiceError("");
    try {
      const job = await setupAnnotationEngine();
      setResourceJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (error) {
      setServiceError(error instanceof Error ? error.message : "Could not start annotation-engine setup.");
    }
  }

  async function choosePreparationSource(resourceId: "promoterai" | "logofunc" | "funcvep") {
    setServiceError("");
    setChoosingResourceSource(resourceId);
    try {
      const selection = await chooseLocalResourceSource(resourceId);
      if (selection.cancelled || !selection.path) return;
      if (resourceId === "promoterai") setPromoterAiSourceDir(selection.path);
      else if (resourceId === "logofunc") setLoGoFuncSourcePath(selection.path);
      else setFuncVepSourcePath(selection.path);
    } catch (error) {
      setServiceError(
        error instanceof Error ? error.message : "Could not open the local file chooser.",
      );
    } finally {
      setChoosingResourceSource(null);
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

  async function prepareDbnsfp() {
    setServiceError("");
    try {
      const job = await startDbnsfpDownload(dbnsfpDownloadUrl.trim());
      setDbnsfpDownloadUrl("");
      setResourceJobs((current) => [
        job,
        ...current.filter((item) => item.id !== job.id),
      ]);
    } catch (error) {
      setServiceError(
        error instanceof Error ? error.message : "Could not download and install dbNSFP.",
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

  async function prepareFuncVep() {
    setServiceError("");
    try {
      const job = await startFuncVepPreparation(
        funcVepSourcePath.trim(),
        funcVepLicenseAccepted,
      );
      setResourceJobs((current) => [
        job,
        ...current.filter((item) => item.id !== job.id),
      ]);
    } catch (error) {
      setServiceError(
        error instanceof Error ? error.message : "Could not prepare the FuncVEP archive.",
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

  // The panel's mount-level poller already refreshes jobs (and their
  // progress payloads) every 2.5 s; active jobs are surfaced first so the
  // one row carrying a progress bar can never fall below the list cap.
  const recentJobs = useMemo(() => {
    const rank = (job: AnnotationJob) =>
      job.status === "running" ? 0 : job.status === "queued" ? 1 : 2;
    return [...jobs].sort((a, b) => rank(a) - rank(b)).slice(0, 8);
  }, [jobs]);
  const profileReady = Boolean(capabilities?.annotation_profile.ready);
  const datasetsReady = Boolean(capabilities?.annotation_profile.datasets_ready);
  const containerFoundation = capabilities?.annotation_profile.foundations.find((item) => item.id === "vep_container");
  const readinessLabel = profileReady
    ? "Ready to run"
    : containerFoundation?.state === "runtime_starting"
    ? "Starting Docker…"
    : datasetsReady && containerFoundation?.state === "runtime_unavailable"
    ? "Datasets installed"
    : containerFoundation?.state === "runtime_missing"
    ? "Container runtime missing"
    : containerFoundation?.state === "image_stale"
    ? "Annotation engine update needed"
    : "Setup needed";
  const dbnsfpOptions = capabilities?.annotation_profile.dbnsfp_predictors ?? [];
  const availableDbnsfpOptions = dbnsfpOptions.filter((item) => item.available);
  const latestResourceJobs = new Map<string, ResourceDownloadJob>();
  resourceJobs.forEach((job) => {
    if (!latestResourceJobs.has(job.resource_id)) {
      latestResourceJobs.set(job.resource_id, job);
    }
  });
  const resourceSetupBusy = resourceJobs.some(
    (job) => job.resource_id !== "omim"
      && (job.status === "queued" || job.status === "running"),
  );
  const engineJob = latestResourceJobs.get("annotation_engine");
  const engineBusy = engineJob?.status === "queued" || engineJob?.status === "running";
  const quickSetupResourceIds = ["recommended_exome", "recommended_wgs", "refresh_updates"];
  const latestQuickSetupJob = resourceJobs.find((job) => quickSetupResourceIds.includes(job.resource_id));
  const quickSetupJob = latestQuickSetupJob
    && (latestQuickSetupJob.status === "queued" || latestQuickSetupJob.status === "running")
    ? latestQuickSetupJob
    : undefined;
  const failedQuickSetupJob = latestQuickSetupJob
    && (latestQuickSetupJob.status === "failed" || latestQuickSetupJob.status === "interrupted")
    ? latestQuickSetupJob
    : undefined;
  const annotationSources = capabilities?.annotation_profile.sources ?? [];
  const exomeDatasetsInstalled = Boolean(capabilities?.annotation_profile.recommended_profiles?.exome.installed);
  const wgsDatasetsInstalled = Boolean(capabilities?.annotation_profile.recommended_profiles?.whole_genome.installed);
  const accessRequiredSources = annotationSources.filter(
    (source) => source.recommendation !== "optional"
      && (source.access === "registration" || source.access === "license"),
  );
  const bundledSources = annotationSources.filter((source) => source.setup_mode === "bundled");
  const optionalSources = annotationSources.filter(
    (source) => source.recommendation === "optional" && source.id !== "genia",
  );
  const geniaSource = annotationSources.find(
    (source) => source.recommendation === "optional" && source.id === "genia",
  );
  const standardSources = annotationSources.filter(
    (source) => !accessRequiredSources.includes(source)
      && !bundledSources.includes(source)
      && source.recommendation !== "optional",
  );
  const datasetCards = (sources: AnnotationSource[]) => sources.map((source) => <DatasetSetupCard key={source.id} source={source} analysisScope={analysisScope} enabled={annotationSourceIsEnabled(source, analysisScope, sourceEnabled)} onEnabled={(checked) => setSourceEnabled((current) => ({ ...current, [source.id]: checked }))} downloadJob={latestResourceJobs.get(source.id)} onDownload={downloadResource} preparationPath={source.id === "dbnsfp" ? dbnsfpDownloadUrl : source.id === "promoterai" ? promoterAiSourceDir : source.id === "logofunc" ? loGoFuncSourcePath : source.id === "funcvep" ? funcVepSourcePath : ""} onPreparationPath={source.id === "dbnsfp" ? setDbnsfpDownloadUrl : source.id === "funcvep" ? setFuncVepSourcePath : () => undefined} choosingPreparationPath={choosingResourceSource === source.id} onChoosePreparationPath={() => { if (source.id === "promoterai" || source.id === "logofunc" || source.id === "funcvep") void choosePreparationSource(source.id); }} onPrepare={source.id === "dbnsfp" ? prepareDbnsfp : source.id === "promoterai" ? preparePromoterAi : source.id === "logofunc" ? prepareLoGoFunc : source.id === "funcvep" ? prepareFuncVep : () => undefined} licenseAccepted={source.id === "funcvep" ? funcVepLicenseAccepted : false} onLicenseAccepted={source.id === "funcvep" ? setFuncVepLicenseAccepted : undefined}/>);
  return <section className="intake-card annotate-card intake-primary-card">
    <div className="intake-card-head"><span className="step-number">{step}</span><div><p className="eyebrow">Local VEP</p><h2>{step === 1 ? "Select raw VCF files" : setupOnly ? "Set up annotation datasets" : "Check annotation settings"}</h2></div><span className={`service-badge ${capabilities ? "online" : "offline"}`}>{capabilities ? "service ready" : "service offline"}</span></div>
    {step === 1 ? <>
      <p className="intake-copy">Drop individual VCFs below, or choose a folder. Each VCF becomes one annotation job.</p>
      <button className="drop-zone annotation-drop-zone" onClick={() => filePicker.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
        <span className="drop-icon"><Icon name="upload" /></span><strong>Drop .vcf or .vcf.gz files here</strong><span>or drop a folder / click to choose files</span><small>Compressed VCFs · single- or multi-sample</small>
      </button>
      <div className="folder-choice"><span>Process every VCF in one folder</span><button className="secondary-button" onClick={() => folderPicker.current?.click()}>Choose folder</button></div>
      <div className="dataset-setup-entry"><div><strong>First time using VEP?</strong><span>Check required datasets, register dbNSFP, and download SpliceAI or ClinVar before selecting a patient VCF.</span></div><button className="secondary-button" onClick={() => { setSetupOnly(true); setStep(2); }}>Set up annotation datasets</button></div>
      <input ref={filePicker} className="sr-only" type="file" accept={VCF_FILE_ACCEPT} multiple onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))} />
      <input ref={configureFolderInput} className="sr-only" type="file" accept={VCF_FILE_ACCEPT} multiple onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))} />
      <details className="advanced-paths"><summary>Advanced: use existing workstation paths</summary><label className="form-field"><span>VCF path(s), one per line</span><textarea rows={3} value={inputPaths} onChange={(event) => setInputPaths(event.target.value)} placeholder={"/absolute/path/patient.vcf.gz\n/absolute/path/folder/another.vcf.gz"} /></label><button className="secondary-button" onClick={() => setStep(2)} disabled={!inputPaths.trim()}>Continue to settings</button></details>
    </> : <>
      {!setupOnly && (() => {
        // Picked files and typed workstation paths BOTH submit; the summary
        // must count both or one source accumulates invisibly.
        const typedPaths = inputPaths.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
        const totalInputs = selectedFiles.length + typedPaths.length;
        const typedNames = typedPaths.map((value) => value.split("/").pop() || value);
        const parts = [
          ...selectedFiles.slice(0, 3).map((file) => file.name),
          ...(selectedFiles.length > 3 ? [`and ${selectedFiles.length - 3} more picked`] : []),
          ...typedNames.slice(0, 2).map((name) => `${name} (typed path)`),
          ...(typedNames.length > 2 ? [`and ${typedNames.length - 2} more typed paths`] : []),
        ];
        return <div className="wizard-selection"><div><strong>{totalInputs} VCF file{totalInputs === 1 ? "" : "s"} selected</strong><span>{parts.join(", ") || "No files chosen yet"}</span></div><button onClick={() => setStep(1)}>Change</button></div>;
      })()}
      {!setupOnly && <section className="settings-section"><div className="settings-section-head"><div><h3>{analysisScope === "exome" ? "Exome-region annotation" : "Whole-genome annotation"}</h3><p>{analysisScope === "exome" ? "Coding exons and splice-region padding are selected before VEP." : "The input is prepared locally for efficient parallel VEP annotation."}</p></div></div>{analysisScope === "whole_genome" && <details className="advanced-paths"><summary>Technical details</summary><p>The input is prepared as sorted BGZF with tabix/CSI indexing before parallel VEP annotation.</p></details>}
        <div className="run-defaults"><div className="scope-run-summary"><strong>{analysisScope === "exome" ? "Exome region only" : "Whole genome"}</strong><span>{analysisScope === "exome" ? "promoterAI and full-genome CADD unavailable" : "indexed WGS intake · installed predictors on by default"}</span></div><Check label="PASS records only" checked={passOnly} onChange={setPassOnly} /><Check label="Refresh ClinVar before run" checked={useClinvar} onChange={setUseClinvar} /></div>
        <div className="form-pair simple"><label className="form-field"><span>Input genome build</span><select value={inputAssembly} onChange={(event) => setInputAssembly(event.target.value as typeof inputAssembly)}>{capabilities?.input_assemblies.map((item) => <option key={item.id} value={item.id}>{item.label}</option>) ?? <option value="GRCh38">GRCh38 / hg38</option>}</select></label><label className="form-field worker-field"><span>VEP workers <small>{workerMode === "automatic" ? "Automatic" : "Custom"}</small></span><div className="worker-value"><strong>{fork}</strong><span>worker{fork === 1 ? "" : "s"}</span>{workerMode === "custom" && <button type="button" onClick={() => { const recommended = capabilities?.hardware.recommended_vep_workers ?? 1; setFork(recommended); setWorkerMode("automatic"); }}>Use automatic</button>}</div><input className="worker-range" type="range" min={1} max={capabilities?.hardware.max_vep_workers ?? 8} step={1} value={fork} onChange={(event) => { setFork(Number(event.target.value)); setWorkerMode("custom"); }} /><small>Detected {capabilities?.hardware.logical_cpus ?? "—"} logical CPU threads · recommended {capabilities?.hardware.recommended_vep_workers ?? "—"}.</small></label></div>
      </section>}
      <section className="settings-section"><div className="settings-section-head"><div><h3>Annotation datasets</h3><p>Availability and indexes are checked automatically. Open each dataset for sources and setup instructions.</p></div><span className={`readiness ${profileReady || datasetsReady ? "ready" : "missing"}`}>{readinessLabel}</span></div>
        {capabilities?.annotation_profile.error && <div className="alert error">{capabilities.annotation_profile.error}</div>}
        {capabilities?.annotation_profile.foundations.map((item) => <div className="foundation-row" key={item.id} title={item.description || undefined}><span className={`availability-dot ${item.available ? "ready" : "missing"}`} /><strong>{item.label}</strong><span>{item.available ? `Available${item.version ? ` · release ${item.version}` : ""}` : item.message || "Missing"}</span></div>)}
        {containerFoundation && (!containerFoundation.available || engineBusy) && <div className="dataset-quick-setup annotation-engine-setup">
          <div><h4>Annotation engine</h4><p>Prepare or repair the annotation engine before annotating raw VCFs. Existing compatible components are reused.</p></div>
          {!containerFoundation.available && (capabilities?.platform === "darwin"
            ? <button type="button" className="secondary-button" disabled={resourceSetupBusy || containerFoundation.state === "runtime_starting"} onClick={() => void prepareEngine()}>{engineBusy ? "Setting up annotation engine…" : engineJob?.status === "failed" || engineJob?.status === "interrupted" ? "Retry annotation-engine setup" : "Set up annotation engine"}</button>
            : <p>Windows: start Docker Desktop and enable WSL integration for your Ubuntu distribution. Linux: install/start the configured container runtime. Then use recommended dataset setup below to build the VEP image if needed.</p>)}
          {engineBusy && <div className="resource-progress"><progress/><span role="status" aria-live="polite">{engineJob.message || "Preparing the annotation engine…"}</span></div>}
          {engineJob?.error && <p role="status">{engineJob.error}</p>}
        </div>}
        {engineJob && <details className="advanced-paths annotation-engine-diagnostics"><summary>Annotation-engine diagnostics</summary><p>Latest setup: {engineJob.status === "succeeded" ? "completed" : engineJob.status}. Current availability is shown in the Ensembl VEP row above.</p><h4>Annotation-engine setup log</h4><pre>{engineJob.log || "No log output yet."}</pre></details>}
        <div className="dataset-quick-setup">
          <div><p className="eyebrow">One-click setup</p><h4>Download recommended public datasets</h4><p>Choose the analysis profile you expect to use. Complete installations are skipped immediately without rereading large files. Registration- and license-gated datasets are handled separately below.</p></div>
          <div className="dataset-quick-actions">
            <button type="button" disabled={resourceSetupBusy || exomeDatasetsInstalled} onClick={() => void downloadResource("recommended_exome")}><strong>{exomeDatasetsInstalled ? "Exome datasets installed" : failedQuickSetupJob?.resource_id === "recommended_exome" ? "Retry exome setup" : "Recommended for exome"}</strong><span>{exomeDatasetsInstalled ? "No download needed · use Update for new ClinVar or ClinGen releases" : "VEP cache, reference FASTA, LOFTEE data, SpliceAI, ClinVar and ClinGen · up to ~90 GiB"}</span></button>
            <button type="button" disabled={resourceSetupBusy || wgsDatasetsInstalled} onClick={() => void downloadResource("recommended_wgs")}><strong>{wgsDatasetsInstalled ? "WGS public core installed" : failedQuickSetupJob?.resource_id === "recommended_wgs" ? "Retry WGS setup" : "Recommended for WGS"}</strong><span>{wgsDatasetsInstalled ? "No download needed · exome set, SCREEN and AVI are present" : "Exome set plus SCREEN and AlphaGenome AVI · up to ~170 GiB free for setup"}</span></button>
            <button type="button" className="dataset-update-all" disabled={resourceSetupBusy} onClick={() => void downloadResource("refresh_updates")}><strong>{failedQuickSetupJob?.resource_id === "refresh_updates" ? "Retry dataset update" : "Update installed datasets"}</strong><span>Refresh ClinVar and ClinGen; pinned resources stay unchanged</span></button>
          </div>
          {quickSetupJob && <><div className="resource-progress"><progress max={100} value={quickSetupJob.progress ?? undefined}/><span role="status" aria-live="polite">{resourceProgressMessage(quickSetupJob, "Preparing recommended datasets…")}</span></div><details className="dataset-instructions"><summary>Dataset details</summary><div className="dataset-technical-status"><span>Technical status</span><code>{quickSetupJob.status}</code>{quickSetupJob.message && <p>{quickSetupJob.message}</p>}</div></details></>}
          {failedQuickSetupJob && <div className="resource-download-error"><strong>Setup stopped before completion.</strong><span>{failedQuickSetupJob.error || failedQuickSetupJob.message}</span><span>Files that completed successfully are preserved; retry resumes only missing work.</span><button type="button" disabled={resourceSetupBusy} onClick={() => void downloadResource(failedQuickSetupJob.resource_id)}>{failedQuickSetupJob.resource_id === "recommended_wgs" ? "Retry WGS setup" : failedQuickSetupJob.resource_id === "refresh_updates" ? "Retry dataset update" : "Retry exome setup"}</button><details><summary>Setup log</summary><pre>{failedQuickSetupJob.log || "No log output was captured."}</pre></details></div>}
        </div>
        {(accessRequiredSources.length > 0 || geniaSource) && <div className="dataset-group access-required"><div className="dataset-group-head"><div><p className="eyebrow">User action needed</p><h4>Needs registration, a license, or private files</h4></div><span>Required and optional sources are labeled on each card</span></div><div className="dataset-grid">{datasetCards(accessRequiredSources)}{geniaSource && <GeniaDatasetSetupCard source={geniaSource} analysisScope={analysisScope} enabled={annotationSourceIsEnabled(geniaSource, analysisScope, sourceEnabled)} onEnabled={(checked) => setSourceEnabled((current) => ({ ...current, genia: checked }))} onInstalled={() => { void getCapabilities().then(setCapabilities).catch(() => undefined); }}/>}</div></div>}
        {standardSources.length > 0 && <div className="dataset-group"><div className="dataset-group-head"><div><p className="eyebrow">Public downloads</p><h4>One-click downloads</h4></div></div><div className="dataset-grid">{datasetCards(standardSources)}</div></div>}
        {bundledSources.length > 0 && <div className="dataset-group"><div className="dataset-group-head"><div><p className="eyebrow">Included</p><h4>Installed with the one-click setup</h4></div><span>Each card can re-download its own files if any are reported missing</span></div><div className="dataset-grid">{datasetCards(bundledSources)}</div></div>}
        <div className="dataset-group optional"><div className="dataset-group-head"><div><p className="eyebrow">Optional</p><h4>Optional add-ons</h4></div></div><div className="dataset-grid">{datasetCards(optionalSources)}<OmimDatasetSetupCard downloadJob={latestResourceJobs.get("omim")} onJobStarted={(job) => setResourceJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])}/></div></div>
        {!setupOnly && <details className="dbnsfp-options" open><summary><span><strong>Additional dbNSFP predictors</strong><small>Optional; the core panel — AlphaMissense, CADD, REVEL, SIFT, PolyPhen-2 — is always included.</small></span><em>{selectedDbnsfpPredictors.size} selected</em></summary><div className="dbnsfp-options-body"><div className="dbnsfp-option-actions"><p>Select only predictors useful to your analysis. More columns increase output size and annotation work.</p><div><button type="button" onClick={() => setSelectedDbnsfpPredictors(new Set(availableDbnsfpOptions.map((item) => item.id)))}>Select all available</button><button type="button" onClick={() => setSelectedDbnsfpPredictors(new Set())}>Clear</button></div></div><div className="dbnsfp-predictor-grid">{dbnsfpOptions.map((item) => <label className={!item.available ? "unavailable" : ""} key={item.id}><input type="checkbox" checked={selectedDbnsfpPredictors.has(item.id)} disabled={!item.available} onChange={(event) => setSelectedDbnsfpPredictors((current) => toggleSet(current, item.id, event.target.checked))}/><span className="custom-check"/><span><strong>{item.label}</strong><small>{item.category}</small></span></label>)}</div>{dbnsfpOptions.some((item) => !item.available) && <p className="dbnsfp-unavailable-note">Unavailable choices are not present in the installed dbNSFP header and cannot be queued.</p>}</div></details>}
      </section>
      {!setupOnly && <details className="advanced-paths"><summary>Output and execution</summary><div className="form-pair simple"><label className="form-field"><span>Output folder</span><input value={outputDirectory} onChange={(event) => setOutputDirectory(event.target.value)} /></label><label className="form-field"><span>Execution</span><select value={profile} onChange={(event) => setProfile(event.target.value)} disabled={!capabilities}>{capabilities?.profiles.map((item) => <option key={item.id} value={item.id}>{item.label}</option>) ?? <option>Local workstation</option>}</select></label></div></details>}
      <div className="annotation-actions wizard-actions"><button className="secondary-button" onClick={() => { setSetupOnly(false); setStep(1); }}>{setupOnly ? "Done" : "Back"}</button>{!setupOnly && <button className="primary-button dark" onClick={queueAnnotation} disabled={submitting || resourceSetupBusy || !capabilities || !profileReady}>{submitting ? stageProgress || "Starting…" : resourceSetupBusy ? "Wait for dataset download" : "Start VEP annotation"}</button>}</div>
    </>}
    {serviceError && <div className={`alert ${capabilities ? "error" : ""}`}>{serviceError}{!capabilities && <small> Start <span className="mono">python3 -m local_service.workbench_service</span> in the pipeline folder.</small>}</div>}
    {recentJobs.length > 0 && <div className="job-list"><div className="job-list-head"><strong>Recent annotation jobs</strong><span>{jobs.filter((job) => job.status === "queued" || job.status === "running").length} active</span></div>{recentJobs.map((job) => <div className="job-row" key={job.id}><span className={`job-status ${job.status}`}>{job.status}</span><div><strong title={job.input_path}>{fileName(job.input_path)}</strong><span title={job.final_output_path ?? job.output_path}>{job.final_output_path ?? job.output_path}</span><small className="job-assembly">{annotationAssemblySummary(job)}</small>{job.status === "running" && <AnnotationJobProgress job={job}/>} {job.error && <small className="job-error">{job.error}</small>}</div><div className="job-actions">{job.status === "succeeded" && <button onClick={() => reviewJob(job)}>Review</button>}<button onClick={() => showLog(job.id)}>Log</button>{(job.status === "queued" || job.status === "running") && <button onClick={() => stopJob(job.id)}>Cancel</button>}</div></div>)}</div>}
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

function cohortProfileLabel(
  profile: "full" | "prefiltered",
  scope: "exome" | "whole_genome" | "unknown",
) {
  if (profile === "prefiltered") return "Compact WGS candidate";
  if (scope === "whole_genome") return "Full WGS index";
  if (scope === "exome") return "Full exome index";
  return "Full index · scope not recorded";
}

function annotationOutputPath(inputPath: string, outputDirectory: string) {
  const separator = inputPath.includes("\\") && !inputPath.includes("/") ? "\\" : "/";
  const filename = fileName(inputPath).replace(/\.vcf(?:\.gz)?$/i, "") + ".vep.vcf.gz";
  // An explicit separator test: with no separator both lastIndexOf calls
  // return -1 and slice(0, -1) truncated the last character of a bare
  // filename into a directory name ("sample.vcf.g/…").
  const separatorIndex = Math.max(inputPath.lastIndexOf("/"), inputPath.lastIndexOf("\\"));
  const inputDirectory = separatorIndex >= 0 ? inputPath.slice(0, separatorIndex) : "";
  const directory = outputDirectory.trim().replace(/[\\/]$/, "") || inputDirectory;
  return directory ? `${directory}${separator}${filename}` : filename;
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

function useGeneKnowledge(gene: string) {
  const [knowledge, setKnowledge] = useState<GeneKnowledgeGene | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setKnowledge(null); setError("");
    getGeneKnowledgeGene(gene)
      .then((value) => { if (active) setKnowledge(value); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Gene lookup failed"); });
    return () => { active = false; };
  }, [gene]);
  return { knowledge, error };
}

function GeneKnowledgeSummary({ gene, onOpen }: { gene: string; onOpen: () => void }) {
  const { knowledge, error } = useGeneKnowledge(gene);
  if (error) return null;
  const geniaRelationships = knowledge?.genia?.relationships ?? [];
  return <button className="gene-knowledge-summary" onClick={onOpen}><div><span>Gene-level knowledge</span><strong>{gene}</strong></div>{knowledge ? <div className="gene-summary-badges">{knowledge.iuis.length > 0 && <span>IUIS · {knowledge.iuis.length}</span>}{knowledge.clingen_validity.length > 0 && <span>ClinGen validity · {knowledge.clingen_validity.length}</span>}{knowledge.clingen_dosage?.hi_score === "3" && <span>ClinGen HI · sufficient</span>}{knowledge.omim.length > 0 && <span>OMIM · {knowledge.omim.length}</span>}{geniaRelationships.length > 0 && <span>GenIA · {geniaRelationships.length}</span>}{!knowledge.iuis.length && !knowledge.clingen_validity.length && !knowledge.clingen_dosage && !knowledge.omim.length && !geniaRelationships.length && <span>No source assertion found</span>}</div> : <span>Loading…</span>}<span>Open gene evidence →</span></button>;
}

function IuisImmuneFinding({ label, raw, summary }: { label: string; raw: string; summary: string }) {
  const tags = summary.split("|").filter(Boolean);
  return <div className="iuis-immune-finding"><span>{label}</span><div>{tags.length ? tags.map((tag) => <em key={tag}>{tag}</em>) : <em className="unreported">Not reported</em>}</div><small>{raw || "No source value"}</small></div>;
}

function geniaCurationLabel(value: GeneKnowledgeGene["genia"]["relationships"][number]["curation_status"]) {
  return {
    curated: "Curated",
    ongoing: "Curation ongoing",
    not_curated: "Listed; not yet curated",
    unknown: "Curation status not provided",
  }[value];
}

function geniaRelationshipSource(value: GeneKnowledgeGene["genia"]["relationships"][number]["relationship_source"]) {
  return {
    gei: "GEI gene–disease list",
    disease_catalog: "GenIA disease catalog",
    phenotype_export: "GenIA disease–phenotype export",
  }[value];
}

function geniaPhenotypeFrequency(count: number | null, percent: number | null) {
  const countLabel = count === null ? "count not reported" : `${count.toLocaleString()} subject${count === 1 ? "" : "s"}`;
  const percentLabel = percent === null ? "percentage not reported" : `${compactNumber(percent, 1)}%`;
  return `${countLabel} · ${percentLabel}`;
}

function GeniaGeneKnowledge({ genia, gene }: {
  genia: GeneKnowledgeGene["genia"] | undefined;
  gene: string;
}) {
  const relationships = genia?.relationships ?? [];
  const geneLinkedInstalled = Boolean(genia?.capabilities.gene_disease || genia?.capabilities.phenotype_evidence);
  return <article className="gene-knowledge-card genia-gene-card"><div className="section-title"><div><p className="eyebrow">Registered-user evidence resource</p><h3>GenIA</h3></div><span>{relationships.length || "—"}</span></div>
    {!genia?.installed && <div className="knowledge-license-note"><strong>GenIA gene knowledge not installed</strong><span>GenIA exports require registration and are not shipped with GUIDE-IEI. Add any available gene or phenotype component under Import &amp; QC → Set up annotation datasets → User action needed.</span></div>}
    {genia?.installed && !geneLinkedInstalled && <div className="knowledge-license-note"><strong>No gene-linked GenIA component is installed</strong><span>The installed GenIA components do not provide gene–disease or disease–phenotype relationships. Add one of those exports to enable gene lookup.</span></div>}
    {geneLinkedInstalled && relationships.length === 0 && <p className="knowledge-empty">The installed GenIA gene-linked components contain no record for {gene}. This is not evidence that the gene lacks a disease association.</p>}
    {relationships.length > 0 && <div className="genia-relationship-list">{relationships.map((item, index) => {
      const curationLabel = geniaCurationLabel(item.curation_status);
      return <section className="genia-relationship" key={`${item.disease_id || item.source_id}:${item.disease_name}:${index}`}><header><div><strong>{item.disease_name || "Disease name not provided"}</strong><small>{geniaRelationshipSource(item.relationship_source)}{item.source_id ? ` · ${item.source_id}` : ""}{item.disease_id ? ` · disease ${item.disease_id}` : ""}</small></div><span className={`genia-curation ${item.curation_status}`}>{curationLabel}</span></header>
        {(item.moi || item.moa || item.iuis_classification) && <div className="genia-relationship-badges">{item.moi && <span>{item.moi}</span>}{item.moa && <span>{item.moa}</span>}{item.iuis_classification && <span>IUIS · {item.iuis_classification}</span>}</div>}
        <dl className="genia-relationship-metadata"><div><dt>Cross-references</dt><dd>{[item.omim_id ? `OMIM ${item.omim_id}` : "", item.mondo_id].filter(Boolean).join(" · ") || "Not reported"}</dd></div><div><dt>Reported cases / families</dt><dd>{item.case_count || "—"} / {item.family_count || "—"}</dd></div><div><dt>Publication / update</dt><dd>{[item.publication_year, item.last_updated].filter(Boolean).join(" · ") || "Not reported"}</dd></div><div><dt>ClinGen classification in GenIA export</dt><dd>{item.clingen_class ? `${item.clingen_class}${item.clingen_review_date ? ` · ${item.clingen_review_date}` : ""}` : "Not reported"}</dd></div></dl>
        {item.synonyms && <p className="genia-synonyms"><strong>Other names</strong><span>{item.synonyms}</span></p>}
        {item.phenotypes.length > 0 ? <details className="genia-phenotypes"><summary>{item.phenotypes.length} phenotype-frequency record{item.phenotypes.length === 1 ? "" : "s"}</summary><p>Percentages describe subjects represented in this GenIA export; they are not population prevalence.</p><div className="knowledge-table-wrap"><table className="knowledge-table"><thead><tr><th>Phenotype</th><th>Reported present</th><th>Reported absent</th><th>Unreported</th></tr></thead><tbody>{item.phenotypes.map((phenotype, phenotypeIndex) => <tr key={`${phenotype.clinical_term_id}:${phenotype.hpo_id}:${phenotypeIndex}`}><td><strong>{phenotype.clinical_term || phenotype.hpo_term || "Phenotype term not provided"}</strong><small>{[phenotype.hpo_id, phenotype.hpo_term, phenotype.rank !== null ? `rank ${phenotype.rank}` : ""].filter(Boolean).join(" · ")}</small>{phenotype.phenotype_description && <details><summary>Description</summary><span>{phenotype.phenotype_description}</span></details>}</td><td>{geniaPhenotypeFrequency(phenotype.count_yes, phenotype.percent_yes)}</td><td>{geniaPhenotypeFrequency(phenotype.count_no, phenotype.percent_no)}</td><td>{geniaPhenotypeFrequency(phenotype.count_unreported, phenotype.percent_unreported)}</td></tr>)}</tbody></table></div></details> : genia?.capabilities.phenotype_evidence ? <p className="genia-no-phenotypes">No phenotype-frequency records were included for this disease in the installed GenIA export.</p> : <p className="genia-no-phenotypes">Disease–phenotype associations are not installed; no phenotype absence is inferred.</p>}
      </section>;
    })}</div>}
    {relationships.length > 0 && <p className="constraint-note">Curation status is shown exactly by source role. A catalog or phenotype-export entry is not presented as fully curated unless the GEI export explicitly supplies that status.</p>}
  </article>;
}

function GeneKnowledgePanel({ gene, constraintRow, relatedRows = [] }: { gene: string; constraintRow?: VariantRow; relatedRows?: VariantRow[] }) {
  const { knowledge, error } = useGeneKnowledge(gene);
  if (error) return <div className="alert error">{error}</div>;
  if (!knowledge) return <div className="empty-state"><h2>Loading {gene}…</h2></div>;
  const identity = knowledge.identity;
  return <section className="gene-knowledge-review">
    <header><p className="eyebrow">Gene-level evidence</p><h2>{identity?.symbol || gene}</h2><p>{identity?.name || "No unambiguous HGNC identity was found."}</p><div className="gene-identity-badges">{identity?.hgnc_id && <span>{identity.hgnc_id}</span>}{identity?.ensembl_gene_id && <span>{identity.ensembl_gene_id}</span>}{identity?.entrez_id && <span>NCBI {identity.entrez_id}</span>}{knowledge.aliases.filter((item) => item.kind !== "approved").slice(0, 8).map((item) => <span key={`${item.kind}:${item.alias}`}>{item.alias} · {item.kind}</span>)}</div></header>
    {relatedRows.length > 0 && <article className="gene-knowledge-card"><div className="section-title"><div><p className="eyebrow">Current review</p><h3>Variants in {gene}</h3></div><span>{relatedRows.length}</span></div><div className="knowledge-table-wrap"><table className="knowledge-table current-review-table"><thead><tr><th>Variant</th><th>Sample</th><th>Consequence</th><th>Genotype</th></tr></thead><tbody>{relatedRows.map((row) => <tr key={row.key}><td><strong><VariantIdentifier row={row}/></strong><small title={row.hgvsP || row.hgvsC}>{row.hgvsP || row.hgvsC}</small></td><td title={row.sample}>{row.sample}</td><td title={cleanLabel(row.consequence)}>{cleanLabel(row.consequence)}</td><td>{row.genotype}</td></tr>)}</tbody></table></div><p className="constraint-note">For carriers in other retained samples, search this gene in Cohort Search. Absence from a heterogeneous candidate index is not proof that a sample lacks a variant.</p></article>}
    {constraintRow && <article className="gene-knowledge-card"><div className="section-title"><div><p className="eyebrow">Population constraint</p><h3>gnomAD</h3></div></div><EvidenceGrid items={[["LOEUF", compactNumber(constraintRow.loeuf ?? null)], ["pLI", compactNumber(constraintRow.pLi ?? null)], ["Missense Z", compactNumber(constraintRow.missenseZ ?? null)], ["Release", constraintRow.constraintRelease || "—"]]}/><p className="constraint-note">Constraint is population evidence and is not equivalent to ClinGen dosage sensitivity or proof of a disease mechanism.</p></article>}
    <article className="gene-knowledge-card iuis-card"><div className="section-title"><div><p className="eyebrow">Inborn errors of immunity</p><h3>IUIS classification</h3></div><span>{knowledge.iuis.length} assertion{knowledge.iuis.length === 1 ? "" : "s"}</span></div>{knowledge.iuis.length ? <div className="iuis-assertion-list">{knowledge.iuis.map((item, index) => <section className="iuis-assertion" key={`${item.disease}:${index}`}><header><div><strong>{item.disease}</strong><small>Source gene: {item.source_gene || gene}{item.omim ? ` · OMIM ${item.omim}` : ""}</small></div><div className="iuis-mechanism-badges">{item.inheritance && <span>{item.inheritance}</span>}{item.mechanism && <span className="mechanism">{item.mechanism}</span>}</div></header><div className="iuis-category-path"><div><span>Major category</span><strong>{item.major_category || "Not reported"}</strong></div><div><span>Subcategory</span><strong>{item.subcategory || "Not reported"}</strong></div></div><div className="iuis-immune-profile"><IuisImmuneFinding label="T cells" raw={item.t_cell_count} summary={item.t_cell_summary}/><IuisImmuneFinding label="B cells" raw={item.b_cell_count} summary={item.b_cell_summary}/><IuisImmuneFinding label="Immunoglobulins" raw={item.immunoglobulin_levels} summary={item.immunoglobulin_summary}/><IuisImmuneFinding label="Neutrophils" raw={item.neutrophil_count} summary={item.neutrophil_summary}/><IuisImmuneFinding label="Other affected cells" raw={item.other_affected_cells} summary={item.other_affected_cell_groups}/></div><div className="iuis-associated-features"><span>Associated features</span><p>{item.associated_features || "Not reported in the IUIS source table."}</p></div></section>)}</div> : <p className="knowledge-empty">No IUIS association was found for this gene.</p>}</article>
    <article className="gene-knowledge-card"><div className="section-title"><div><p className="eyebrow">Expert gene–disease curation</p><h3>ClinGen validity</h3></div><span>{knowledge.clingen_validity.length} assertion{knowledge.clingen_validity.length === 1 ? "" : "s"}</span></div>{knowledge.clingen_validity.length ? <div className="knowledge-table-wrap"><table className="knowledge-table"><thead><tr><th>Disease</th><th>Inheritance</th><th>Classification</th><th>Expert panel / date</th></tr></thead><tbody>{knowledge.clingen_validity.map((item, index) => <tr key={`${item.mondo_id}:${item.moi}:${index}`}><td><strong>{item.disease}</strong><small>{item.mondo_id}</small></td><td>{item.moi || "—"}</td><td><strong>{item.classification}</strong>{item.report_url && <a href={item.report_url} target="_blank" rel="noreferrer">Open report</a>}</td><td>{item.expert_panel || "—"}<small>{item.classification_date}</small></td></tr>)}</tbody></table></div> : <p className="knowledge-empty">No ClinGen gene–disease validity assertion was found.</p>}</article>
    <article className="gene-knowledge-card"><div className="section-title"><div><p className="eyebrow">Dosage sensitivity</p><h3>ClinGen dosage</h3></div></div>{knowledge.clingen_dosage ? <><EvidenceGrid items={[["Haploinsufficiency", `${knowledge.clingen_dosage.hi_score || "—"} · ${knowledge.clingen_dosage.hi_description || "No description"}`], ["HI disease", knowledge.clingen_dosage.hi_disease_id || "—"], ["Triplosensitivity", `${knowledge.clingen_dosage.ts_score || "—"} · ${knowledge.clingen_dosage.ts_description || "No description"}`], ["TS disease", knowledge.clingen_dosage.ts_disease_id || "—"], ["Last evaluated", knowledge.clingen_dosage.date_last_evaluated || "—"]]}/><p className="constraint-note">ClinGen score 3 means sufficient evidence for dosage pathogenicity. Score 30 denotes a gene associated with an autosomal-recessive phenotype and must not be treated as haploinsufficiency evidence.</p></> : <p className="knowledge-empty">No ClinGen dosage evaluation was found.</p>}</article>
    <GeniaGeneKnowledge genia={knowledge.genia} gene={gene}/>
    <article className="gene-knowledge-card"><div className="section-title"><div><p className="eyebrow">Mendelian disease catalog</p><h3>OMIM</h3></div><span>{knowledge.omim.length || "—"}</span></div>{!knowledge.omim_installed ? <div className="knowledge-license-note"><strong>OMIM dataset not installed</strong><span>OMIM data are licensed and are not shipped with this software. Install them under Import &amp; QC → Set up annotation datasets → Optional add-ons.</span></div> : knowledge.omim.length ? <><div className="knowledge-table-wrap"><table className="knowledge-table"><thead><tr><th>Phenotype</th><th>Phenotype MIM</th><th>Gene MIM</th><th>Mapping / inheritance</th></tr></thead><tbody>{knowledge.omim.map((item, index) => <tr key={`${item.phenotype_mim}:${index}`}><td><strong>{item.phenotype}</strong></td><td>{item.phenotype_mim || "—"}</td><td>{item.gene_mim || "—"}</td><td>{[`Mapping key ${item.mapping_key || "—"}`, item.inheritance].filter(Boolean).join(" · ")}</td></tr>)}</tbody></table></div><p className="constraint-note">OMIM mapping keys describe the evidence basis for a gene–phenotype map (1–4); they are not pathogenicity grades for this variant.</p></> : <p className="knowledge-empty">The installed OMIM dataset contains no association for this gene.</p>}</article>
    <div className="interpretation-banner"><strong>Gene evidence is not variant evidence</strong><span>A gene–disease or dosage association does not establish pathogenicity, mechanism, or relevance of the selected variant. Sources are shown separately and are not merged into one classification.</span></div>
  </section>;
}

function GeneKnowledgeSettingsPanel() {
  const [status, setStatus] = useState<GeneKnowledgeStatus | null>(null);
  const [query, setQuery] = useState("NFKB1");
  const [result, setResult] = useState<GeneKnowledgeGene | null>(null);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const refresh = () => getGeneKnowledgeStatus().then(setStatus).catch((error) => setMessage(error instanceof Error ? error.message : "Gene resource status failed"));
  useEffect(() => { void refresh(); }, []);
  async function updatePublic() {
    setWorking(true); setMessage("");
    try {
      const job = await startResourceDownload("gene_knowledge");
      let current = job;
      while (["queued", "running"].includes(current.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 750));
        current = (await getResourceDownloads()).find((item) => item.id === job.id) ?? current;
      }
      if (current.status !== "succeeded") throw new Error(current.error || current.message || "Public resource update failed");
      setMessage("HGNC and ClinGen public resources were updated and switched atomically.");
      await refresh(); window.dispatchEvent(new Event("gene-knowledge-updated"));
    } catch (error) { setMessage(error instanceof Error ? error.message : "Public resource update failed"); }
    finally { setWorking(false); }
  }
  async function search() {
    setMessage("");
    try { setResult(await getGeneKnowledgeGene(query.trim())); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Gene lookup failed"); }
  }
  return <div className="gene-knowledge-settings"><div className="content-header"><div><p className="eyebrow">Versioned local resources</p><h1>Gene knowledge</h1><p className="subtitle">HGNC identity, full IUIS associations, ClinGen validity and dosage, plus optional user-provided OMIM and GenIA data. Gene resources are joined during review; GenIA exact-allele evidence is recorded separately during annotation.</p></div></div>
    {message && <div className="alert">{message}</div>}
    <section className="gene-resource-section"><div className="section-title"><div><p className="eyebrow">Redistributable bundle</p><h2>HGNC · IUIS · ClinGen</h2></div><button className="secondary-button" disabled={working} onClick={() => void updatePublic()}>{working ? "Updating…" : "Check and update now"}</button></div>{status?.error && <div className="alert error">{status.error}</div>}<div className="gene-resource-grid">{status?.resources.map((resource) => <article key={resource.id}><strong>{resource.id === "clingen_validity" ? "ClinGen validity" : resource.id === "clingen_dosage" ? "ClinGen dosage" : resource.id.toUpperCase()}</strong><span>{resource.release || "release not labeled"}</span><small>{resource.record_count.toLocaleString()} records</small><a href={resource.source_url} target="_blank" rel="noreferrer">Official source</a></article>)}</div><p className="constraint-note">Updates download only official HGNC and ClinGen public releases, validate their schemas, build a new local database, and switch only after a successful build. IUIS remains pinned to the reviewed October 2024 classification until a new IUIS release is intentionally adopted.</p></section>
    <section className="gene-resource-section"><div className="section-title"><div><p className="eyebrow">Lookup</p><h2>Open a gene</h2></div></div><div className="gene-lookup"><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void search(); }} placeholder="Approved symbol, alias, HGNC or Ensembl ID"/><button className="secondary-button" onClick={() => void search()}>Open</button></div>{result && <GeneKnowledgePanel gene={result.identity?.symbol || query}/>}</section>
  </div>;
}

function Stat({ value, label }: { value: number | string; label: string }) { return <div><strong>{typeof value === "number" ? value.toLocaleString() : value}</strong><span>{label}</span></div>; }

function GenePanel({ genes, rows, onSelect }: { genes: [string, number][]; rows: VariantRow[]; onSelect: (gene: string) => void }) {
  return <><div className="content-header"><div><p className="eyebrow">Gene-level summary</p><h1>Prioritized genes</h1><p className="subtitle">Counts reflect the active variant filters.</p></div></div><div className="gene-grid">{genes.map(([gene, count]) => { const geneRows = rows.filter((row) => row.gene === gene); const strongest = geneRows.some((row) => row.impact === "HIGH") ? "HIGH" : geneRows[0]?.impact; return <button key={gene} onClick={() => onSelect(gene)}><span className="gene-rank">{String(genes.indexOf(genes.find(([item]) => item === gene)!) + 1).padStart(2, "0")}</span><strong>{gene}</strong><span>{count} variant{count === 1 ? "" : "s"}</span><span className={`impact-pill ${strongest?.toLowerCase()}`}>{strongest}</span><Icon name="chevron" /></button>; })}</div></>;
}

function downloadTsv(rows: VariantRow[], qcSettings: VariantQcSettings) {
  const detectedDbnsfp = ADDITIONAL_DBNSFP_PREDICTORS.filter((definition) =>
    rows.some((row) => row.availableDbnsfpPredictors?.includes(definition.id)));
  const headers = ["sample", "variant_id", "chrom", "pos", "ref", "alt", "original_assembly", "original_chrom", "original_pos", "original_ref", "original_alt", "unscored_indel_reasons", "gene", "HGVSc", "HGVSp", "consequence", "impact", "gnomad_popmax", "gnomad_popmax_population", "gnomad_frequencies", "CADD_phred", "CADD_raw", "AlphaMissense", "AlphaMissense_pred", "REVEL", "MetaRNN", "MetaRNN_pred", "PrimateAI", "PrimateAI_pred", "SIFT", "SIFT_pred", "PolyPhen_HDIV", "PolyPhen_HDIV_pred", "GERP_RS", "phyloP100way", "phastCons100way", "LOFTEE", "LOFTEE_filter", "LOFTEE_flags", "LOFTEE_PTC_50BP", "LOFTEE_50BP_original", "PTC_distance_from_last_exon", "PTC_calc_status", "haplotype_frame_status", "haplotype_frame_partners", "haplotype_protein_change", "ClinVar", "ClinVar_conflicting_evidence", "SpliceAI", "promoterAI", "LoGoFunc_prediction", "LoGoFunc_neutral", "LoGoFunc_GOF", "LoGoFunc_LOF", "LoGoFunc_source_transcript", "LoGoFunc_source_HGVSp", "LoGoFunc_match", "FuncVEP_CTI", "FuncVEP_CTE", "FuncVEP_SP", "predictor_observations", "genotype", "DP", "GQ", "allele_balance", "carriers", "MANE", "PICK", "RepeatMasker", "SegDup", ...detectedDbnsfp.flatMap((definition) => [definition.scoreColumn, ...(definition.predictionColumn ? [definition.predictionColumn] : [])])];
  const body = rows.map((row) => [row.sample, fullVariantId(row), row.chrom, row.pos, row.ref, row.alt, row.originalAssembly ?? "", row.originalChrom ?? "", row.originalPos ?? "", row.originalRef ?? "", row.originalAlt ?? "", row.unscoredIndelReasons?.join("&") ?? "", row.gene, row.hgvsC, row.hgvsP, row.consequence, row.impact, row.gnomadPopmax ?? "", row.gnomadPopmaxPopulation ?? "", JSON.stringify(row.gnomadFrequencies ?? {}), row.cadd ?? "", row.caddRaw ?? "", row.alphaMissense ?? "", row.alphaPrediction, row.revel ?? "", row.metaRnn ?? "", row.metaRnnPrediction ?? "", row.primateAi ?? "", row.primateAiPrediction ?? "", row.sift ?? "", row.siftPrediction ?? "", row.polyPhen ?? "", row.polyPhenPrediction ?? "", row.gerpRs ?? "", row.phyloP100way ?? "", row.phastCons100way ?? "", row.loftee, row.lofteeFilter, row.lofteeFlags, row.loftee50bp, row.loftee50bpOriginal, row.ptcDistanceFromLastExon ?? "", row.ptcCalcStatus, row.haplotypeFrameStatus ?? "", row.haplotypeFramePartners?.join(",") ?? "", row.haplotypeProteinChange ?? "", row.clinvar, row.clinvarConflictingEvidence ?? "", row.spliceAI ?? "", row.promoterAI ?? "", row.loGoFuncPrediction, row.loGoFuncNeutral ?? "", row.loGoFuncGof ?? "", row.loGoFuncLof ?? "", row.loGoFuncSourceTranscript, row.loGoFuncSourceHgvsp, row.loGoFuncMatch, row.funcVepCti ?? "", row.funcVepCte ?? "", row.funcVepSp ?? "", JSON.stringify(row.predictions ?? {}), row.genotype, row.dp ?? "", row.gq ?? "", row.alleleBalance ?? "", row.carriers?.map((carrier) => { const failures = carrierQcFailures(carrier, row, qcSettings); return `${carrier.sample}:${carrier.evidence.gt}${carrier.evidence.partialCall ? "(partial)" : ""}:DP=${carrier.evidence.dp ?? "."}:GQ=${carrier.evidence.gq ?? "."}:AD=${carrier.evidence.adRef ?? "."},${carrier.evidence.adAlt ?? "."}:AB=${carrier.evidence.alleleBalance?.toFixed(3) ?? "."}:FT=${carrier.evidence.genotypeFilter || "."}:QC=${failures.length ? `fail(${failures.join("|").replace(/[;\t]/g, " ")})` : "pass"}`; }).join(";") ?? "", row.mane, row.picked, row.repeat, row.segdup, ...detectedDbnsfp.flatMap((definition) => [row.dbnsfpPredictors?.[definition.id]?.score ?? "", ...(definition.predictionColumn ? [row.dbnsfpPredictors?.[definition.id]?.prediction ?? ""] : [])])].join("\t"));
  headers.push("AlphaGenomeAVI_phred", "AlphaGenomeAVI_raw");
  const exportedBody = body.map((line, index) => [line,
    rows[index].alphaGenomeAviPhred ?? "", rows[index].alphaGenomeAviRaw ?? "",
  ].join("\t"));
  const url = URL.createObjectURL(new Blob([[researchUseNoticeRow(), headers.join("\t"), ...exportedBody].join("\n")], { type: "text/tab-separated-values" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "iei-prioritized-variants.tsv"; anchor.click(); URL.revokeObjectURL(url);
}

// Exported tables carry the same notice the workbench shows on screen (as a
// leading "#" comment row, so the header row stays the second line).
function researchUseNoticeRow() {
  return `# ${RESEARCH_USE_NOTICE}`;
}

function downloadCohortTsv(rows: CohortQueryRow[]) {
  const headers = ["sample", "variant", "rsid", "original_assembly", "original_chrom", "original_pos", "original_ref", "original_alt", "unscored_indel_reasons", "gene", "HGVSc", "HGVSp", "consequence", "impact", "genotype", "zygosity", "DP", "GQ", "allele_balance", "gnomAD_popmax", "CADD", "AlphaMissense", "SpliceAI", "promoterAI", "LoGoFunc_prediction", "LoGoFunc_neutral", "LoGoFunc_GOF", "LoGoFunc_LOF", "LoGoFunc_source_transcript", "LoGoFunc_source_HGVSp", "LoGoFunc_match", "ClinVar", "ClinVar_conflicting_evidence", "LOFTEE", "LOFTEE_PTC_50BP", "LOFTEE_50BP_original", "PTC_distance_from_last_exon", "PTC_calc_status", "haplotype_frame_status", "haplotype_frame_partners", "haplotype_protein_change", "haplotype_transcript", "MANE", "PICK", "source_vcf"];
  const body = rows.map((row) => [row.sample, row.variant_key, row.rsid ?? "", row.original_assembly ?? "", row.original_chrom ?? "", row.original_pos ?? "", row.original_ref ?? "", row.original_alt ?? "", row.unscored_indel_reasons, row.gene, row.hgvsc, row.hgvsp, row.consequence, row.impact, row.genotype, row.zygosity, row.dp ?? "", row.gq ?? "", row.allele_balance ?? "", row.gnomad_popmax ?? "", row.cadd ?? "", row.alpha_missense ?? "", row.spliceai ?? "", row.promoterai ?? "", row.logofunc_prediction, row.logofunc_neutral ?? "", row.logofunc_gof ?? "", row.logofunc_lof ?? "", row.logofunc_source_transcript, row.logofunc_source_hgvsp, row.logofunc_match, row.clinvar, row.clinvar_conflicting, row.loftee, row.loftee_50bp, row.loftee_50bp_original, row.ptc_distance ?? "", row.ptc_calc_status, row.haplotype_frame_status, row.haplotype_frame_partners, row.haplotype_protein_change, row.haplotype_transcript, row.mane, row.picked, row.source_path].join("\t"));
  const url = URL.createObjectURL(new Blob([[researchUseNoticeRow(), headers.join("\t"), ...body].join("\n")], { type: "text/tab-separated-values" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "iei-cohort-carriers.tsv"; anchor.click(); URL.revokeObjectURL(url);
}

// The database's variant keys are chromosome-normalized (chr1 -> 1, M ->
// MT, uppercase alleles); a source VCF's own spelling must normalize the
// same way or restoration falls back for every chr-prefixed file.
function normalizedVariantKey(row: VariantRow) {
  // Must agree with the service's cohort variant_key (canonical minimal
  // allele), which is what cohort selections and stored-record lookups
  // are matched against.
  return canonicalVariantKey(row.chrom, row.pos, row.ref, row.alt);
}

function cohortRowForReview(row: CohortQueryRow): VariantRow {
  const impact = IMPACTS.includes(row.impact as typeof IMPACTS[number])
    ? row.impact as VariantRow["impact"]
    : "UNKNOWN";
  const genotypeClass = row.zygosity === "homozygous"
    ? "homozygous_alt" as const
    : row.zygosity === "hemizygous"
      ? "hemizygous" as const
      : row.zygosity === "heterozygous"
        ? "heterozygous" as const
        : "other" as const;
  // The cohort DB stores DP and allele balance but not per-allele AD.
  // DP x AB is NOT the ALT depth (the service computes AB against sum(AD),
  // which routinely differs from DP), and a derived integer rendered in the
  // "REF, ALT depth" column is indistinguishable from a measured one. Report
  // the depths as unavailable instead of fabricating them.
  const genotypeEvidence = {
    gt: row.genotype,
    called: true,
    carrier: true,
    dp: row.dp,
    gq: row.gq,
    adRef: null,
    adAlt: null,
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
    liftedFromGrch37: row.original_assembly === "GRCh37",
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
    gnomadPopmaxSource: row.gnomad_popmax_source ?? "",
    cadd: row.cadd,
    alphaMissense: row.alpha_missense,
    alphaPrediction: "",
    loftee: row.loftee,
    lofteeFilter: "",
    lofteeFlags: "",
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
    adRef: null,
    adAlt: null,
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
