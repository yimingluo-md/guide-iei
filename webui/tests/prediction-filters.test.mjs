import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const registry = await readFile(new URL("../../config/predictor-registry.json", import.meta.url), "utf8");
function compile(source) {
  const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
  return "data:text/javascript;base64," + Buffer.from(compiled).toString("base64");
}
const appSource = name => readFile(new URL("../app/" + name, import.meta.url), "utf8");
const reference = JSON.parse(await readFile(new URL("../../config/gpn-star-ranks.json", import.meta.url), "utf8"));
const parserUrl = compile((await appSource("vcf.ts")).replace('import predictorRegistry from "../../config/predictor-registry.json";', "const predictorRegistry = " + registry + ";"));
const rankUrl = compile((await appSource("gpn-star-rank.ts")).replace('import reference from "../../config/gpn-star-ranks.json";', "const reference = " + JSON.stringify(reference) + ";")
  .replace('from "./vcf";', 'from "' + parserUrl + '";'));
const { parseVcfFiles } = await import(parserUrl);
const { gpnConstraintQ } = await import(rankUrl);
const source = (await appSource("prediction-filters.ts"))
  .replace('import registry from "../../config/predictor-registry.json";', "const registry = " + registry + ";")
  .replace('from "./gpn-star-rank";', 'from "' + rankUrl + '";');
const { reviewMetrics, compileRule } = await import(compile(source));
const observation = (values, matchStatus = "exact") => ({ values, matchStatus });
const scored = { cadd: 0, alphaMissense: null, spliceAI: 0.8, predictions: {
  cadd_wgs: observation({ phred: 8 }), starter_cadd: observation({ phred: 4 }),
  alphamissense: observation({ score: 0.9 }), spliceai: observation({ delta_acceptor_loss: 0.8 }),
  gpn_star_v: observation({ llr: -2, abs_llr: -0.5 }),
  logofunc: observation({ prediction: "GOF", gof: 0.7 }), vep_sift: observation({ prediction: "deleterious(0.02)" }),
  funcvep: observation({cti: 0.99}, "ambiguous"), clinvar_assertions: observation({clinical_significance: "Pathogenic"}),
} };
const missing = {};
const metrics = new Map(reviewMetrics([scored, missing], ["FuncVEP_CTI", "StarterAM_score"]).map(m => [m.id, m]));
function check(metric, operator, first = "", second = "", choices = []) {
  return compileRule({metric, operator, first, second, choices}, metrics.get(metric));
}

test("metrics follow displayed provider precedence, match status and the single SpliceAI maximum", () => {
  assert.deepEqual([...metrics.keys()].filter(id => id.startsWith("spliceai.")), ["spliceai.max"]);
  assert.deepEqual([...metrics.keys()].filter(id => id.includes("cadd")), ["cadd.phred"]);
  assert.equal(metrics.get("alphamissense.score").count, 0);
  assert.equal(metrics.get("funcvep.cti").count, 0);
  assert(!metrics.has("clinvar_assertions.clinical_significance"));
  assert(check("cadd.phred", "lte", "0")(scored));
  assert(check("spliceai.max", "gte", ".8")(scored));
  assert(check("funcvep.cti", "missing")(scored));
});

test("signed, inclusive, absolute and presence rules distinguish zero from missing", () => {
  for (const rule of [check("gpn_star_v.llr", "lte", "-2e0"), check("gpn_star_v.llr", "absolute", "2"),
    check("gpn_star_v.llr", "between", "-2", "-1"), check("cadd.phred", "present"), check("cadd.phred", "gte", "0")]) {
    assert(rule(scored)); assert(!rule(missing));
  }
  assert(!check("gpn_star_v.abs_llr", "gte", "0")(scored));
  assert.equal(check("cadd.phred", "between", "2", "1"), null);
  for (const invalid of ["", " ", "-", "1e", "Infinity", "NaN"]) assert.equal(check("cadd.phred", "gte", invalid), null);
});

test("category rules offer observed labels and allow several selections", () => {
  assert.deepEqual(metrics.get("vep_sift.prediction").categories, ["deleterious"]);
  assert(check("vep_sift.prediction", "oneOf", "", "", ["deleterious"])(scored));
  assert(check("logofunc.prediction", "oneOf", "", "", ["LOF", "GOF"])(scored));
  assert(!check("logofunc.prediction", "oneOf", "", "", ["LOF"])(scored));
  assert(!check("logofunc.prediction", "oneOf", "", "", ["GOF"])(missing));
  assert.equal(check("logofunc.prediction", "oneOf"), null);
});

test("compact cohort rows keep available scores and withhold unmatched LoGoFunc evidence", () => {
  const matched = { promoterAI: 0, loGoFuncPrediction: "GOF", loGoFuncMatch: "source_transcript_match_elsewhere" };
  const unmatched = { loGoFuncPrediction: "GOF", loGoFuncMatch: "partial" };
  const catalog = new Map(reviewMetrics([matched, unmatched]).map(metric => [metric.id, metric]));
  assert.equal(catalog.get("promoterai.score").count, 1);
  assert.equal(catalog.get("logofunc.prediction").count, 1);
  assert.equal(compileRule({operator: "missing"}, catalog.get("logofunc.prediction"))(unmatched), true);
});

test("LOFTEE reasons can be selected separately on exact observations and summary rows", () => {
  const evidence = "END_TRUNC, ANC_ALLELE&END_TRUNC";
  const full = { predictions: { loftee: observation({ filter: evidence }) } };
  const summary = { lofteeFilter: evidence };
  const empty = { lofteeFilter: "., -", predictions: undefined };
  const [metric] = reviewMetrics([full, summary, empty]);
  assert.equal(metric.id, "loftee.filter");
  assert.equal(metric.count, 2);
  assert.deepEqual(metric.categories, ["ANC_ALLELE", "END_TRUNC"]);
  for (const row of [full, summary]) {
    for (const choice of metric.categories) assert(compileRule({operator: "oneOf", choices: [choice]}, metric)(row));
  }
  assert(compileRule({operator: "missing"}, metric)(empty));
});

test("compact rule_changed preserves false, true, and missing, with normalized observations authoritative", () => {
  const rows = [
    { loftee50bpChanged: false }, { loftee50bpChanged: true },
    { loftee50bpChanged: null }, {}, { loftee50bpChanged: true, predictions: {} },
  ];
  const [metric] = reviewMetrics(rows);
  assert.equal(metric.id, "loftee_ptc_50bp.rule_changed");
  assert.equal(metric.count, 2);
  assert.deepEqual(metric.categories, ["false", "true"]);
  assert.deepEqual(rows.map(compileRule({operator: "oneOf", choices: ["false"]}, metric)), [true, false, false, false, false]);
  assert.deepEqual(rows.map(compileRule({operator: "missing"}, metric)), [false, false, true, true, true]);
});

test("GPN constraint ranks include ties, respect model-specific boundaries and source revision", async () => {
  const metricId = "gpn_star_v.constraint_q";
  async function parse(packageName, releases) {
    const lines = ["##fileformat=VCFv4.2", "##reference=GRCh38",
      ...releases.map(revision => "##GPNStar_v100_release=" + revision),
      "##GPNStar_v100_package=" + packageName,
      '##INFO=<ID=GPNv100_LLR,Number=A,Type=Float,Description="LLR">',
      '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|ALLELE_NUM|Consequence|IMPACT">',
      "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS",
      "chr1\t100\t.\tA\tC,G,T\t60\tPASS\tGPNv100_LLR=-5.651,-5.65,.;CSQ=C|1|intergenic_variant|MODIFIER,G|2|intergenic_variant|MODIFIER,T|3|intergenic_variant|MODIFIER\tGT\t1/2", ""];
    return (await parseVcfFiles([new File([lines.join("\n")], "rank.vcf")])).rows;
  }
  const full = await parse("v100", [reference.revision]);
  const exome = await parse("v100-exome", [reference.revision]);
  const q = row => gpnConstraintQ(row, "gpn_star_v");
  assert.deepEqual(full.map(q), exome.map(q));
  const pass = full.find(row => row.alt === "C"), fail = full.find(row => row.alt === "G");
  assert.equal(q(pass), 10 * Math.log10(8804242314 / 88011798));
  assert.equal(q(fail), 10 * Math.log10(8804242314 / 88056221));
  const metric = reviewMetrics(full).find(m => m.id === metricId);
  assert.equal(metric.suggestedMinimum, "20");
  const rule = compileRule({operator: "gte", first: "20", second: ""}, metric);
  assert(rule(pass)); assert(!rule(fail));
  for (const releases of [[], ["other"], [reference.revision, "other"]]) {
    const rows = await parse("v100", releases);
    assert(rows.every(row => q(row) === null));
    assert.equal(rows[0].predictions.gpn_star_v.values.llr, -5.651);
  }
  assert.equal(q({...pass, chrom: "MT"}), null);
  assert.equal(q({...pass, alt: "AT"}), null);
  assert.equal(q({...pass, ref: "AT", alt: "CT"}), q(pass));
  const obs = pass.predictions.gpn_star_v;
  for (const values of [{abs_llr: 3.4}, {llr: NaN}, {llr: -4.1234}, {llr: -999}]) {
    assert.equal(q({...pass, predictions: {gpn_star_v: {...obs, values}}}), null);
  }
  const m = llr => gpnConstraintQ({...pass, predictions: {gpn_star_m: {...obs, values: {llr}}}}, "gpn_star_m");
  assert(m(-5.449) >= 20 && m(-5.448) < 20);
  assert(Math.abs(m(7.669) - 0.000429561568589) < 1e-12);
});
