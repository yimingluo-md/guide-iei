import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const root = new URL("../", import.meta.url);
const source = await readFile(new URL("app/reference-data.ts", root), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { attachGeneConstraints, deriveLofConstrainedGenes, parseGeneConstraintTsv, parseGeneList, loadBundledReferences } = await import(moduleUrl);

test("reference failures are isolated, validated, and recover on retry", async (t) => {
  const files = [
    "gnomad_v4.1.1_gene_constraint.tsv", "iuis_2024_genes.txt",
    "iei_haploinsufficiency_genes.txt", "iuis_2024_dominant_genes.txt", "manifest.json",
  ];
  const keys = ["constraints", "iei", "hi", "dominant", "manifest"];
  const contents = new Map(await Promise.all(files.map(async (file) => [
    file, await readFile(new URL(`public/bundled-data/${file}`, root), "utf8"),
  ])));
  let failedFile = "";
  let corrupt = false;
  t.mock.method(globalThis, "fetch", async (path) => {
    const file = path.split("/").pop();
    return {
      ok: file !== failedFile || corrupt,
      text: async () => file === failedFile && corrupt ? "<html>Error page</html>" : contents.get(file),
    };
  });
  for (let index = 0; index < files.length; index++) {
    failedFile = files[index];
    for (corrupt of [false, true]) {
      const result = await loadBundledReferences();
      assert.deepEqual(Object.keys(result.errors), [keys[index]]);
      if (index !== 0) assert.equal(result.constraints.size, 19638);
      if (index !== 1) assert.equal(result.ieiGenes.size, 505);
      if (index !== 2) assert.equal(result.hiGenes.size, 50);
      if (index !== 3) assert.equal(result.dominantGenes.size, 137);
    }
  }
  failedFile = "";
  const recovered = await loadBundledReferences();
  assert.deepEqual(recovered.errors, {});
  assert.equal(recovered.ieiGenes.size, 505);
  assert.equal(recovered.manifest.gnomad.release, "4.1.1");
});

test("missing constraint schema and malformed manifests are not treated as empty valid data", async (t) => {
  t.mock.method(globalThis, "fetch", async (path) => ({
    ok: true,
    text: async () => path.endsWith(".tsv") ? "gene_symbol\nNFKB1" : path.endsWith(".json") ? "{}" : "NFKB1",
  }));
  const result = await loadBundledReferences();
  assert.deepEqual(Object.keys(result.errors).sort(), ["constraints", "manifest"]);
  assert.equal(result.constraints.size, 0);
  assert.equal(result.manifest, null);
  assert.equal(result.ieiGenes.size, 1);
});

const constraintText = await readFile(
  new URL("public/bundled-data/gnomad_v4.1.1_gene_constraint.tsv", root),
  "utf8",
);
const ieiText = await readFile(
  new URL("public/bundled-data/iuis_2024_genes.txt", root),
  "utf8",
);
const dominantText = await readFile(
  new URL("public/bundled-data/iuis_2024_dominant_genes.txt", root),
  "utf8",
);
const haploinsufficiencyText = await readFile(
  new URL("public/bundled-data/iei_haploinsufficiency_genes.txt", root),
  "utf8",
);

test("bundles one selected gnomAD v4.1.1 constraint row per gene", () => {
  const constraints = parseGeneConstraintTsv(constraintText);
  assert.equal(constraints.size, 19_638);
  assert.equal(constraints.get("NFKB1").transcript, "ENST00000226574");
  assert.equal(constraints.get("NFKB1").pLi, 1);
  assert.equal(constraints.get("NFKB1").loeuf, 0.14949);
  assert.equal(constraints.get("NFKB1").missenseZ, 4.5938);
  assert.equal(constraints.get("CYBB").loeuf, 0.2259);
});

test("bundles complete IUIS and AD-derived gene sets", () => {
  const ieiGenes = parseGeneList(ieiText);
  const dominantGenes = parseGeneList(dominantText);
  assert.equal(ieiGenes.size, 505);
  assert.equal(dominantGenes.size, 137);
  assert(ieiGenes.has("NFKB1"));
  assert(ieiGenes.has("IL10RA"));
  assert(dominantGenes.has("NFKB1"));
  assert(!dominantGenes.has("IL10RA"));
  assert(ieiGenes.has("MS4A1"));
  assert(ieiGenes.has("NCKAP1L"));
});

test("bundles the manually curated IEI haploinsufficiency set", () => {
  const genes = parseGeneList(haploinsufficiencyText);
  assert.equal(genes.size, 50);
  assert(genes.has("CTLA4"));
  assert(genes.has("NFKB1"));
  assert(genes.has("STAT3"));
  assert(!genes.has("GENE"));
});

test("custom gene-list input ignores common headers and accepts pasted delimiters", () => {
  const genes = parseGeneList("Gene\nNFKB1, CTLA4;STAT3\tCARD11");
  assert.deepEqual([...genes], ["NFKB1", "CTLA4", "STAT3", "CARD11"]);
});

test("automatically attaches bundled constraint data by gene symbol", () => {
  const constraints = parseGeneConstraintTsv(constraintText);
  const [row] = attachGeneConstraints([{
    gene: "nfkb1",
    pLi: null,
    loeuf: null,
    missenseZ: null,
  }], constraints, "4.1.1");
  assert.equal(row.pLi, 1);
  assert.equal(row.loeuf, 0.14949);
  assert.equal(row.constraintRelease, "4.1.1");
  assert.equal(row.constraintGeneId, "ENSG00000109320");
});

test("derives a sensitive LoF-constrained set from pLI or LOEUF", () => {
  const constraints = parseGeneConstraintTsv(constraintText);
  const genes = deriveLofConstrainedGenes(constraints);
  assert(genes.has("NFKB1"));
  assert(genes.size > 1_000);
  for (const gene of genes) {
    const constraint = constraints.get(gene);
    assert(
      (constraint.pLi !== null && constraint.pLi >= 0.9)
      || (constraint.loeuf !== null && constraint.loeuf < 0.6),
    );
  }
});
