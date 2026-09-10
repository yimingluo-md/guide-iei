export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";

export function downloadFilename(disposition: string, fallbackName: string): string {
  const extended = disposition.match(/filename\*=UTF-8''([^;\s]+)/i)?.[1];
  if (extended) {
    try { return decodeURIComponent(extended); } catch { /* use the legacy fallback */ }
  }
  return disposition.match(/filename="([^"]+)"/i)?.[1] || fallbackName;
}

export type AnnotationJob = {
  id: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  status: JobStatus;
  profile: string;
  analysis_scope: "exome" | "whole_genome";
  input_assembly: "GRCh38" | "GRCh37" | "auto";
  input_path: string;
  output_path: string;
  final_output_path: string | null;
  config_path: string;
  coding_only: boolean;
  include_filtered: boolean;
  use_clinvar: boolean;
  command: string[][];
  log_path: string;
  pid: number | null;
  exit_code: number | null;
  error: string | null;
  progress?: JobProgress;
};

export type JobProgressStage = {
  id: string;
  label: string;
  state: "done" | "active" | "pending";
};

export type JobProgress = {
  stage: string;
  stage_label: string;
  stages: JobProgressStage[];
  resolved_assembly?: "GRCh38" | "GRCh37" | null;
  variants_total: number | null;
  variants_done: number | null;
  vep_percent: number | null;
  eta_seconds: number | null;
  post_processing: boolean;
};

export type ServiceCapabilities = {
  service: string;
  version: string;
  platform: string;
  wsl: boolean;
  pipeline_root: string;
  cohort_database: string;
  phenotype_database: string;
  storage: StorageConfiguration;
  container_runtimes: string[];
  hardware: {
    logical_cpus: number;
    recommended_vep_workers: number;
    max_vep_workers: number;
  };
  profiles: { id: string; label: string; available: boolean }[];
  input_assemblies: {
    id: "GRCh38" | "GRCh37" | "auto";
    label: string;
  }[];
  defaults: {
    output_directory?: string;
    config_path: string;
    analysis_scope: "exome" | "whole_genome";
    coding_only: boolean;
    include_filtered: boolean;
    use_clinvar: boolean;
    input_assembly: "GRCh38" | "GRCh37" | "auto";
  };
  annotation_profile: {
    ready: boolean;
    datasets_ready: boolean;
    execution_ready: boolean;
    error: string;
    foundations: {
      id: string;
      label: string;
      description?: string;
      available: boolean;
      required: boolean;
      version: number | null;
      state?: "ready" | "runtime_missing" | "runtime_starting" | "runtime_unavailable" | "image_missing" | "image_stale";
      message?: string;
    }[];
    sources: {
      id: string;
      label: string;
      description: string;
      enabled: boolean;
      required: boolean;
      available: boolean;
      installed: boolean;
      configured_paths: string[];
      version: string;
      available_in: ("exome" | "whole_genome")[];
      access: "public" | "bundled" | "registration" | "license" | "terms";
      recommendation: "required" | "included" | "recommended" | "recommended_wgs" | "optional";
      status: "ready" | "required_missing" | "optional_missing";
      setup_mode: "manual" | "download" | "prepare" | "bundled" | "deferred";
      download_id?: string;
      prepare_id?: "dbnsfp" | "promoterai" | "logofunc" | "funcvep" | "alphagenome_avi";
      reference_url: string;
      reference_label: string;
      size_hint: string;
      instructions: string[];
    }[];
    recommended_profiles: {
      exome: { installed: boolean; missing: string[] };
      whole_genome: { installed: boolean; missing: string[] };
    };
    dbnsfp_predictors: {
      id: string;
      label: string;
      category: string;
      columns: string[];
      available: boolean;
    }[];
    defaults: {
      fork: number;
    };
  };
};

export type AnnotationOptions = Record<string, boolean | number | string | string[]>;

export type ResourceDownloadJob = {
  id: string;
  resource_id: "dbnsfp" | "spliceai" | "cadd_wgs" | "clinvar" | "liftover" | "promoterai" | "logofunc" | "funcvep" | "alphagenome_avi" | "ccre" | "loftee" | "repeatmasker" | "segdup" | "gene_knowledge" | "clingen_erepo" | "omim" | "genia" | "recommended_exome" | "recommended_wgs" | "refresh_updates";
  operation?: "download" | "preparation" | "installation";
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted";
  progress: number | null;
  message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  error: string;
  log: string;
  files?: string[];
  result?: { counts?: { genes: number; phenotypes: number } };
};

export type GeneKnowledgeResource = {
  id: "hgnc" | "iuis" | "clingen_validity" | "clingen_dosage";
  release: string;
  source_url: string;
  source_sha256: string;
  record_count: number;
  imported_at: string;
};

export type GeniaComponentId =
  | "gei_disease"
  | "disease_catalog"
  | "disease_phenotypes"
  | "phenotype_vocabulary"
  | "variant_vcf";

export type GeniaComponent = {
  id: GeniaComponentId;
  label: string;
  source_name: string;
  source_sha256: string;
  schema_fingerprint: string;
  record_count: number;
  rejected_record_count?: number;
  installed_at: string;
  release_hint: string;
};

export type GeniaStatus = {
  installed: boolean;
  components: Partial<Record<GeniaComponentId, GeniaComponent>>;
  capabilities: {
    gene_disease: boolean;
    phenotype_evidence: boolean;
    phenotype_details: boolean;
    variant_evidence: boolean;
  };
  updated_at?: string;
  license: string;
  error?: string;
};

export type GeneKnowledgeStatus = {
  available: boolean;
  error: string;
  resources: GeneKnowledgeResource[];
  omim: {
    installed: boolean;
    installed_at?: string;
    genes?: number;
    phenotypes?: number;
    license: string;
    error?: string;
  };
  genia: GeniaStatus;
};

export type GeniaVariantRecord = {
  record_id: string;
  short_name: string;
  class_code: string;
  class_label: string;
  relevant_subjects: number | null;
  relevant_subjects_raw: string;
  source_chrom: string;
  source_pos: number;
  source_ref: string;
  source_alt: string;
};

export type GeniaVariant = GeniaStatus & {
  available: boolean;
  records: GeniaVariantRecord[];
};

export type ClinGenErepoAssertion = {
  uuid: string; variation: string; clinvar_variation_id: string; caid: string;
  hgvs_expressions: string; gene: string; disease: string; mondo_id: string;
  mode_of_inheritance: string; assertion: string; evidence_met: string;
  evidence_not_met: string; interpretation_summary: string; pubmed: string;
  expert_panel: string; guideline: string; approval_date: string;
  published_date: string; retracted: string; evidence_repo_link: string;
  mapping_method: string;
};

export type ClinGenErepoVariant = {
  available: boolean; error: string; generated_utc?: string; api_version?: string;
  source_rows?: number; active_rows?: number; mapped_active_rows?: number;
  mapping_rate?: number; alleles?: number; source_sha256?: string;
  assertions: ClinGenErepoAssertion[];
};

export type GeneKnowledgeFilters = {
  iuis_categories: { category: string; genes: number }[];
  iuis_category_genes: Record<string, string[]>;
  omim_genes: string[];
  genia_gei_genes: string[];
};

export type GeneKnowledgeGene = {
  query: string;
  found: boolean;
  identity: null | {
    hgnc_id: string;
    symbol: string;
    name: string;
    ensembl_gene_id: string;
    entrez_id: string;
    locus_type: string;
    status: string;
  };
  aliases: { alias: string; kind: string }[];
  iuis: {
    disease: string; inheritance: string; mechanism: string; omim: string;
    t_cell_count: string; t_cell_summary: string;
    b_cell_count: string; b_cell_summary: string;
    immunoglobulin_levels: string; immunoglobulin_summary: string;
    neutrophil_count: string; neutrophil_summary: string;
    other_affected_cells: string; other_affected_cell_groups: string;
    associated_features: string;
    major_category: string; subcategory: string; source_gene: string;
  }[];
  clingen_validity: {
    disease: string; mondo_id: string; moi: string; sop: string;
    classification: string; report_url: string; classification_date: string; expert_panel: string;
  }[];
  clingen_dosage: null | {
    hi_score: string; hi_description: string; hi_disease_id: string;
    ts_score: string; ts_description: string; ts_disease_id: string;
    date_last_evaluated: string; hi_pmids: string; ts_pmids: string;
  };
  omim: {
    gene_mim: string; phenotype_mim: string; phenotype: string;
    mapping_key: string; inheritance: string; cytoband: string; gene_title: string;
  }[];
  omim_installed: boolean;
  genia: GeniaStatus & {
    relationships: {
      source_id: string; disease_id: string; disease_name: string;
      synonyms: string; gene_symbol: string; moi: string; moa: string;
      publication_year: string; iuis_classification: string;
      curation_status: "curated" | "ongoing" | "not_curated" | "unknown";
      curation_status_raw: string; case_count: string; family_count: string;
      omim_id: string; mondo_id: string; clingen_class: string;
      clingen_review_date?: string; last_updated: string;
      relationship_source: "gei" | "disease_catalog" | "phenotype_export";
      phenotypes: {
        clinical_term_id: string; clinical_term: string;
        hpo_term: string; hpo_id: string; rank: number | null;
        count_yes: number | null; percent_yes: number | null;
        count_no: number | null; percent_no: number | null;
        count_unreported: number | null; percent_unreported: number | null;
        phenotype_description: string; phenotype_alternate_terms: string;
        phenotype_parent_terms: string;
      }[];
    }[];
  };
};

export type SubmitJob = {
  input_path: string;
  output_path: string;
  config_path?: string;
  profile: string;
  analysis_scope: "exome" | "whole_genome";
  input_assembly: "GRCh38" | "GRCh37" | "auto";
  coding_only: boolean;
  include_filtered: boolean;
  use_clinvar: boolean;
  annotation_options?: AnnotationOptions;
};

export type WgsPrefilterOptions = {
  max_gnomad_popmax: number | null;
  min_spliceai: number | null;
  min_promoterai_abs: number | null;
  noncoding_mode: "ccre" | "all" | "none";
};

export type WgsReviewResult = {
  id: string;
  filename: string;
  index_path: string;
  cache_hit: boolean;
  prepared_cache_hit: boolean;
  reader_count: number;
  records_scanned: number;
  records_retained: number;
  annotations_scanned: number;
  annotations_retained: number;
  unscored_intronic_indels: number;
  unscored_promoter_indels: number;
  noncoding_mode: "ccre" | "all" | "none";
  ccre_resource: string;
  preparation_warning: string;
};

export type WgsReviewJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted";
  phase: "queued" | "preparing_index" | "filtering" | "merging" | "compressing" | "indexing_output" | "complete" | "failed";
  progress: number;
  message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  records_scanned: number;
  records_retained: number;
  reader_count: number;
  result: WgsReviewResult | null;
  error: string;
};

export type CcreNearbyGene = {
  symbol: string;
  gene_id: string;
  tss: number;
  strand: "+" | "-";
  biotype: string;
  distance_bp: number;
  absolute_distance_bp: number;
  relative_position: "overlaps TSS" | "upstream of TSS" | "downstream of TSS";
};

export type CcreOverlap = {
  accession: string;
  class: string;
  class_label: string;
  chrom: string;
  start: number;
  end: number;
  length_bp: number;
  nearby_genes: CcreNearbyGene[];
};

export type CcreContext = {
  assembly: "GRCh38";
  resource_version: string;
  resource_available: boolean;
  gene_resource_available: boolean;
  gene_source: string;
  gene_window_bp: number;
  status: "overlap" | "no_overlap" | "resource_unavailable";
  interpretation_caveat: string;
  variant: { chrom: string; start: number; end: number; ref: string; alt: string };
  overlaps: CcreOverlap[];
};

export type ScreenContextState =
  | "classified"
  | "h3k4me3_associated"
  | "mixed"
  | "accessible_only"
  | "accessible_classification_unavailable"
  | "not_detected";

export type ScreenTissueContext = {
  id: string;
  index: number;
  name: string;
  source_filename: string;
  state: ScreenContextState;
  state_label: string;
  activity_detected: boolean;
  class: string;
};

export type ScreenImmuneContext = {
  id: string;
  context_id: string;
  ontology_id: string;
  name: string;
  lineages: string[];
  donor_count: number;
  tier_counts: Record<string, number>;
  assay_capability: Record<string, number>;
  audit_warning_counts: Record<string, number>;
  ancestor_context_ids: string[];
  direct_parent_context_ids: string[];
  descendant_context_ids: string[];
  direct_child_context_ids: string[];
  is_nested_context: boolean;
  is_summary_parent: boolean;
  state: ScreenContextState;
  state_label: string;
  activity_detected: boolean;
  donors_detected: number;
  classification_capable_donors: number;
  exact_class_counts: Record<string, number>;
  profile_calls: {
    donor_accession: string;
    evidence_tier: string;
    assays_available: string[];
    class: string;
    detected: boolean;
  }[];
};

export type ScreenContextCatalog = {
  available: boolean;
  registry: string;
  assembly: "GRCh38";
  created_at?: string;
  message?: string;
  scientific_caveats?: string[];
  tissues: Omit<ScreenTissueContext, "state" | "state_label" | "activity_detected" | "class">[];
  immune_contexts: Omit<ScreenImmuneContext, "state" | "state_label" | "activity_detected" | "donors_detected" | "classification_capable_donors" | "exact_class_counts" | "profile_calls">[];
  presets: { id: string; name: string; tissue_ids: string[]; immune_context_ids: string[] }[];
};

export type ScreenContextEvidence = {
  available: boolean;
  status: "overlap" | "no_overlap" | "resource_unavailable";
  registry?: string;
  assembly?: "GRCh38";
  overlaps: {
    row_index: number;
    accession: string;
    overall_class: string;
    chrom: string;
    start: number;
    end: number;
    tissues: ScreenTissueContext[];
    immune_contexts: ScreenImmuneContext[];
    summary: {
      tissues_detected: number;
      tissues_total: number;
      immune_contexts_detected: number;
      immune_contexts_total: number;
      mixed_immune_contexts: number;
    };
  }[];
  modules?: Record<string, { available: boolean; label: string; note?: string }>;
};

export type StagedAnnotationFile = {
  path: string;
  filename: string;
  relative_path: string;
  bytes: number;
};

export type CohortStats = {
  files: number;
  sample_entries: number;
  individuals: number;
  variants: number;
  carrier_observations: number;
  full_files: number;
  prefiltered_files: number;
};

export type CohortSample = {
  id: number;
  name: string;
  file_id: number;
  source_path: string;
  import_profile: "full" | "prefiltered";
  analysis_scope: "exome" | "whole_genome" | "unknown";
  imported_at: string;
  carrier_observations: number;
  profile_label: string;
  profile_hash: string;
};

export type CohortProfile = {
  profile_hash: string;
  profile_label: string;
  analysis_scope: "exome" | "whole_genome";
  import_profile: "full" | "prefiltered";
  settings: Record<string, unknown>;
  files: number;
  sample_entries: number;
};

export type CohortSampleRemoval = {
  removed: Pick<CohortSample, "id" | "name" | "file_id" | "source_path">[];
  removed_count: number;
  orphan_variants_removed: number;
  stats: CohortStats;
};

export type CohortImportFile = {
  path: string;
  status: "imported" | "unchanged" | "failed";
  sample_count?: number;
  pass_records?: number;
  excluded_records?: number;
  variant_count?: number;
  carrier_count?: number;
  records_processed?: number;
  prepared_path?: string;
  index_path?: string | null;
  import_mode?: "serial_staged" | "parallel_tabix_staged";
  reader_count?: number;
  preparation_warning?: string;
  cache_hit?: boolean;
  import_profile?: "full" | "prefiltered";
  analysis_scope?: "exome" | "whole_genome";
  prefilter_options?: WgsPrefilterOptions | Record<string, never>;
  prefilter_records_scanned?: number;
  prefilter_records_retained?: number;
  error?: string;
};

export type CohortImportResult = {
  files: CohortImportFile[];
  imported: number;
  skipped: number;
  failed: number;
  stats: CohortStats;
};

export type CohortImportJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  total_files: number;
  completed_files: number;
  total_bytes: number;
  processed_bytes: number;
  current_path: string;
  current_file_bytes: number;
  current_file_size: number;
  records_processed: number;
  pass_records: number;
  carrier_count: number;
  phase: "queued" | "preparing" | "preparing_index" | "filtering" | "compressing" | "indexing_output" | "indexing" | "merging" | "complete" | "failed";
  reader_count: number;
  prepared_path: string;
  import_profile: "full" | "prefiltered";
  analysis_scope: "exome" | "whole_genome";
  prefilter_options: WgsPrefilterOptions | Record<string, never>;
  prefilter_records_scanned: number;
  prefilter_records_retained: number;
  result: CohortImportResult | null;
  error: string;
};

export type CohortQueryRow = {
  variant_key: string;
  chrom: string;
  pos: number;
  ref: string;
  alt: string;
  rsid: string | null;
  original_assembly: string | null;
  original_chrom: string | null;
  original_pos: number | null;
  original_ref: string | null;
  original_alt: string | null;
  unscored_indel_reasons: string | null;
  gene: string;
  gene_id: string;
  transcript: string;
  hgvsc: string;
  hgvsp: string;
  consequence: string;
  impact: string;
  gnomad_popmax: number | null;
  gnomad_popmax_source?: "gnomad_popmax" | "max_af" | "gnomad_global" | "legacy_pooled" | "" | null;
  cadd: number | null;
  alpha_missense: number | null;
  spliceai: number | null;
  promoterai: number | null;
  logofunc_prediction: string;
  logofunc_neutral: number | null;
  logofunc_gof: number | null;
  logofunc_lof: number | null;
  logofunc_allele_available: boolean;
  logofunc_source_transcript: string;
  logofunc_source_hgvsp: string;
  logofunc_match: string;
  clinvar: string;
  clinvar_conflicting: string;
  loftee: string;
  loftee_50bp: string;
  loftee_50bp_original: string;
  loftee_50bp_changed: boolean;
  ptc_distance: number | null;
  ptc_calc_status: string;
  mane: boolean;
  picked: boolean;
  repeat_masker: boolean;
  segdup: boolean;
  sample: string;
  sample_entry_id: number;
  source_file_id: number;
  source_path: string;
  import_profile: "full" | "prefiltered";
  analysis_scope: "exome" | "whole_genome" | "unknown";
  profile_label: string;
  profile_hash: string;
  genotype: string;
  zygosity: string;
  phased: boolean;
  dp: number | null;
  gq: number | null;
  allele_balance: number | null;
  qual: number | null;
  haplotype_frame_status: string;
  haplotype_frame_partners: string;
  haplotype_protein_change: string;
  haplotype_transcript: string;
  sample_qualifying_variant_count: number;
};

export type CohortQueryResult = {
  mode: "variant" | "gene" | "gene_list" | "region";
  total: number;
  limit: number;
  truncated: boolean;
  individuals: number;
  variants: number;
  rows: CohortQueryRow[];
  represented_profiles: string[];
  comparability_warning: string;
};

export type CohortVariantAnnotation = Pick<CohortQueryRow,
  "gene" | "gene_id" | "transcript" | "hgvsc" | "hgvsp" |
  "consequence" | "impact" | "gnomad_popmax" | "cadd" |
  "alpha_missense" | "spliceai" | "promoterai" |
  "logofunc_prediction" | "logofunc_neutral" | "logofunc_gof" |
  "logofunc_lof" | "logofunc_allele_available" |
  "logofunc_source_transcript" | "logofunc_source_hgvsp" | "logofunc_match" |
  "clinvar" | "clinvar_conflicting" |
  "loftee" | "loftee_50bp" | "loftee_50bp_original" |
  "loftee_50bp_changed" | "ptc_distance" | "ptc_calc_status" |
  "mane" | "picked" | "repeat_masker" | "segdup"
>;

export type CohortVariantDetail = CohortQueryResult & {
  annotations: CohortVariantAnnotation[];
};

export type CohortReviewSelection = {
  variant_key: string;
  sample_entry_id: number;
  sample: string;
};

export type CohortReviewVcf = {
  source_file_id: number;
  source_path: string;
  prepared_path: string;
  name: string;
  selections: CohortReviewSelection[];
  vcf: string;
};

export type CohortReviewRecords = {
  requested: number;
  resolved: number;
  files: CohortReviewVcf[];
  unresolved: Pick<CohortReviewSelection, "variant_key" | "sample_entry_id">[];
  warnings: string[];
};

export type CohortSampleReviewEntry = {
  sample_entry_id: number;
  sample: string;
  carrier_observations: number;
};

export type CohortSampleReviewVcf = {
  source_file_id: number;
  source_path: string;
  prepared_path: string;
  name: string;
  import_profile: "full" | "prefiltered";
  analysis_scope: "exome" | "whole_genome" | "unknown";
  prefilter_options: WgsPrefilterOptions | Record<string, never>;
  imported_at: string;
  record_count: number;
  samples: CohortSampleReviewEntry[];
  // Served by the service as a streamed file (vcf_url); getCohortSampleReview
  // fetches it so callers still receive the text in `vcf`.
  vcf_url: string;
  vcf_bytes: number;
  vcf: string;
};

export type CohortSampleReview = {
  export_id: string;
  sample_entries: number;
  carrier_observations: number;
  records: number;
  bytes: number;
  analysis_scope: "exome" | "whole_genome";
  files: CohortSampleReviewVcf[];
  warnings: string[];
};

export type CohortQuery = {
  mode: "variant" | "gene" | "gene_list" | "region";
  query?: string;
  gene?: string;
  genes?: string[];
  region?: string;
  impacts?: string[];
  max_popmax?: number | null;
  min_cadd?: number | null;
  min_alpha_missense?: number | null;
  min_spliceai?: number | null;
  logofunc_class?: "GOF" | "LOF" | "Neutral" | "";
  min_logofunc_probability?: number | null;
  clinvar_pathogenic_only?: boolean;
  clinvar_conflict_pathogenic_only?: boolean;
  exclude_confirmed_frame_restored?: boolean;
  mane_only?: boolean;
  exclude_repeat?: boolean;
  exclude_segdup?: boolean;
  zygosity?: "all" | "heterozygous" | "homozygous" | "hemizygous";
  analysis_scopes?: ("exome" | "whole_genome")[];
  profile_hashes?: string[];
  limit?: number;
};

export type SampleLibraryDataset = {
  id: string;
  sample_id: string;
  sample_label: string;
  individual_id: string | null;
  vcf_sample_name: string;
  original_name: string;
  original_path: string;
  managed_path: string;
  managed_index_path: string | null;
  managed_checksum: string;
  managed_size_bytes: number;
  analysis_scope: "exome" | "whole_genome";
  index_scope: "compact" | "full";
  annotation_bundle: Record<string, unknown>;
  resource_versions: Record<string, unknown>;
  qc_settings: Record<string, unknown>;
  prefilter_settings: WgsPrefilterOptions | Record<string, unknown>;
  retention_routes: string[];
  complete_settings: Record<string, unknown>;
  settings_hash: string;
  profile_label: string;
  source_record_count: number | null;
  retained_record_count: number | null;
  include_in_cohort: boolean;
  cohort_file_id: number | null;
  cohort_sample_entry_id: number | null;
  cohort_index_status: "ready" | "not_included" | "needs_repair";
  callset_id: string;
  callset_fingerprint: string;
  version_id: string;
  version_number: number;
  is_current: boolean;
  cohort_preferred: boolean;
  supersedes_version_id: string | null;
  status: string;
  warnings: string[];
  imported_at: string;
  updated_at: string;
};

export type SampleLibraryImportSource = {
  path?: string;
  review_id?: string;
  original_path?: string;
  original_name?: string;
  source_record_count?: number;
  retained_record_count?: number;
  identity_action?: "replace" | "separate";
  replace_callset_id?: string;
};

export type SampleLibraryIdentityMatch = {
  callset_id: string;
  version_id: string;
  version_number: number;
  original_name: string;
  imported_at: string;
  sample_count: number;
  matching_samples: string[];
  matching_sample_count: number;
  same_sample_set: boolean;
};

export type SampleLibraryInspection = {
  status: "new" | "exact_current" | "exact_previous" | "reannotation" | "possible_update";
  source_name: string;
  path: string;
  sample_count: number;
  samples: string[];
  matches: SampleLibraryIdentityMatch[];
  match?: SampleLibraryIdentityMatch;
};

export type SampleLibraryImportResult = {
  imports: {
    datasets: Pick<SampleLibraryDataset, "id" | "sample_id" | "vcf_sample_name" | "individual_id" | "cohort_sample_entry_id">[];
    managed_path: string;
    deduplicated_file: boolean;
    profile_label: string;
    profile_hash: string;
    callset_id: string;
    version_id: string;
    version_number: number;
    import_outcome: "new" | "exact_current" | "exact_previous" | "updated_annotation" | "updated_version" | "separate_dataset";
    warnings: string[];
  }[];
  datasets: Pick<SampleLibraryDataset, "id" | "sample_id" | "vcf_sample_name" | "individual_id" | "cohort_sample_entry_id">[];
};

export async function inspectSampleLibrary(payload: {
  sources: SampleLibraryImportSource[];
  analysis_scope: "exome" | "whole_genome";
}) {
  return (await request<{ inspections: SampleLibraryInspection[] }>(
    "/api/sample-library/inspect",
    { method: "POST", body: JSON.stringify(payload) },
  )).inspections;
}

export type StorageStats = {
  state_dir: string;
  workspace_dir: string;
  locations: Record<string, number>;
  total_bytes: number;
  datasets: number;
  current_datasets: number;
  callsets: number;
  versions: number;
  managed_unique_files: number;
  database_page_bytes: number;
  database_reclaimable_bytes: number;
  annotation_bytes: number;
  storage_configuration: StorageConfiguration;
  storage_error?: string;
};

export type StorageLocationKind = "annotation" | "data" | "temporary";

export type StorageLocation = {
  id: StorageLocationKind;
  path: string;
  exists: boolean;
  writable: boolean;
  available: boolean;
  free_bytes: number | null;
  total_bytes: number | null;
  warning: string;
  uses_default: boolean;
  follows_data_root: boolean;
  active_path: string;
  restart_required: boolean;
  used_bytes: number | null;
  included_with_data: boolean;
  windows_path: string | null;
};

export type StorageConfiguration = {
  registry_path: string;
  locations: StorageLocation[];
  annotation_bytes: number | null;
  active_data_root: string;
  active_annotation_root: string;
  active_temporary_root: string;
};

export type StorageLocationTest = {
  id: StorageLocationKind;
  path: string;
  parent: string;
  exists: boolean;
  empty: boolean;
  free_bytes: number | null;
  total_bytes: number | null;
  warning: string;
  ready: boolean;
  allow_missing: boolean;
};

export type StorageMigrationJob = {
  id: string;
  kind: StorageLocationKind;
  source: string;
  destination: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  bytes_total: number;
  required_free_bytes?: number;
  bytes_copied: number;
  message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string;
  restart_required: boolean;
  original_retained?: boolean;
  staging_path?: string;
};

export type PhenotypeField =
  | "individual_id" | "sample_ids" | "sex_at_birth"
  | "age_at_evaluation" | "age_at_evaluation_unit"
  | "age_at_onset" | "age_at_onset_unit"
  | "reported_race" | "reported_ethnicity"
  | "phenotype_summary" | "present_features" | "absent_features"
  | "current_diagnosis" | "notes" | "source_date";

export type PhenotypeIndividual = {
  individual_id: string;
  sample_ids: string[];
  sex_at_birth: string;
  age_at_evaluation: number | null;
  age_at_evaluation_unit: string;
  age_at_onset: number | null;
  age_at_onset_unit: string;
  reported_race: string[];
  reported_ethnicity: string[];
  phenotype_summary: string;
  present_features: string[];
  absent_features: string[];
  current_diagnosis: string;
  notes: string;
  source_date: string;
  custom_fields: Record<string, string>;
  source_name: string;
  created_at: string;
  updated_at: string;
};

function phenotypeStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (typeof value === "string") {
    return value.split(/[;,\n]/).map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

function normalizePhenotypeIndividual(
  record: Partial<PhenotypeIndividual> | null | undefined,
): PhenotypeIndividual {
  const value = record ?? {};
  const finiteNumber = (item: unknown) => {
    if (item === null || item === undefined || item === "") return null;
    const number = Number(item);
    return Number.isFinite(number) ? number : null;
  };
  const text = (item: unknown) => item === null || item === undefined ? "" : String(item);
  return {
    individual_id: text(value.individual_id),
    sample_ids: phenotypeStringList(value.sample_ids),
    sex_at_birth: text(value.sex_at_birth),
    age_at_evaluation: finiteNumber(value.age_at_evaluation),
    age_at_evaluation_unit: text(value.age_at_evaluation_unit),
    age_at_onset: finiteNumber(value.age_at_onset),
    age_at_onset_unit: text(value.age_at_onset_unit),
    reported_race: phenotypeStringList(value.reported_race),
    reported_ethnicity: phenotypeStringList(value.reported_ethnicity),
    phenotype_summary: text(value.phenotype_summary),
    present_features: phenotypeStringList(value.present_features),
    absent_features: phenotypeStringList(value.absent_features),
    current_diagnosis: text(value.current_diagnosis),
    notes: text(value.notes),
    source_date: text(value.source_date),
    custom_fields: value.custom_fields && typeof value.custom_fields === "object" && !Array.isArray(value.custom_fields)
      ? value.custom_fields : {},
    source_name: text(value.source_name),
    created_at: text(value.created_at),
    updated_at: text(value.updated_at),
  };
}

export type PhenotypeStats = {
  individuals: number;
  sample_links: number;
  matched_samples: number;
  imports: number;
};

export type PhenotypePreview = {
  sheet_names: string[];
  selected_sheet: string | null;
  suggested_header_row: number;
  header_row: number;
  columns: string[];
  suggested_mapping: Partial<Record<PhenotypeField, string>>;
  row_count: number;
  rows: Record<string, string>[];
};

export type PhenotypeValidation = {
  row_count: number;
  valid_individuals: number;
  duplicate_individual_ids: string[];
  duplicate_individual_rows: Record<string, number[]>;
  matched_sample_ids: string[];
  unmatched_sample_ids: string[];
  ambiguous_sample_ids: Record<string, string[]>;
  case_insensitive_suggestions: Record<string, string>;
  preview: Partial<PhenotypeIndividual>[];
};

export type PhenotypeProfile = {
  name: string;
  mapping: Partial<Record<PhenotypeField, string>>;
  sheet_name: string | null;
  header_row: number;
  updated_at: string;
};

export type PhenotypeUploadPayload = {
  filename: string;
  content_base64: string;
  sheet?: string | null;
  header_row?: number;
  mapping?: Partial<Record<PhenotypeField, string>>;
  preserve_unmapped?: boolean;
  update_mode?: "update_nonblank" | "replace" | "skip_existing";
  profile_name?: string;
};

const SERVICE_URL = process.env.NEXT_PUBLIC_IEI_SERVICE_URL ?? "http://127.0.0.1:43117";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${SERVICE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  // Never let a JSON parse error mask the HTTP status: when the service is
  // down or a proxy answers with an HTML error page, the user should see
  // "Local service returned 502", not "Unexpected token '<'".
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "error" in payload
        ? String((payload as { error?: unknown }).error ?? "")
        : "";
    throw new Error(message || `Local service returned ${response.status}`);
  }
  if (payload === null) {
    throw new Error(`Local service returned a non-JSON response for ${path}`);
  }
  return payload as T;
}

export async function getCapabilities() {
  return request<ServiceCapabilities>("/api/capabilities");
}

export async function getJobs() {
  return (await request<{ jobs: AnnotationJob[] }>("/api/jobs")).jobs;
}

export async function getResourceDownloads() {
  return (await request<{ jobs: ResourceDownloadJob[] }>("/api/resource-downloads")).jobs;
}

export async function getGeneKnowledgeStatus() {
  return request<GeneKnowledgeStatus>("/api/gene-knowledge/status");
}

export async function getGeneKnowledgeFilters() {
  return request<GeneKnowledgeFilters>("/api/gene-knowledge/filters");
}

export async function getGeneKnowledgeGene(identifier: string) {
  return request<GeneKnowledgeGene>(`/api/gene-knowledge/gene/${encodeURIComponent(identifier)}`);
}

export async function installOmimGeneKnowledge(sourceDir: string) {
  return request<{ installed: boolean; counts: { genes: number; phenotypes: number }; path: string }>(
    "/api/gene-knowledge/omim/install",
    { method: "POST", body: JSON.stringify({ source_dir: sourceDir }) },
  );
}

export async function startOmimGeneKnowledgeDownload(linkBlock: string) {
  return request<ResourceDownloadJob>("/api/gene-knowledge/omim/download", {
    method: "POST",
    body: JSON.stringify({ link_block: linkBlock }),
  });
}

export async function startResourceDownload(resourceId: ResourceDownloadJob["resource_id"]) {
  return request<ResourceDownloadJob>(
    `/api/resource-downloads/${encodeURIComponent(resourceId)}`,
    { method: "POST", body: "{}" },
  );
}

export type LocalResourceSelection = {
  cancelled: boolean;
  resource_id:
    | "dbnsfp"
    | "promoterai"
    | "logofunc"
    | "funcvep"
    | "alphagenome_avi"
    | "omim"
    | "genia"
    | "storage_annotation"
    | "storage_data"
    | "storage_temporary";
  selection_type?: "file" | "files" | "folder";
  path?: string;
  name?: string;
  paths?: string[];
  names?: string[];
  detected?: { role: GeniaComponentId; label: string; name: string; path: string }[];
};

export async function chooseLocalResourceSource(
  resourceId: LocalResourceSelection["resource_id"],
) {
  return request<LocalResourceSelection>("/api/local-resource-source/choose", {
    method: "POST",
    body: JSON.stringify({ resource_id: resourceId }),
  });
}

export async function getClinGenErepoVariant(chrom: string, pos: number, ref: string, alt: string) {
  const query = new URLSearchParams({ chrom, pos: String(pos), ref, alt });
  return request<ClinGenErepoVariant>(`/api/clingen-erepo/variant?${query.toString()}`);
}

export async function getGeniaVariant(chrom: string, pos: number, ref: string, alt: string) {
  const query = new URLSearchParams({ chrom, pos: String(pos), ref, alt });
  return request<GeniaVariant>(`/api/genia/variant?${query.toString()}`);
}

export async function inspectGeniaFiles(paths: string[]) {
  return request<{ files: NonNullable<LocalResourceSelection["detected"]>; selected_components: GeniaComponentId[] }>(
    "/api/gene-knowledge/genia/inspect",
    { method: "POST", body: JSON.stringify({ paths }) },
  );
}

export async function installGeniaFiles(
  paths: string[],
  replaceUnreadable = false,
) {
  return request<GeniaStatus & { updated_components: GeniaComponentId[]; warnings: string[] }>(
    "/api/gene-knowledge/genia/install",
    {
      method: "POST",
      body: JSON.stringify({
        paths,
        replace_unreadable: replaceUnreadable,
      }),
    },
  );
}

export async function startPromoterAiPreparation(sourceDir: string) {
  return request<ResourceDownloadJob>("/api/resource-preparations/promoterai", {
    method: "POST",
    body: JSON.stringify({ source_dir: sourceDir }),
  });
}

export async function startDbnsfpDownload(downloadUrl: string) {
  return request<ResourceDownloadJob>("/api/resource-preparations/dbnsfp", {
    method: "POST",
    body: JSON.stringify({ download_url: downloadUrl }),
  });
}

export async function startLoGoFuncPreparation(sourcePath: string) {
  return request<ResourceDownloadJob>("/api/resource-preparations/logofunc", {
    method: "POST",
    body: JSON.stringify({ source_path: sourcePath }),
  });
}

export async function startFuncVepPreparation(
  sourcePath: string,
  licenseAccepted: boolean,
) {
  return request<ResourceDownloadJob>("/api/resource-preparations/funcvep", {
    method: "POST",
    body: JSON.stringify({
      source_path: sourcePath,
      license_accepted: licenseAccepted,
    }),
  });
}

export async function submitJob(payload: SubmitJob) {
  return request<AnnotationJob>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function stageAnnotationFile(file: File, batchId: string) {
  const response = await fetch(`${SERVICE_URL}/api/annotation-files/stage`, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-File-Name": encodeURIComponent(file.name),
      "X-Relative-Path": encodeURIComponent(file.webkitRelativePath || file.name),
      "X-Upload-Batch": batchId,
    },
    body: file,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error ?? `Local service returned ${response.status}`);
  }
  return payload as StagedAnnotationFile;
}

export async function cancelJob(jobId: string) {
  return request<AnnotationJob>(`/api/jobs/${jobId}/cancel`, { method: "POST", body: "{}" });
}

export async function getJobLog(jobId: string) {
  return (await request<{ job_id: string; log: string }>(`/api/jobs/${jobId}/log`)).log;
}

export async function openJobReviewFile(jobId: string, fallbackName: string) {
  const response = await fetch(`${SERVICE_URL}/api/jobs/${encodeURIComponent(jobId)}/review-file`);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error ?? `Local service returned ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const name = downloadFilename(disposition, fallbackName);
  return new File([await response.blob()], name, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

export async function prefilterWgsReview(
  path: string,
  filters: WgsPrefilterOptions,
) {
  return request<WgsReviewJob>("/api/wgs-review", {
    method: "POST",
    body: JSON.stringify({ path, filters }),
  });
}

export async function getCcreContext(
  variant: { chrom: string; pos: number; ref: string; alt: string },
) {
  return request<CcreContext>("/api/ccre-context", {
    method: "POST",
    body: JSON.stringify(variant),
  });
}

export interface SpliceAiLookupResult {
  variant: string;
  distance: number;
  masked: boolean;
  retrieved_at: string;
  cached: boolean;
  source: string;
  transcripts: {
    gene: string;
    transcript: string;
    refseq: string | null;
    mane_select: boolean;
    strand: string;
    scores: Record<
      "acceptor_gain" | "acceptor_loss" | "donor_gain" | "donor_loss",
      { delta: string | null; position: number | null }
    >;
  }[];
}

/** Explicit per-variant online lookup — the one deliberate network exception. */
export async function spliceAiLookup(
  variant: { chrom: string; pos: number; ref: string; alt: string },
) {
  return request<SpliceAiLookupResult>("/api/spliceai-lookup", {
    method: "POST",
    body: JSON.stringify(variant),
  });
}

export async function getScreenContextCatalog() {
  return request<ScreenContextCatalog>("/api/screen-context/catalog");
}

export async function installScreenContext(manifestPath: string) {
  return request<ScreenContextCatalog & { manifest_path: string }>(
    "/api/screen-context/install",
    { method: "POST", body: JSON.stringify({ manifest_path: manifestPath }) },
  );
}

export async function getScreenContext(
  variant: { chrom: string; pos: number; ref: string; alt: string },
) {
  return request<ScreenContextEvidence>("/api/screen-context", {
    method: "POST",
    body: JSON.stringify(variant),
  });
}

export async function filterScreenContext(payload: {
  variants: { key: string; chrom: string; pos: number; ref: string; alt: string }[];
  tissue_ids: string[];
  immune_context_ids: string[];
  mode: "any" | "all";
}) {
  return request<{ available: boolean; matching_keys: string[]; tested: number }>(
    "/api/screen-context/filter",
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function getWgsReviewJob(jobId: string) {
  return request<WgsReviewJob>(`/api/wgs-review/${encodeURIComponent(jobId)}`);
}

export async function openWgsReviewFile(reviewId: string, fallbackName: string) {
  const response = await fetch(
    `${SERVICE_URL}/api/wgs-review/${encodeURIComponent(reviewId)}/file`,
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error ?? `Local service returned ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const name = downloadFilename(disposition, fallbackName);
  return new File([await response.blob()], name, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

export async function getCohortStats() {
  return request<CohortStats>("/api/cohort/stats");
}

export async function getCohortProfiles() {
  return (await request<{ profiles: CohortProfile[] }>("/api/cohort/profiles")).profiles;
}

export async function importCohort(
  paths: string[],
  recursive = true,
  force = false,
  allowUnknownAssembly = false,
) {
  return request<CohortImportResult>("/api/cohort/import", {
    method: "POST",
    body: JSON.stringify({
      paths,
      recursive,
      force,
      allow_unknown_assembly: allowUnknownAssembly,
    }),
  });
}

export async function startCohortImport(
  paths: string[],
  recursive = true,
  force = false,
  allowUnknownAssembly = false,
  importProfile: "full" | "prefiltered" = "full",
  analysisScope: "exome" | "whole_genome" = "exome",
  filters?: WgsPrefilterOptions,
) {
  return request<CohortImportJob>("/api/cohort/import-jobs", {
    method: "POST",
    body: JSON.stringify({
      paths,
      recursive,
      force,
      allow_unknown_assembly: allowUnknownAssembly,
      import_profile: importProfile,
      analysis_scope: importProfile === "prefiltered" ? "whole_genome" : analysisScope,
      filters,
    }),
  });
}

export async function getCohortImportJob(jobId: string) {
  return request<CohortImportJob>(`/api/cohort/import-jobs/${encodeURIComponent(jobId)}`);
}

export async function queryCohort(payload: CohortQuery) {
  return request<CohortQueryResult>("/api/cohort/query", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getCohortVariantDetail(variantKey: string) {
  return request<CohortVariantDetail>("/api/cohort/variant-detail", {
    method: "POST",
    body: JSON.stringify({ variant_key: variantKey }),
  });
}

export async function getCohortReviewRecords(
  selections: Pick<CohortReviewSelection, "variant_key" | "sample_entry_id">[],
) {
  return request<CohortReviewRecords>("/api/cohort/review-records", {
    method: "POST",
    body: JSON.stringify({ selections }),
  });
}

export async function getCohortSampleReview(sampleIds: number[]) {
  const review = await request<CohortSampleReview>("/api/cohort/sample-review", {
    method: "POST",
    body: JSON.stringify({ sample_ids: sampleIds }),
  });
  // The projected VCFs are written to disk by the service and streamed one
  // file at a time (audit M32): the JSON above carries only their URLs.
  const files: CohortSampleReviewVcf[] = [];
  for (const file of review.files) {
    const response = await fetch(`${SERVICE_URL}${file.vcf_url}`);
    if (!response.ok) {
      const payload: unknown = await response.json().catch(() => null);
      const message =
        payload && typeof payload === "object" && "error" in payload
          ? String((payload as { error?: unknown }).error ?? "")
          : "";
      throw new Error(message || `Local service returned ${response.status} for ${file.name}`);
    }
    files.push({ ...file, vcf: await response.text() });
  }
  return { ...review, files };
}

// Default to the server's cap (5000): a smaller client limit truncated
// silently with no flag on either side, so a large cohort looked complete.
export async function getCohortSamples(query = "", limit = 5000) {
  const params = new URLSearchParams({ query, limit: String(limit) });
  return (await request<{ samples: CohortSample[] }>(
    `/api/cohort/samples?${params.toString()}`,
  )).samples;
}

export async function removeCohortSamples(sampleIds: number[]) {
  return request<CohortSampleRemoval>("/api/cohort/samples/remove", {
    method: "POST",
    body: JSON.stringify({ sample_ids: sampleIds }),
  });
}

export async function getSampleLibrary(query = "") {
  // Server cap; see getCohortSamples on why the client must not ask for less.
  const params = new URLSearchParams({ query, limit: "5000" });
  return (await request<{ datasets: SampleLibraryDataset[] }>(
    `/api/sample-library?${params.toString()}`,
  )).datasets;
}

export async function getSampleLibraryPhenotype(datasetId: string) {
  const phenotype = (await request<{ phenotype: PhenotypeIndividual | null }>(
    `/api/sample-library/${encodeURIComponent(datasetId)}/phenotype`,
  )).phenotype;
  return phenotype ? normalizePhenotypeIndividual(phenotype) : null;
}

export async function importSampleLibrary(payload: {
  sources: SampleLibraryImportSource[];
  analysis_scope: "exome" | "whole_genome";
  index_scope?: "compact" | "full";
  include_in_cohort: boolean;
  qc_settings?: Record<string, unknown>;
  prefilter_settings?: WgsPrefilterOptions | Record<string, unknown>;
  retention_routes?: string[];
  annotation_bundle?: Record<string, unknown>;
  resource_versions?: Record<string, unknown>;
}) {
  return request<SampleLibraryImportResult>("/api/sample-library/import", {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function activateSampleLibraryVersion(datasetId: string) {
  return request<{ datasets: SampleLibraryDataset[]; warning: string; already_current: boolean }>(
    `/api/sample-library/${encodeURIComponent(datasetId)}/activate-version`,
    { method: "POST", body: "{}" },
  );
}

export type BulkIntakeJob = {
  id: string;
  status: "queued" | "running" | "completed" | "cancelled" | "failed";
  created_at: string;
  updated_at: string;
  options: { analysis_scope: "exome" | "whole_genome"; filters: Record<string, unknown>; include_in_cohort: boolean };
  total: number;
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  skipped: number;
  datasets: number;
  current_path: string | null;
  failures: { path: string; error: string }[];
};

export async function startBulkIntake(payload: {
  paths: string[];
  analysis_scope: "exome" | "whole_genome";
  filters?: Record<string, unknown>;
  include_in_cohort?: boolean;
  recursive?: boolean;
}) {
  return request<{ job: BulkIntakeJob }>("/api/bulk-intake", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getBulkIntake() {
  return request<{ job: BulkIntakeJob | null }>("/api/bulk-intake");
}

export async function cancelBulkIntake(jobId: string) {
  return request<{ job: BulkIntakeJob }>(
    `/api/bulk-intake/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
  );
}

export type SampleLibraryBulkReport = {
  action: string;
  requested: number;
  succeeded: number;
  skipped: number;
  failures: { id: string; error: string }[];
};

export async function bulkSampleLibraryAction(
  datasetIds: string[],
  action: "remove" | "cohort_add",
) {
  return request<SampleLibraryBulkReport>("/api/sample-library/bulk", {
    method: "POST",
    body: JSON.stringify({ dataset_ids: datasetIds, action }),
  });
}

export async function openSampleLibraryReviewSelection(
  datasetIds: string[],
  fallbackName: string,
) {
  const response = await fetch(`${SERVICE_URL}/api/sample-library/review-file`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset_ids: datasetIds }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error ?? `Local service returned ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const name = downloadFilename(disposition, fallbackName);
  return new File([await response.blob()], name, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

export async function getSampleLibraryReviewRecord(
  datasetId: string,
  variantKey: string,
) {
  return request<{
    variant_key: string;
    sample: string;
    name: string;
    vcf: string;
  }>("/api/sample-library/review-record", {
    method: "POST",
    body: JSON.stringify({ dataset_id: datasetId, variant_key: variantKey }),
  });
}

export async function openSampleLibraryFile(datasetId: string, fallbackName: string) {
  const response = await fetch(`${SERVICE_URL}/api/sample-library/${encodeURIComponent(datasetId)}/file`);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error ?? `Local service returned ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const name = downloadFilename(disposition, fallbackName);
  return new File([await response.blob()], name, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

export async function mapSampleLibraryIdentity(
  datasetId: string,
  payload: { mode: "existing" | "create" | "unavailable"; individual_id?: string },
) {
  return request<SampleLibraryDataset>(`/api/sample-library/${encodeURIComponent(datasetId)}/identity`, {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function updateSampleLibraryMetadata(
  datasetId: string,
  payload: { sample_label?: string },
) {
  return request<SampleLibraryDataset>(`/api/sample-library/${encodeURIComponent(datasetId)}/metadata`, {
    method: "POST", body: JSON.stringify(payload),
  });
}

export async function reindexSampleLibraryDataset(datasetId: string, fullWgs = false) {
  return request<{ dataset: SampleLibraryDataset; cohort: CohortImportFile }>(
    `/api/sample-library/${encodeURIComponent(datasetId)}/reindex`,
    { method: "POST", body: JSON.stringify({ full_wgs: fullWgs }) },
  );
}

export async function removeSampleLibraryDatasetFromCohort(datasetId: string) {
  return request<SampleLibraryDataset>(
    `/api/sample-library/${encodeURIComponent(datasetId)}/cohort/remove`,
    { method: "POST", body: "{}" },
  );
}

export async function removeSampleLibraryDataset(datasetId: string, removeManagedFile = true) {
  return request<{ removed_dataset: string; removed_files: string[] }>(
    `/api/sample-library/${encodeURIComponent(datasetId)}/remove`,
    { method: "POST", body: JSON.stringify({ remove_managed_file: removeManagedFile }) },
  );
}

export async function getStorageStats() {
  return request<StorageStats>("/api/storage");
}

export async function getStorageConfiguration() {
  return request<StorageConfiguration>("/api/storage/locations");
}

export async function getStorageMigrations() {
  return (await request<{ jobs: StorageMigrationJob[] }>("/api/storage/migrations")).jobs;
}

export async function testStorageLocation(kind: StorageLocationKind, path: string) {
  return request<StorageLocationTest>("/api/storage/test-location", {
    method: "POST", body: JSON.stringify({ kind, path }),
  });
}

export async function setStorageLocation(payload: {
  kind: StorageLocationKind;
  path?: string;
  follow_data_root?: boolean;
}) {
  return request<{ storage: StorageConfiguration; restart_required: boolean; message: string }>(
    "/api/storage/location", { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function getServiceHealth() {
  return request<{ ok: boolean; version: string }>("/api/health");
}

export async function restartWorkbenchService() {
  return request<{ restarting: boolean; message: string }>("/api/service/restart", {
    method: "POST",
    body: "{}",
  });
}

export type SoftwareUpdateStatus = {
  desktop_app?: boolean;
  release_page?: string;
  current_version: string;
  repo: string;
  rollback_available: boolean;
  rollback_version: string | null;
  incomplete_update: boolean;
  restart_pending: boolean;
  /** An in-app restart cannot finish the installed update: the interface
   * must be rebuilt or its dependencies reinstalled by a full relaunch. */
  full_relaunch_required?: boolean;
};

export type SoftwareUpdateCheck = {
  desktop_app?: boolean;
  release_page?: string;
  ok: boolean;
  current_version: string;
  repo: string;
  latest_version?: string;
  tag?: string;
  published_at?: string | null;
  notes?: string;
  update_available?: boolean;
  incomplete_update?: boolean;
  error?: string;
};

export type SoftwareUpdateResult = {
  ok: boolean;
  installed_version?: string;
  previous_version?: string;
  restored_version?: string;
  restart_required: boolean;
  config_review_needed?: string[];
  dependencies_changed?: boolean;
  /** webui/ files changed: only a full relaunch rebuilds the interface. */
  web_build_required?: boolean;
  container_changed?: boolean;
  files_removed?: string[];
  repaired?: boolean;
  wsl_origin_synced?: boolean | null;
};

export async function getSoftwareUpdateStatus() {
  return request<SoftwareUpdateStatus>("/api/software-update/status");
}

export async function checkForSoftwareUpdate() {
  // POST: the outbound release lookup must be impossible to trigger from
  // another site's no-cors GET.
  return request<SoftwareUpdateCheck>("/api/software-update/check", {
    method: "POST",
    body: "{}",
  });
}

export async function installSoftwareUpdate() {
  return request<SoftwareUpdateResult>("/api/software-update/install", {
    method: "POST",
    body: "{}",
  });
}

export async function rollbackSoftwareUpdate() {
  return request<SoftwareUpdateResult>("/api/software-update/rollback", {
    method: "POST",
    body: "{}",
  });
}

export async function startStorageMigration(kind: StorageLocationKind, path: string) {
  return request<StorageMigrationJob>("/api/storage/migrate", {
    method: "POST", body: JSON.stringify({ kind, path }),
  });
}

export async function openStorageLocation(kind: StorageLocationKind) {
  return request<{ opened: string }>("/api/storage/open", {
    method: "POST", body: JSON.stringify({ kind }),
  });
}

export async function cleanupStorage(categories: string[]) {
  return request<{ removed_files: number; freed_bytes: number; storage: StorageStats }>(
    "/api/storage/cleanup", { method: "POST", body: JSON.stringify({ categories }) },
  );
}

export async function compactStorage() {
  return request<StorageStats>("/api/storage/compact", {
    method: "POST", body: JSON.stringify({ confirmation: "COMPACT" }),
  });
}

export async function getPhenotypeStats() {
  return request<PhenotypeStats>("/api/phenotypes/stats");
}

export async function getPhenotypeIndividuals(query = "") {
  const individuals = (await request<{ individuals: PhenotypeIndividual[] }>(
    `/api/phenotypes?query=${encodeURIComponent(query)}`,
  )).individuals;
  return individuals.map(normalizePhenotypeIndividual);
}

export async function getPhenotypesBySample(sampleId: string) {
  const individuals = (await request<{ individuals: PhenotypeIndividual[] }>(
    `/api/phenotypes/by-sample/${encodeURIComponent(sampleId)}`,
  )).individuals;
  return individuals.map(normalizePhenotypeIndividual);
}

export async function getPhenotypeProfiles() {
  return (await request<{ profiles: PhenotypeProfile[] }>(
    "/api/phenotypes/profiles",
  )).profiles;
}

export async function savePhenotypeIndividual(
  payload: Partial<PhenotypeIndividual> & { individual_id: string },
) {
  const individual = await request<PhenotypeIndividual>("/api/phenotypes/individual", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return normalizePhenotypeIndividual(individual);
}

export async function previewPhenotypeInput(payload: PhenotypeUploadPayload) {
  return request<PhenotypePreview>("/api/phenotypes/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function validatePhenotypeInput(payload: PhenotypeUploadPayload) {
  return request<PhenotypeValidation>("/api/phenotypes/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function importPhenotypeInput(payload: PhenotypeUploadPayload) {
  return request<{
    import_id: string;
    created: number;
    updated: number;
    skipped: number;
    row_count: number;
    stats: PhenotypeStats;
  }>("/api/phenotypes/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
