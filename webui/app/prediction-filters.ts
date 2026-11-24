import registry from "../../config/predictor-registry.json";
import { gpnConstraintQ } from "./gpn-star-rank";
import type { VariantRow } from "./vcf";

type Value = string | number | boolean | null | undefined;
type Metric = {
  id: string;
  label: string;
  numeric: boolean;
  fields: string[];
  suggestedMinimum?: string;
  read: (row: VariantRow) => Value;
};
export type ReviewMetric = Metric & { count: number; categories: string[] };
export type Operator = "gte" | "lte" | "between" | "absolute" | "oneOf" | "present" | "missing";
export type PredictionRule = {
  id: string; metric: string; operator: Operator; first: string; second: string; choices: string[];
};

// These review values already resolve multiple providers or summarize component scores.
const projections: Metric[] = [
  { id: "alphamissense.score", label: "AlphaMissense", numeric: true, fields: ["am_pathogenicity"], read: r => r.alphaMissense },
  { id: "alphamissense.prediction", label: "AlphaMissense · prediction", numeric: false, fields: ["am_class"], read: r => r.alphaPrediction },
  { id: "cadd.phred", label: "CADD phred", numeric: true, fields: [], read: r => r.cadd },
  { id: "cadd.raw", label: "CADD raw", numeric: true, fields: [], read: r => r.caddRaw },
  { id: "spliceai.max", label: "SpliceAI max", numeric: true, fields: ["SpliceAI", "DS_AG", "DS_AL", "DS_DG", "DS_DL"], read: r => r.spliceAI },
  { id: "alphagenome_avi.phred", label: "AlphaGenome AVI · Phred", numeric: true, fields: [], read: r => r.alphaGenomeAviPhred },
  { id: "alphagenome_avi.raw", label: "AlphaGenome AVI · raw", numeric: true, fields: [], read: r => r.alphaGenomeAviRaw },
];

// The compact Cohort Search fallback predates normalized observations. Its
// promoter score is already match-checked by the store. LoGoFunc carries
// the source-transcript match alongside the individual score columns.
const summaryFields: Record<string, (row: VariantRow) => Value> = {
  "promoterai.score": row => row.promoterAI,
  "loftee.classification": row => row.loftee,
  "loftee.filter": row => row.lofteeFilter,
  "loftee_ptc_50bp.rule_changed": row => row.loftee50bpChanged,
  "loftee_ptc_50bp.ptc_rule": row => row.loftee50bp,
  "loftee_ptc_50bp.ptc_distance": row => row.ptcDistanceFromLastExon,
  "logofunc.prediction": row => row.loGoFuncPrediction,
  "logofunc.neutral": row => row.loGoFuncNeutral,
  "logofunc.gof": row => row.loGoFuncGof,
  "logofunc.lof": row => row.loGoFuncLof,
};
const summaryMetrics: Metric[] = [];
const allMetrics = new Map(projections.map(m => [m.id, m]));
const observationMetrics = new Map<string, Metric[]>();
const displayNames: Record<string, string> = {
  llr: "calibrated LLR", abs_llr: "calibrated absLLR", cti: "CTI", cte: "CTE", sp: "SP",
  gof: "GOF probability", lof: "LOF probability", neutral: "neutral probability",
};

for (const predictor of registry.predictors) {
  if (["Clinical evidence", "Regional context", "Haplotype consequence"].includes(predictor.category)) continue;
  for (const metric of predictor.metrics) {
    if (!metric.filterable || metric.value_type === "text") continue;
    let id = predictor.id + "." + metric.id;
    if (predictor.id === "spliceai") id = "spliceai.max";
    if (predictor.id === "starter_alphamissense") id = "alphamissense." + metric.id;
    if (["starter_cadd", "cadd_coding", "cadd_wgs"].includes(predictor.id)) id = "cadd." + metric.id;
    const existing = allMetrics.get(id);
    if (existing) {
      existing.fields.push(metric.field);
      continue;
    }
    const entry: Metric = {
      id, label: predictor.label + " · " + (displayNames[metric.id] ?? metric.id.replaceAll("_", " ")),
      numeric: metric.value_type === "float" || metric.value_type === "integer", fields: [metric.field],
      read(row) {
        if (!row.predictions && summaryFields[id]) {
          if (predictor.id === "logofunc" && !["allele_transcript_protein", "source_transcript_match_elsewhere"].includes(row.loGoFuncMatch)) return undefined;
          return summaryFields[id](row);
        }
        const observation = row.predictions?.[predictor.id];
        return observation?.matchStatus === "exact" ? observation.values[metric.id] : undefined;
      },
    };
    allMetrics.set(id, entry);
    if (summaryFields[id]) summaryMetrics.push(entry);
    const group = observationMetrics.get(predictor.id) ?? [];
    group.push(entry);
    const model = predictor.id;
    if (metric.id === "llr" && (model === "gpn_star_m" || model === "gpn_star_v")) {
      const rank: Metric = {
        id: model + ".constraint_q", label: predictor.label + " phred",
        numeric: true, fields: [], suggestedMinimum: "20", read: row => gpnConstraintQ(row, model),
      };
      allMetrics.set(rank.id, rank);
      group.push(rank);
    }
    observationMetrics.set(predictor.id, group);
  }
}

function categoryValues(metric: Metric, value: string | boolean) {
  // VEP emits labels such as deleterious(0.02). dbNSFP may supply several labels.
  const separator = metric.id === "loftee.filter" ? /\s+\/\s+|[,&|]/ : /\s+\/\s+|[&|]/;
  return String(value).split(separator)
    .map(label => label.trim().replace(/\([-+\d.eE]+\)$/, ""))
    .filter(label => label && label !== "." && label !== "-");
}

export function reviewMetrics(rows: VariantRow[], declaredFields: readonly string[] = []): ReviewMetric[] {
  const stats = new Map<Metric, { count: number; categories: Set<string> }>();
  const declared = new Set(declaredFields);
  for (const metric of allMetrics.values()) {
    if (metric.fields.some(field => declared.has(field))) stats.set(metric, { count: 0, categories: new Set() });
  }
  function observe(metric: Metric, row: VariantRow) {
    const value = metric.read(row);
    if (value == null || (metric.numeric ? !Number.isFinite(value) : typeof value === "number")) return;
    const labels = typeof value === "number" ? null : categoryValues(metric, value);
    if (labels?.length === 0) return;
    let found = stats.get(metric);
    if (!found) {
      found = { count: 0, categories: new Set() };
      stats.set(metric, found);
    }
    found.count++;
    labels?.forEach(label => found.categories.add(label));
  }
  for (const row of rows) {
    for (const metric of projections) observe(metric, row);
    if (!row.predictions) for (const metric of summaryMetrics) observe(metric, row);
    for (const predictor in row.predictions) {
      const metrics = observationMetrics.get(predictor);
      if (metrics) for (const metric of metrics) observe(metric, row);
    }
  }
  return [...stats].map(([metric, { count, categories }]) => ({
    ...metric, count, categories: [...categories].sort(),
  })).sort((a, b) => a.label.localeCompare(b.label));
}

function numericInput(text: string) {
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text.trim())) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export function compileRule(rule: PredictionRule, metric: Metric): ((row: VariantRow) => boolean) | null {
  const op = rule.operator;
  if (op === "missing" || op === "present") return row => {
    const value = metric.read(row);
    const present = typeof value === "number" ? Number.isFinite(value) : value != null && categoryValues(metric, value).length > 0;
    return op === "present" ? present : !present;
  };
  if (op === "oneOf") {
    if (!rule.choices.length || metric.numeric) return null;
    const selected = new Set(rule.choices);
    return row => {
      const value = metric.read(row);
      return (typeof value === "string" || typeof value === "boolean") && categoryValues(metric, value).some(label => selected.has(label));
    };
  }
  const first = numericInput(rule.first);
  const second = numericInput(rule.second);
  if (!metric.numeric || first === null || (op === "between" && (second === null || first > second))) return null;
  return row => {
    const value = metric.read(row);
    if (typeof value !== "number" || !Number.isFinite(value)) return false;
    if (op === "lte") return value <= first;
    if (op === "absolute") return Math.abs(value) >= first;
    if (op === "between") return value >= first && value <= second!;
    return value >= first;
  };
}
