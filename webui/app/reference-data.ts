import type { VariantRow } from "./vcf";

const BUNDLED_DATA = "/bundled-data";

export type GeneConstraint = {
  gene: string;
  geneId: string;
  transcript: string;
  pLi: number | null;
  loeuf: number | null;
  missenseZ: number | null;
  lofOe: number | null;
  lofObserved: number | null;
  lofExpected: number | null;
  geneFlags: string[];
  constraintFlags: string[];
  exomeAn90: number | null;
  exomeSegdup: number | null;
  exomeLcr: number | null;
};

export type ReferenceManifest = {
  gnomad: { release: string; selected_genes: number; source_url: string };
  iuis: {
    release: string;
    unique_genes: number;
    dominant_genes: number;
    source_url: string;
  };
  haploinsufficiency: {
    release: string;
    genes: number;
    source: string;
  };
};

export type BundledReferences = {
  constraints: Map<string, GeneConstraint>;
  ieiGenes: Set<string>;
  hiGenes: Set<string>;
  dominantGenes: Set<string>;
  manifest: ReferenceManifest | null;
  errors: Partial<Record<ReferenceKey, string>>;
};

export type ReferenceKey = "constraints" | "iei" | "hi" | "dominant" | "manifest";

function parseNumber(value: string | undefined) {
  if (!value) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function splitFlags(value: string | undefined) {
  return value ? value.split("|").filter(Boolean) : [];
}

export function parseGeneConstraintTsv(text: string) {
  const lines = text.trim().split(/\r?\n/);
  const headers = lines.shift()?.split("\t") ?? [];
  const index = new Map(headers.map((header, position) => [header, position]));
  for (const required of ["gene_symbol", "pLI", "loeuf"]) {
    if (!index.has(required)) throw new Error(`Constraint table is missing ${required}`);
  }
  const value = (columns: string[], name: string) => columns[index.get(name) ?? -1] ?? "";
  const constraints = new Map<string, GeneConstraint>();
  for (const line of lines) {
    if (!line) continue;
    const columns = line.split("\t");
    const gene = value(columns, "gene_symbol").toUpperCase();
    if (!gene) continue;
    constraints.set(gene, {
      gene,
      geneId: value(columns, "ensembl_gene_id"),
      transcript: value(columns, "transcript_id"),
      pLi: parseNumber(value(columns, "pLI")),
      loeuf: parseNumber(value(columns, "loeuf")),
      missenseZ: parseNumber(value(columns, "missense_z")),
      lofOe: parseNumber(value(columns, "lof_oe")),
      lofObserved: parseNumber(value(columns, "lof_observed")),
      lofExpected: parseNumber(value(columns, "lof_expected")),
      geneFlags: splitFlags(value(columns, "gene_flags")),
      constraintFlags: splitFlags(value(columns, "constraint_flags")),
      exomeAn90: parseNumber(value(columns, "exome_prop_bp_an90")),
      exomeSegdup: parseNumber(value(columns, "exome_prop_segdup")),
      exomeLcr: parseNumber(value(columns, "exome_prop_lcr")),
    });
  }
  return constraints;
}

export function parseGeneList(text: string) {
  const headers = new Set(["GENE", "GENES", "SYMBOL", "GENE_SYMBOL", "HGNC_SYMBOL"]);
  return new Set(
    text
      .split(/[\s,;]+/)
      .map((gene) => gene.trim().replace(/^["']|["']$/g, "").toUpperCase())
      .filter((gene) => gene && !headers.has(gene)),
  );
}

async function requireText(path: string) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(path, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`Could not load bundled reference ${path}`);
    const text = await response.text();
    if (!text.trim() || /^\s*</.test(text)) throw new Error(`Invalid bundled reference ${path}`);
    return text;
  } finally {
    clearTimeout(timeout);
  }
}

export async function loadBundledReferences(): Promise<BundledReferences> {
  const errors: BundledReferences["errors"] = {};
  async function load<T>(key: ReferenceKey, file: string, parse: (text: string) => T, fallback: T): Promise<T> {
    try {
      return parse(await requireText(`${BUNDLED_DATA}/${file}`));
    } catch {
      errors[key] = `${file} could not be loaded or validated`;
      return fallback;
    }
  }
  const genes = (text: string) => {
    const result = parseGeneList(text);
    if (!result.size || [...result].some((gene) => !/^[A-Z0-9][A-Z0-9._-]*$/.test(gene))) {
      throw new Error("Invalid gene list");
    }
    return result;
  };
  const [constraints, ieiGenes, hiGenes, dominantGenes, manifest] = await Promise.all([
    load("constraints", "gnomad_v4.1.1_gene_constraint.tsv", (text) => {
      const result = parseGeneConstraintTsv(text);
      if (!result.size) throw new Error("Empty constraint table");
      return result;
    }, new Map<string, GeneConstraint>()),
    load("iei", "iuis_2024_genes.txt", genes, new Set<string>()),
    load("hi", "iei_haploinsufficiency_genes.txt", genes, new Set<string>()),
    load("dominant", "iuis_2024_dominant_genes.txt", genes, new Set<string>()),
    load<ReferenceManifest | null>("manifest", "manifest.json", (text) => {
      const value = JSON.parse(text);
      if (!value?.gnomad?.release || !value?.iuis?.release || !value?.haploinsufficiency?.release) {
        throw new Error("Invalid reference manifest");
      }
      return value as ReferenceManifest;
    }, null),
  ]);
  return { constraints, ieiGenes, hiGenes, dominantGenes, manifest, errors };
}

export function attachGeneConstraints(
  rows: VariantRow[],
  constraints: Map<string, GeneConstraint>,
  release = "4.1.1",
) {
  return rows.map((row) => {
    const constraint = constraints.get(row.gene.toUpperCase());
    if (!constraint) return row;
    return {
      ...row,
      pLi: constraint.pLi ?? row.pLi,
      loeuf: constraint.loeuf ?? row.loeuf,
      missenseZ: constraint.missenseZ ?? row.missenseZ,
      constraintGeneId: constraint.geneId,
      constraintTranscript: constraint.transcript,
      constraintRelease: release,
      constraintFlags: constraint.constraintFlags,
      geneFlags: constraint.geneFlags,
      constraintLofOe: constraint.lofOe,
      constraintLofObserved: constraint.lofObserved,
      constraintLofExpected: constraint.lofExpected,
      constraintExomeAn90: constraint.exomeAn90,
      constraintExomeSegdup: constraint.exomeSegdup,
      constraintExomeLcr: constraint.exomeLcr,
    };
  });
}

export function deriveLofConstrainedGenes(
  constraints: Map<string, GeneConstraint>,
  pLiMinimum = 0.9,
  loeufMaximum = 0.6,
) {
  return new Set(
    [...constraints.values()]
      .filter(
        (constraint) =>
          (constraint.pLi !== null && constraint.pLi >= pLiMinimum)
          || (constraint.loeuf !== null && constraint.loeuf < loeufMaximum),
      )
      .map((constraint) => constraint.gene),
  );
}
