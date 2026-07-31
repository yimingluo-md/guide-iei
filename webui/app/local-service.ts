export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";

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
};

export type ServiceCapabilities = {
  service: string;
  version: string;
  platform: string;
  wsl: boolean;
  pipeline_root: string;
  cohort_database: string;
  phenotype_database: string;
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
    config_path: string;
    analysis_scope: "exome" | "whole_genome";
    coding_only: boolean;
    include_filtered: boolean;
    use_clinvar: boolean;
    input_assembly: "GRCh38" | "GRCh37" | "auto";
  };
  annotation_profile: {
    ready: boolean;
    error: string;
    foundations: {
      id: string;
      label: string;
      available: boolean;
      required: boolean;
      version: number | null;
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
      status: "ready" | "required_missing" | "optional_missing";
      setup_mode: "manual" | "download" | "bundled" | "deferred";
      download_id?: string;
      reference_url: string;
      reference_label: string;
      size_hint: string;
      instructions: string[];
    }[];
    dbnsfp_predictors: {
      id: string;
      label: string;
      category: string;
      columns: string[];
      recommended: boolean;
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
  resource_id: "spliceai" | "clinvar" | "liftover";
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted";
  progress: number | null;
  message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  error: string;
  log: string;
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
  min_cadd: number | null;
  genes: string[];
  gene_window_bp: number;
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
  missing_genes: string[];
  preparation_warning: string;
};

export type WgsReviewJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
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
  phase: "queued" | "preparing" | "preparing_index" | "indexing" | "merging" | "complete" | "failed";
  reader_count: number;
  prepared_path: string;
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
  gene: string;
  gene_id: string;
  transcript: string;
  hgvsc: string;
  hgvsp: string;
  consequence: string;
  impact: string;
  gnomad_popmax: number | null;
  cadd: number | null;
  alpha_missense: number | null;
  spliceai: number | null;
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
  source_path: string;
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
  mode: "variant" | "gene";
  total: number;
  limit: number;
  truncated: boolean;
  individuals: number;
  variants: number;
  rows: CohortQueryRow[];
};

export type CohortQuery = {
  mode: "variant" | "gene";
  query?: string;
  gene?: string;
  impacts?: string[];
  max_popmax?: number | null;
  min_cadd?: number | null;
  min_alpha_missense?: number | null;
  min_spliceai?: number | null;
  clinvar_pathogenic_only?: boolean;
  clinvar_conflict_pathogenic_only?: boolean;
  exclude_confirmed_frame_restored?: boolean;
  mane_only?: boolean;
  exclude_repeat?: boolean;
  exclude_segdup?: boolean;
  zygosity?: "all" | "heterozygous" | "homozygous" | "hemizygous";
  limit?: number;
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
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? `Local service returned ${response.status}`);
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

export async function startResourceDownload(resourceId: "spliceai" | "clinvar" | "liftover") {
  return request<ResourceDownloadJob>(
    `/api/resource-downloads/${encodeURIComponent(resourceId)}`,
    { method: "POST", body: "{}" },
  );
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
  const name = disposition.match(/filename="([^"]+)"/i)?.[1] || fallbackName;
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
  const name = disposition.match(/filename="([^"]+)"/i)?.[1] || fallbackName;
  return new File([await response.blob()], name, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

export async function getCohortStats() {
  return request<CohortStats>("/api/cohort/stats");
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
) {
  return request<CohortImportJob>("/api/cohort/import-jobs", {
    method: "POST",
    body: JSON.stringify({
      paths,
      recursive,
      force,
      allow_unknown_assembly: allowUnknownAssembly,
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

export async function getPhenotypeStats() {
  return request<PhenotypeStats>("/api/phenotypes/stats");
}

export async function getPhenotypeIndividuals(query = "") {
  return (await request<{ individuals: PhenotypeIndividual[] }>(
    `/api/phenotypes?query=${encodeURIComponent(query)}`,
  )).individuals;
}

export async function getPhenotypesBySample(sampleId: string) {
  return (await request<{ individuals: PhenotypeIndividual[] }>(
    `/api/phenotypes/by-sample/${encodeURIComponent(sampleId)}`,
  )).individuals;
}

export async function getPhenotypeProfiles() {
  return (await request<{ profiles: PhenotypeProfile[] }>(
    "/api/phenotypes/profiles",
  )).profiles;
}

export async function savePhenotypeIndividual(
  payload: Partial<PhenotypeIndividual> & { individual_id: string },
) {
  return request<PhenotypeIndividual>("/api/phenotypes/individual", {
    method: "POST",
    body: JSON.stringify(payload),
  });
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
