// Opt-in real VEP output -> browser reader check; not part of npm test.
// node tests/starter-real-payload.mjs /path/to/public-starter.vep.vcf.gz
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gunzipSync } from "node:zlib";
import ts from "typescript";

const path = process.argv[2];
if (!path) throw new Error("Pass the synthetic public VEP output to validate");
const registry = JSON.parse(await readFile(new URL("../../config/predictor-registry.json", import.meta.url), "utf8"));
const source = (await readFile(new URL("../app/vcf.ts", import.meta.url), "utf8")).replace(
  'import predictorRegistry from "../../config/predictor-registry.json";',
  `const predictorRegistry = ${JSON.stringify(registry)};`,
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { parseVcfFiles } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const original = gunzipSync(await readFile(path)).toString("utf8");
const fields = original.split("\n").find((l) => l.startsWith("##INFO=<ID=CSQ,")).split("Format: ")[1].split('"')[0].split("|");
const expected = new Map();
for (const line of original.split("\n").filter((l) => l && !l.startsWith("#"))) {
  const row = line.split("\t");
  const csq = row[7].split(";").find((s) => s.startsWith("CSQ=")).slice(4);
  for (const text of csq.split(",")) {
    const data = Object.fromEntries(fields.map((f, i) => [f, decodeURIComponent(text.split("|")[i] || "")]));
    expected.set([row[0], row[1], row[3], row[4], data.Feature].join(":"), data);
  }
}
// Synthetic carrier only; no patient identifiers or measured call quality.
const vcf = original.split("\n").map((line) => line.startsWith("#CHROM")
  ? `${line}\tFORMAT\tSYNTHETIC` : line && !line.startsWith("#") ? `${line}\tGT\t0/1` : line).join("\n");
const { rows } = await parseVcfFiles([new File([vcf], "public-starter-fixture.vcf")]);
let am = 0, cadd = 0, spliceai = 0, renamedGene = 0;
for (const row of rows) {
  const raw = expected.get([row.chrom, row.pos, row.ref, row.alt, row.transcript || ""].join(":"));
  assert.ok(raw, "Every review row must map back to its original allele/transcript");
  assert.equal(row.alphaMissense, raw.StarterAM_score ? Number(raw.StarterAM_score) : null);
  assert.equal(row.cadd, raw.StarterCADD_phred ? Number(raw.StarterCADD_phred) : null);
  assert.equal(row.caddRaw, null);
  if (row.alphaMissense !== null) am++;
  if (row.cadd !== null) cadd++;
  if (raw.SpliceAI_pred_SYMBOL) {
    const prediction = row.predictions.spliceai;
    assert.equal(prediction.matchStatus, "exact");
    assert.equal(prediction.target.gene_symbol, raw.SpliceAI_pred_SYMBOL);
    const labels = ["acceptor_gain", "acceptor_loss", "donor_gain", "donor_loss"];
    const codes = ["AG", "AL", "DG", "DL"];
    codes.forEach((code, i) => {
      assert.equal(prediction.values[`delta_${labels[i]}`], Number(raw[`SpliceAI_pred_DS_${code}`]));
      assert.equal(prediction.provenance[`delta_position_${labels[i]}`], Number(raw[`SpliceAI_pred_DP_${code}`]));
    });
    spliceai++;
    if (raw.SpliceAI_pred_SYMBOL !== raw.SYMBOL) renamedGene++;
  }
}
assert.ok(am > 0 && cadd > 0 && spliceai > 0 && renamedGene > 0);
console.log(JSON.stringify({ status: "PASS", review_rows: rows.length, alphamissense_scores: am, cadd_scores: cadd, spliceai_scores: spliceai, spliceai_renamed_gene_scores: renamedGene }));
