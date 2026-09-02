import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { deflateRawSync, gzipSync } from "node:zlib";
import ts from "typescript";

const rawSource = await readFile(new URL("../app/vcf.ts", import.meta.url), "utf8");
const predictorRegistry = JSON.parse(await readFile(
  new URL("../../config/predictor-registry.json", import.meta.url),
  "utf8",
));
// The production bundle resolves the JSON import. This dependency-free test
// harness transpiles one TypeScript file to a data URL, so inline the same
// canonical registry document before transpilation.
const source = rawSource.replace(
  'import predictorRegistry from "../../config/predictor-registry.json";',
  `const predictorRegistry = ${JSON.stringify(predictorRegistry)};`,
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  ADDITIONAL_DBNSFP_PREDICTORS,
  candidateCompoundHetKeys,
  isHeterozygousGenotype,
  STANDARD_VARIANT_QC,
  ReviewRowLimitError,
  parseVcfFiles,
  predictorBinaryClassification,
  collapseToOneRowPerVariant,
  preferredClinicalTranscriptRows,
  variantQcFailures,
} = await import(moduleUrl);

const CSQ_FIELDS = [
  "Allele", "Consequence", "IMPACT", "SYMBOL", "HGVSc", "HGVSp", "MANE_SELECT", "PICK",
  "Feature", "Gene", "BIOTYPE", "EXON", "CADD_phred", "AlphaMissense_score",
  "AlphaMissense_pred", "REVEL_score", "MetaRNN_score", "PrimateAI_score",
  "SIFT_score", "Polyphen2_HDIV_score", "CADD_raw", "MetaRNN_pred",
  "PrimateAI_pred", "SIFT_pred", "Polyphen2_HDIV_pred", "GERP++_RS",
  "phyloP100way_vertebrate", "phastCons100way_vertebrate",
  "gnomADe_AF", "gnomADe_AFR_AF", "gnomADg_AF", "gnomADg_NFE_AF",
  "MAX_AF_POPS", "ClinVar_CLNREVSTAT", "ClinVar_CLNDN",
  "pLI", "LOEUF", "MPC_score", "ESM1b_score", "ESM1b_pred", "PromoterAI_score",
];
const PASS_CSQ = [
  "G", "missense_variant", "MODERATE", "NFKB1", "ENST:c.1A>G", "ENSP:p.Lys1Arg",
  "NM_001165412.2", "1", "ENST00000226574", "ENSG00000109320", "protein_coding", "4/22",
  "24.6", "0.91", "pathogenic", "0.82", "0.77", "0.68", "0.01", "0.99",
  "2.3", "D", "D", "D", "D", "5.1", "2.4", "0.98",
  "0.0002", "0.0003", "0.0001", "0.00015", "AFR",
  "reviewed_by_expert_panel", "immunodeficiency", "0.997", "0.21",
  "2.1", "0.73", "D", "-0.91",
].join("|");
const VCF = [
  "##fileformat=VCFv4.2",
  "##reference=GRCh38",
  "##contig=<ID=1,length=248956422>",
  `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">`,
  "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
  `1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ};IEI_UNSCORED_INDEL=SpliceAI_intronic&PromoterAI_promoter\tGT:DP:GQ:AD\t0/1:40:99:20,20`,
  "1\t200\t.\tC\tT\t20\tLowQual\tCSQ=T|missense_variant|MODERATE|NFKB1||||\tGT:DP:GQ:AD\t0/1:20:30:10,10",
  "",
].join("\n");

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function bgzfBlock(content) {
  const payload = Buffer.from(content);
  const compressed = deflateRawSync(payload);
  const blockSize = 18 + compressed.length + 8;
  const header = Buffer.from([
    0x1f, 0x8b, 0x08, 0x04, 0, 0, 0, 0, 0, 0xff,
    0x06, 0, 0x42, 0x43, 0x02, 0,
    (blockSize - 1) & 0xff, ((blockSize - 1) >>> 8) & 0xff,
  ]);
  const footer = Buffer.alloc(8);
  footer.writeUInt32LE(crc32(payload), 0);
  footer.writeUInt32LE(payload.length, 4);
  return Buffer.concat([header, compressed, footer]);
}

test("imports a real gzip-compressed .vcf.gz file", async () => {
  const compressed = gzipSync(Buffer.from(VCF));
  const file = new File([compressed], "patient.vep.vcf.gz", { type: "application/gzip" });
  const result = await parseVcfFiles([file]);

  assert.equal(result.summary.files, 1);
  assert.equal(result.summary.samples, 1);
  assert.equal(result.summary.passRecords, 1);
  assert.equal(result.summary.excludedNonPass, 1);
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].gene, "NFKB1");
  assert.equal(result.rows[0].genotype, "0/1");
  assert.equal(result.rows[0].adRef, 20);
  assert.equal(result.rows[0].adAlt, 20);
  assert.equal(result.rows[0].sampleGenotypes.PATIENT.gt, "0/1");
  assert.equal(result.rows[0].qual, 99);
  assert.equal(result.rows[0].transcript, "ENST00000226574");
  assert.equal(result.rows[0].picked, true);
  assert.equal(result.rows[0].cadd, 24.6);
  assert.equal(result.rows[0].alphaMissense, 0.91);
  assert.equal(result.rows[0].revel, 0.82);
  assert.equal(result.rows[0].caddRaw, 2.3);
  assert.equal(result.rows[0].metaRnnPrediction, "D");
  assert.equal(result.rows[0].primateAiPrediction, "D");
  assert.equal(result.rows[0].siftPrediction, "D");
  assert.equal(result.rows[0].polyPhenPrediction, "D");
  assert.equal(result.rows[0].gerpRs, 5.1);
  assert.equal(result.rows[0].phyloP100way, 2.4);
  assert.equal(result.rows[0].phastCons100way, 0.98);
  assert.equal(result.rows[0].gnomadFrequencies.gnomADe_AF, 0.0002);
  assert.equal(result.rows[0].gnomadFrequencies.gnomADe_AFR_AF, 0.0003);
  assert.equal(result.rows[0].gnomadFrequencies.gnomADg_NFE_AF, 0.00015);
  assert.equal(result.rows[0].gnomadPopmaxPopulation, "AFR");
  assert.equal(result.rows[0].clinvarReviewStatus, "reviewed_by_expert_panel");
  assert.deepEqual(result.rows[0].predictions.clinvar_assertions, {
    scope: "allele",
    matchStatus: "exact",
    matchedOn: ["chromosome", "position", "reference", "alternate"],
    values: { review_status: "reviewed_by_expert_panel" },
    provenance: { disease: "immunodeficiency" },
  });
  assert.equal(result.rows[0].pLi, 0.997);
  assert.equal(result.rows[0].loeuf, 0.21);
  assert.equal(result.rows[0].promoterAI, null);
  assert.deepEqual(result.rows[0].predictions.promoterai, {
    scope: "allele_transcript_tss_strand",
    matchStatus: "partial",
    matchedOn: ["chromosome", "position", "reference", "alternate"],
    values: {},
    provenance: { withheld_metrics: "score" },
  });
  assert.deepEqual(result.rows[0].unscoredIndelReasons, [
    "SpliceAI_intronic", "PromoterAI_promoter",
  ]);
  assert.deepEqual(result.rows[0].availableDbnsfpPredictors, ["mpc", "esm1b"]);
  assert.deepEqual(result.rows[0].dbnsfpPredictors.mpc, {
    score: 2.1,
    prediction: "",
  });
  assert.deepEqual(result.rows[0].dbnsfpPredictors.esm1b, {
    score: 0.73,
    prediction: "D",
  });
  assert.equal(
    ADDITIONAL_DBNSFP_PREDICTORS.find((item) => item.id === "esm1b")
      .damagingDirection,
    "lower",
  );
  assert.equal(result.summary.intakeQc.length, 10);
  assert.equal(result.summary.intakeQc.every((check) => check.status === "pass"), true);
});


test("oversized files are refused before parsing, with routing guidance", async () => {
  const oversized = { name: "cohort.vep.vcf.gz", size: 150 * 1024 * 1024 };
  await assert.rejects(
    () => parseVcfFiles([oversized]),
    /too large[\s\S]*Whole genome analysis scope/,
  );
});

test("row limit distinguishes variants from transcripts and retained reviews compact clinically", async () => {
  const second = PASS_CSQ.split("|");
  second[4] = "ENST:c.2A>G";
  second[5] = "ENSP:p.Lys2Arg";
  second[6] = "";
  second[7] = "";
  second[8] = "ENST00000999999";
  const vcf = VCF.replace(`CSQ=${PASS_CSQ};`, `CSQ=${PASS_CSQ},${second.join("|")};`);

  await assert.rejects(
    () => parseVcfFiles([new File([vcf], "many-transcripts.vcf")], { rowCap: 1 }),
    (reason) => {
      assert.equal(reason instanceof ReviewRowLimitError, true);
      assert.match(reason.message, /1 passing variant record/);
      assert.match(reason.message, /transcript annotation rows/);
      return true;
    },
  );

  const compact = await parseVcfFiles(
    [new File([vcf], "many-transcripts.vcf")],
    { rowCap: 1, clinicalTranscriptsOnly: true },
  );
  assert.equal(compact.rows.length, 1);
  assert.equal(compact.rows[0].mane, true);
  assert.equal(compact.rows[0].compactTranscriptView, true);
  assert.match(compact.summary.warnings.join("\n"), /Responsive clinical-transcript view/);
});

test("cohort files parse variant-centrically: one row per variant with carriers", async () => {
  const samples = Array.from({ length: 20 }, (_, i) => `S${i + 1}`);
  const genotypes1 = samples.map((s) => (s === "S1" ? "0/1:30:99:15,15" : "0/0:30:99:30,0"));
  const genotypes2 = samples.map((s) => (s === "S2" || s === "S3" ? "0/1:25:80:12,13" : "0/0:25:80:25,0"));
  const vcf = "##fileformat=VCFv4.2\n"
    + "##reference=GRCh38\n"
    + "##contig=<ID=1,length=248956422>\n"
    + `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">\n`
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + samples.join("\t") + "\n"
    + `1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t` + genotypes1.join("\t") + "\n"
    + `1\t200\trs2\tC\tT\t99\tPASS\tCSQ=${PASS_CSQ.replace("missense_variant", "stop_gained")}\tGT:DP:GQ:AD\t` + genotypes2.join("\t") + "\n";
  const result = await parseVcfFiles([new File([vcf], "cohort.vcf")]);
  assert.equal(result.summary.cohortMode, true);
  assert.equal(result.summary.samples, 20);
  // One row per variant (single transcript each), not one per carrier.
  assert.equal(result.rows.length, 2);
  const first = result.rows.find((row) => row.pos === 100);
  const second = result.rows.find((row) => row.pos === 200);
  assert.equal(first.genotype, "1/20 carry");
  assert.deepEqual(first.carriers.map((c) => c.sample), ["S1"]);
  assert.equal(first.carriers[0].evidence.gt, "0/1");
  assert.equal(first.carriers[0].evidence.dp, 30);
  assert.equal(first.cohortSampleCount, 20);
  assert.equal(second.genotype, "2/20 carry");
  assert.deepEqual(second.carriers.map((c) => c.sample).sort(), ["S2", "S3"]);
  // The carriers-only evidence map replaces the all-samples map.
  assert.deepEqual(Object.keys(first.sampleGenotypes), ["S1"]);
});

test("cohort imports respect the carrier-entry cap with prefilter guidance", async () => {
  const samples = Array.from({ length: 20 }, (_, i) => `S${i + 1}`);
  const carriedByAll = samples.map(() => "0/1:30:99:15,15");
  const vcf = "##fileformat=VCFv4.2\n"
    + "##reference=GRCh38\n"
    + "##contig=<ID=1,length=248956422>\n"
    + `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">\n`
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + samples.join("\t") + "\n"
    + `1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t` + carriedByAll.join("\t") + "\n"
    + `1\t200\trs2\tC\tT\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t` + carriedByAll.join("\t") + "\n";
  await assert.rejects(
    () => parseVcfFiles([new File([vcf], "cohort.vcf")], { carrierEntryCap: 30 }),
    /carrier genotypes[\s\S]*population-frequency/,
  );
});

test("a joint cohort file mixed with other files aggregates without a denominator", async () => {
  const samples = Array.from({ length: 20 }, (_, i) => `S${i + 1}`);
  const genotypes = samples.map((s) => (s === "S1" ? "0/1:30:99:15,15" : "0/0:30:99:30,0"));
  const cohort = "##fileformat=VCFv4.2\n"
    + "##reference=GRCh38\n"
    + "##contig=<ID=1,length=248956422>\n"
    + `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${CSQ_FIELDS.join("|")}">\n`
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + samples.join("\t") + "\n"
    + `1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t` + genotypes.join("\t") + "\n";
  const result = await parseVcfFiles([
    new File([cohort], "cohort.vcf"),
    new File([VCF], "patient.vcf"),
  ]);
  assert.equal(result.summary.separatelyCalled, true);
  const shared = result.rows.find((row) => row.pos === 100);
  // S1 (from the joint file) and PATIENT (separate file) both carry — but
  // mixed provenance means no denominator is claimed.
  assert.equal(shared.genotype, "2 carry");
  assert.deepEqual(shared.carriers.map((c) => c.sample).sort(), ["PATIENT", "S1"]);
  assert.equal(shared.cohortSampleCount, undefined);
});

test("separately-called files aggregate: N carry, popmax at parse, drift warning", async () => {
  const fileFor = (name, sampleNames, records, clinvarDate) => {
    const vcf = "##fileformat=VCFv4.2\n"
      + "##reference=GRCh38\n"
      + "##contig=<ID=1,length=248956422>\n"
      + `##INFO=<ID=ClinVar_path_aa_match,Number=1,Type=Integer,Description="ClinVar release ${clinvarDate}">\n`
      + `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">\n`
      + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + sampleNames.join("\t") + "\n"
      + records.join("\n") + "\n";
    return new File([vcf], name);
  };
  const rare = (gts) => `1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t${gts.join("\t")}`;
  const common = (gts) => `1\t300\trs3\tG\tC\t99\tPASS\tCSQ=${PASS_CSQ.replace("|0.0002|", "|0.35|")}\tGT:DP:GQ:AD\t${gts.join("\t")}`;
  const groupA = Array.from({ length: 9 }, (_, i) => `A${i + 1}`);
  const groupB = Array.from({ length: 9 }, (_, i) => `B${i + 1}`);
  const carryFirst = (names) => names.map((name, index) => (index === 0 ? "0/1:30:99:15,15" : "0/0:30:99:30,0"));
  const fileA = fileFor("groupA.vcf", groupA, [rare(carryFirst(groupA)), common(carryFirst(groupA))], "2026-08-01");
  const fileB = fileFor("groupB.vcf", groupB, [rare(carryFirst(groupB))], "2026-06-15");

  const result = await parseVcfFiles([fileA, fileB], { aggregateMaxPopmax: 0.01 });
  assert.equal(result.summary.cohortMode, true);
  assert.equal(result.summary.separatelyCalled, true);
  assert.equal(result.summary.samples, 18);
  assert.equal(result.rows.length, 1);
  const row = result.rows[0];
  assert.equal(row.genotype, "2 carry");
  assert.equal(row.cohortSampleCount, undefined);
  assert.deepEqual(row.carriers.map((c) => c.sample).sort(), ["A1", "B1"]);
  assert.ok(result.summary.warnings.some((w) => w.includes("different ClinVar releases")));
  assert.ok(result.summary.warnings.some((w) => w.includes("not confirmed reference")));

  const smallA = fileFor("smallA.vcf", ["S1"], [rare(["0/1:30:99:15,15"])], "2026-08-01");
  const smallB = fileFor("smallB.vcf", ["S2"], [rare(["0/1:25:80:12,13"])], "2026-08-01");
  const family = await parseVcfFiles([smallA, smallB]);
  assert.ok(!family.summary.cohortMode);
  assert.equal(family.rows.length, 2);
  assert.equal(family.rows[0].genotype, "0/1");
});

test("aggregation: a carrier-less first sighting cannot orphan the allele", async () => {
  const fileFor = (name, sampleNames, records) => new File([
    "##fileformat=VCFv4.2\n"
    + "##reference=GRCh38\n"
    + "##contig=<ID=1,length=248956422>\n"
    + `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">\n`
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + sampleNames.join("\t") + "\n"
    + records.join("\n") + "\n",
  ], name);
  const site = (gts) => `1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t${gts.join("\t")}`;
  const groupA = Array.from({ length: 9 }, (_, i) => `A${i + 1}`);
  const groupB = Array.from({ length: 9 }, (_, i) => `B${i + 1}`);
  // File A sees the site but nobody carries it; file B brings the carrier.
  const fileA = fileFor("groupA.vcf", groupA, [site(groupA.map(() => "0/0:30:99:30,0"))]);
  const fileB = fileFor("groupB.vcf", groupB, [site(groupB.map((_, i) => (i === 0 ? "0/1:30:99:15,15" : "0/0:30:99:30,0")))]);
  const result = await parseVcfFiles([fileA, fileB], { aggregateMaxPopmax: 0.01 });
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].genotype, "1 carry");
  assert.deepEqual(result.rows[0].carriers.map((c) => c.sample), ["B1"]);
});

test("half-called genotypes stay carriers in cohort mode and are QC-flagged everywhere", async () => {
  const samples = Array.from({ length: 16 }, (_, i) => `S${i + 1}`);
  const gts = samples.map((_, i) => (i === 0 ? "./1:30:99:15,15" : "0/0:30:99:30,0"));
  const vcf = "##fileformat=VCFv4.2\n"
    + "##reference=GRCh38\n"
    + "##contig=<ID=1,length=248956422>\n"
    + `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">\n`
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + samples.join("\t") + "\n"
    + `1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t${gts.join("\t")}\n`;
  const cohort = await parseVcfFiles([new File([vcf], "cohort.vcf")]);
  assert.equal(cohort.rows.length, 1);
  assert.deepEqual(cohort.rows[0].carriers.map((c) => c.sample), ["S1"]);

  const single = await parseVcfFiles([new File([
    vcf.replace("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + samples.join("\t"),
      "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1")
      .replace(`GT:DP:GQ:AD\t${gts.join("\t")}`, "GT:DP:GQ:AD\t./1:30:99:15,15"),
  ], "single.vcf")]);
  const failures = variantQcFailures(single.rows[0], STANDARD_VARIANT_QC);
  assert.ok(failures.some((f) => f.includes("partially called")));
});

test("a cohort row passes QC when any carrier passes, fails only when all do", () => {
  const evidence = (gq, alleleBalance) => ({
    gt: "0/1", called: true, carrier: true, dp: 30, gq, adRef: 15, adAlt: 15,
    alleleBalance, pl: null, phased: false, phaseSet: "", phaseHaplotype: null,
    genotypeClass: "heterozygous", genotypeFilter: "",
  });
  // Representative (highest GQ) fails allele balance; the other carrier passes.
  const row = {
    genotype: "2/16 carry", dp: 30, gq: 99, adRef: 28, adAlt: 2,
    alleleBalance: 0.06, qual: 99, genotypeClass: "heterozygous",
    carriers: [
      { sample: "S1", evidence: evidence(99, 0.06) },
      { sample: "S2", evidence: evidence(60, 0.5) },
    ],
  };
  assert.deepEqual(variantQcFailures(row, STANDARD_VARIANT_QC), []);
  const allFail = { ...row, carriers: [
    { sample: "S1", evidence: evidence(99, 0.06) },
    { sample: "S2", evidence: evidence(60, 0.04) },
  ] };
  const failures = variantQcFailures(allFail, STANDARD_VARIANT_QC);
  assert.ok(failures.some((f) => f.includes("no carrier passes QC (2 carriers)")));
});

test("server-restored record slices stay per-sample even at 16+ samples", async () => {
  const samples = Array.from({ length: 18 }, (_, i) => `S${i + 1}`);
  const gts = samples.map((_, i) => (i < 2 ? "0/1:30:99:15,15" : "0/0:30:99:30,0"));
  const vcf = "##fileformat=VCFv4.2\n"
    + "##reference=GRCh38\n"
    + "##contig=<ID=chr1,length=248956422>\n"
    + `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">\n`
    + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + samples.join("\t") + "\n"
    + `chr1\t100\trs1\tA\tG\t99\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t${gts.join("\t")}\n`;
  const restored = await parseVcfFiles(
    [new File([vcf], "slice.vcf")],
    { intake: "server-records" },
  );
  assert.ok(!restored.summary.cohortMode);
  assert.deepEqual(restored.rows.map((row) => row.sample).sort(), ["S1", "S2"]);
});

test("one-row-per-variant collapse prefers MANE Select and keeps the rest as a chip", () => {
  const base = { sample: "P1", chrom: "19", pos: 1620980, ref: "G", alt: "A", picked: true, impact: "HIGH" };
  const maneSelect = { ...base, key: "a", gene: "TCF3", transcript: "ENST00000262965", mane: true, maneSelect: true };
  const manePlus = { ...base, key: "b", gene: "TCF3", transcript: "ENST00000588136", mane: true, maneSelect: false };
  const neighborGene = { ...base, key: "c", gene: "LINC1", transcript: "ENST00000756700", mane: false, maneSelect: false, impact: "MODIFIER" };
  const otherVariant = { ...base, key: "d", pos: 999, gene: "TCF3", transcript: "ENST00000262965", mane: true, maneSelect: true };
  const collapsed = collapseToOneRowPerVariant([manePlus, neighborGene, maneSelect, otherVariant]);
  assert.equal(collapsed.length, 2);
  const merged = collapsed.find((row) => row.pos === 1620980);
  assert.equal(merged.transcript, "ENST00000262965");
  assert.deepEqual(merged.collapsedTranscriptRows.map((row) => row.key).sort(), ["b", "c"]);
  const twoSamples = collapseToOneRowPerVariant([maneSelect, { ...manePlus, sample: "P2" }]);
  assert.equal(twoSamples.length, 2);
});

test("preserves separate disease-specific ClinGen expert assertions", async () => {
  const tokens = [
    ["G", "uuid-a", "CA1", "Pathogenic", "Disease A", "MONDO:1", "Autosomal dominant inheritance", "Panel A", "2026-01-01"],
    ["G", "uuid-b", "CA1", "Uncertain Significance", "Disease B", "MONDO:2", "Autosomal recessive inheritance", "Panel A", "2026-02-01"],
  ].map((fields) => fields.map((value) => encodeURIComponent(value)).join("|")).join(",");
  const vcf = VCF.replace(
    `CSQ=${PASS_CSQ};IEI_UNSCORED_INDEL=SpliceAI_intronic&PromoterAI_promoter`,
    `CSQ=${PASS_CSQ};ClinGen_ERepo=${tokens}`,
  );
  const result = await parseVcfFiles([new File([vcf], "clingen.vcf")]);
  assert.equal(result.rows[0].clingenErepo.length, 2);
  assert.deepEqual(result.rows[0].clingenErepo.map((item) => item.disease), ["Disease A", "Disease B"]);
  assert.equal(result.rows[0].clingenErepo[0].modeOfInheritance, "Autosomal dominant inheritance");
});

test("keeps Number=A ClinGen assertions isolated to their ALT allele", async () => {
  const encodeAssertion = (alt, uuid, disease) => [
    alt, uuid, `CA-${uuid}`, "Pathogenic", disease, "MONDO:1",
    "Autosomal dominant inheritance", "Panel", "2026-01-01",
  ].map((value) => encodeURIComponent(value)).join("|");
  const fields = ["Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL", "PICK"];
  const gAssertions = [
    encodeAssertion("G", "g1", "Disease G1"),
    encodeAssertion("G", "g2", "Disease G2"),
  ].join("&");
  const tAssertion = encodeAssertion("T", "t1", "Disease T");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "##INFO=<ID=ClinGen_ERepo,Number=A,Type=String,Description=\"per ALT\">",
    "##INFO=<ID=ClinGen_ERepo_count,Number=A,Type=Integer,Description=\"per ALT\">",
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t100\t.\tA\tG,T\t99\tPASS\tCSQ=G|1|missense_variant|MODERATE|GENE1|1,T|2|missense_variant|MODERATE|GENE2|1;ClinGen_ERepo=${gAssertions},${tAssertion};ClinGen_ERepo_count=2,1\tGT\t1/2`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "clingen-number-a.vcf")]);
  const byAlt = new Map(result.rows.map((row) => [row.alt, row]));
  assert.deepEqual(
    byAlt.get("G").clingenErepo.map((item) => item.disease),
    ["Disease G1", "Disease G2"],
  );
  assert.deepEqual(
    byAlt.get("T").clingenErepo.map((item) => item.disease),
    ["Disease T"],
  );
  assert.equal(byAlt.get("G").predictions.clingen_erepo_assertions.values.count, 2);
  assert.equal(
    byAlt.get("G").predictions.clingen_erepo_assertions.provenance.assertions,
    gAssertions,
  );
  assert.equal(byAlt.get("T").predictions.clingen_erepo_assertions.values.count, 1);
  assert.equal(
    byAlt.get("T").predictions.clingen_erepo_assertions.provenance.assertions,
    tAssertion,
  );
});

test("filters legacy record-wide ClinGen observations by encoded ALT", async () => {
  const encodeAssertion = (alt, uuid) => [
    alt, uuid, `CA-${uuid}`, "Pathogenic", `Disease ${uuid}`, "MONDO:1",
    "Autosomal dominant inheritance", "Panel", "2026-01-01",
  ].map((value) => encodeURIComponent(value)).join("|");
  const fields = ["Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL", "PICK"];
  const gAssertions = [encodeAssertion("G", "g1"), encodeAssertion("G", "g2")];
  const tAssertion = encodeAssertion("T", "t1");
  const legacyAssertions = [...gAssertions, tAssertion].join(",");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "##INFO=<ID=ClinGen_ERepo,Number=.,Type=String,Description=\"legacy record wide\">",
    "##INFO=<ID=ClinGen_ERepo_count,Number=1,Type=Integer,Description=\"legacy record wide\">",
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t100\t.\tA\tG,T\t99\tPASS\tCSQ=G|1|missense_variant|MODERATE|GENE1|1,T|2|missense_variant|MODERATE|GENE2|1;ClinGen_ERepo=${legacyAssertions};ClinGen_ERepo_count=3\tGT\t1/2`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "clingen-legacy.vcf")]);
  const byAlt = new Map(result.rows.map((row) => [row.alt, row]));
  assert.equal(byAlt.get("G").predictions.clingen_erepo_assertions.values.count, 2);
  assert.equal(byAlt.get("T").predictions.clingen_erepo_assertions.values.count, 1);
  assert.equal(
    byAlt.get("G").predictions.clingen_erepo_assertions.provenance.assertions,
    gAssertions.join("&"),
  );
  assert.equal(
    byAlt.get("T").predictions.clingen_erepo_assertions.provenance.assertions,
    tAssertion,
  );
});

test("normalizes regional context and ClinVar amino-acid flags as allele observations", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Feature", "PICK",
    "RepeatMasker", "SegDup", "ClinVar_path_aa_match",
    "ClinVar_path_aa_change_match",
  ];
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    "1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE1|ENST1|1|LINE_L1|0.992|1|0\tGT\t0/1",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "allele-context.vcf")]);
  const observations = result.rows[0].predictions;
  assert.deepEqual(observations.repeatmasker_context.values, { overlap: "LINE_L1" });
  assert.deepEqual(observations.segdup_context.values, { overlap: "0.992" });
  assert.deepEqual(observations.clinvar_aa_match.values, {
    residue_match: true,
    change_match: false,
  });
  assert.equal(observations.repeatmasker_context.scope, "allele");
  assert.equal(observations.segdup_context.scope, "allele");
  assert.equal(observations.clinvar_aa_match.scope, "allele");
});

test("models SpliceAI as an allele-and-source-gene predictor with delta positions", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature", "PICK",
    "SpliceAI_pred_SYMBOL", "SpliceAI_pred_DS_AG", "SpliceAI_pred_DS_AL",
    "SpliceAI_pred_DS_DG", "SpliceAI_pred_DS_DL", "SpliceAI_pred_DP_AG",
    "SpliceAI_pred_DP_AL", "SpliceAI_pred_DP_DG", "SpliceAI_pred_DP_DL",
  ];
  const csq = [
    "G", "splice_region_variant", "MODERATE", "GENE1", "ENSG1", "ENST1", "1",
    "GENE1", "0.31", "0.02", "0.7", "0.01", "-12", "4", "8", "-3",
  ].join("|");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t100\t.\tA\tG\t99\tPASS\tCSQ=${csq}\tGT\t0/1`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "spliceai.vcf")]);
  assert.deepEqual(result.rows[0].predictions.spliceai, {
    scope: "allele_gene_symbol",
    matchStatus: "exact",
    matchedOn: [
      "chromosome", "position", "reference", "alternate", "gene_symbol",
    ],
    values: {
      delta_acceptor_gain: 0.31,
      delta_acceptor_loss: 0.02,
      delta_donor_gain: 0.7,
      delta_donor_loss: 0.01,
    },
    target: { gene_symbol: "GENE1" },
    provenance: {
      source_gene_symbol: "GENE1",
      delta_position_acceptor_gain: -12,
      delta_position_acceptor_loss: 4,
      delta_position_donor_gain: 8,
      delta_position_donor_loss: -3,
    },
  });
});

test("drops malformed predictor metrics without rejecting the VCF", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature", "PICK",
    "SpliceAI_pred_SYMBOL", "SpliceAI_pred_DS_AG", "SpliceAI_pred_DP_AG",
    "REVEL_score",
  ];
  const csq = [
    "G", "splice_region_variant", "MODERATE", "GENE1", "ENSG1", "ENST1", "1",
    "GENE1", "0.5", "137", "1.0001",
  ].join("|");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t100\t.\tA\tG\t99\tPASS\tCSQ=${csq}\tGT\t0/1`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "invalid-metric.vcf")]);

  assert.equal(result.rows.length, 1);
  assert.deepEqual(result.rows[0].predictions.spliceai, {
    scope: "allele_gene_symbol",
    matchStatus: "exact",
    matchedOn: [
      "chromosome", "position", "reference", "alternate", "gene_symbol",
    ],
    values: { delta_acceptor_gain: 0.5 },
    target: { gene_symbol: "GENE1" },
    provenance: {
      source_gene_symbol: "GENE1",
      invalid_metrics: "delta_position_acceptor_gain",
    },
  });
  assert.equal(result.rows[0].predictions.revel, undefined);
});

test("normalizes PromoterAI strand encoding and supports legacy VEP STRAND", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature", "PICK",
    "STRAND", "PromoterAI_score", "PromoterAI_TSS", "PromoterAI_strand",
    "PromoterAI_source_transcript", "PromoterAI_match",
  ];
  const row = (tss, pluginStrand) => [
    "G", "upstream_gene_variant", "MODIFIER", "GENE1", "ENSG1", "ENST1", "1",
    "-1", "-0.8", tss, pluginStrand, "ENST1", "stable_id",
  ].join("|");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t100\t.\tA\tG\t99\tPASS\tCSQ=${row("90", "-1")}\tGT\t0/1`,
    `1\t110\t.\tA\tG\t99\tPASS\tCSQ=${row("90", "")}\tGT\t0/1`,
    `1\t120\t.\tA\tG\t99\tPASS\tCSQ=${row("90&91", "-1")}\tGT\t0/1`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "promoter-strand.vcf")]);

  for (const prediction of result.rows.slice(0, 2).map(
    (variant) => variant.predictions.promoterai,
  )) {
    assert.equal(prediction.matchStatus, "exact");
    assert.equal(prediction.target.strand, "-");
    assert.equal(prediction.provenance.strand, "-");
    assert.equal(prediction.provenance.tss, 90);
    assert.equal(prediction.values.score, -0.8);
  }
  const ambiguous = result.rows[2].predictions.promoterai;
  assert.equal(ambiguous.matchStatus, "partial");
  assert.equal(ambiguous.target.tss, undefined);
  assert.equal(ambiguous.provenance.tss, undefined);
  assert.deepEqual(ambiguous.values, {});
  assert.equal(ambiguous.provenance.withheld_metrics, "score");
  assert.equal(result.rows[2].promoterAI, null);
});

test("prefers the selected WGS CADD plugin over a duplicate dbNSFP value", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL",
    "CADD_phred", "CADD_PHRED", "CADD_raw", "CADD_RAW",
  ];
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    "1\t1000000\t.\tG\tA\t99\tPASS\tCSQ=A|intergenic_variant|MODIFIER|—|35|21.5|7|2.1\tGT\t0/1",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "cadd-wgs.vcf")]);
  assert.equal(result.rows[0].cadd, 21.5);
  assert.equal(result.rows[0].caddRaw, 2.1);
});

test("rows carry no raw INFO/CSQ/FORMAT payload (removed by design)", async () => {
  // The raw-evidence panel was removed at the user's request; retaining the
  // parsed payload anyway cost memory on cohort-scale imports while nothing
  // rendered it. Restored records populate the standard evidence fields.
  const result = await parseVcfFiles([new File([VCF], "raw.vcf")], {});
  assert.ok(result.rows.length > 0);
  for (const row of result.rows) {
    assert.equal("rawVcfEvidence" in row && row.rawVcfEvidence !== undefined, false);
  }
});
test("parses strict LoGoFunc evidence and exposes it beside the MANE transcript", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature",
    "HGVSp", "MANE_SELECT", "PICK", "LoGoFunc_prediction",
    "LoGoFunc_neutral", "LoGoFunc_GOF", "LoGoFunc_LOF",
    "LoGoFunc_allele_available", "LoGoFunc_source_transcript",
    "LoGoFunc_source_HGVSp", "LoGoFunc_match",
  ];
  const mane = [
    "G", "missense_variant", "MODERATE", "GENE1", "ENSG1", "ENST_MANE",
    "ENSP_MANE:p.Lys1Arg", "NM_1", "1", "", "", "", "", "1",
    "ENST_SOURCE", "ENSP_SOURCE:p.Lys1Arg", "allele_only",
  ].join("|");
  const sourceMatch = [
    "G", "missense_variant", "MODERATE", "GENE1", "ENSG1", "ENST_SOURCE",
    "ENSP_SOURCE:p.Lys1Arg", "", "", "GOF", "0.05", "0.9", "0.05", "1",
    "ENST_SOURCE", "ENSP_SOURCE:p.Lys1Arg", "allele_transcript_protein",
  ].join("|");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t150\t.\tA\tG\t99\tPASS\tCSQ=${mane},${sourceMatch}\tGT:DP:GQ:AD\t0/1:30:99:15,15`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "logofunc.vcf")]);
  const maneRow = result.rows.find((row) => row.transcript === "ENST_MANE");
  const sourceRow = result.rows.find((row) => row.transcript === "ENST_SOURCE");

  assert.equal(sourceRow.loGoFuncMatch, "allele_transcript_protein");
  assert.equal(sourceRow.loGoFuncPrediction, "GOF");
  assert.equal(sourceRow.loGoFuncGof, 0.9);
  assert.deepEqual(sourceRow.predictions.logofunc, {
    scope: "allele_transcript_protein",
    matchStatus: "exact",
    matchedOn: [
      "chromosome", "position", "reference", "alternate",
      "ensembl_transcript", "protein_position", "amino_acid_change",
    ],
    values: { prediction: "GOF", neutral: 0.05, gof: 0.9, lof: 0.05 },
    target: {
      ensembl_transcript: "ENST_SOURCE",
      protein_position: "1",
      amino_acid_change: "ENSP_SOURCE:p.Lys1Arg",
    },
    provenance: {
      allele_available: true,
      match: "allele_transcript_protein",
    },
  });
  assert.equal(maneRow.loGoFuncPrediction, "GOF");
  assert.equal(maneRow.loGoFuncSourceTranscript, "ENST_SOURCE");
  assert.equal(maneRow.loGoFuncMatch, "source_transcript_match_elsewhere");
  assert.deepEqual(maneRow.predictions.logofunc, sourceRow.predictions.logofunc);
});

test("retains allele-only LoGoFunc provenance without assigning a score", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature",
    "HGVSp", "PICK", "LoGoFunc_prediction", "LoGoFunc_neutral",
    "LoGoFunc_GOF", "LoGoFunc_LOF", "LoGoFunc_allele_available",
    "LoGoFunc_source_transcript", "LoGoFunc_source_HGVSp", "LoGoFunc_match",
  ];
  const consequence = [
    "G", "missense_variant", "MODERATE", "GENE1", "ENSG1", "ENST_QUERY",
    "ENSP_QUERY:p.Lys1Arg", "1", "", "", "", "", "1",
    "ENST_SOURCE", "ENSP_SOURCE:p.Lys1Arg", "allele_only",
  ].join("|");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t150\t.\tA\tG\t99\tPASS\tCSQ=${consequence}\tGT:DP:GQ:AD\t0/1:30:99:15,15`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "logofunc-partial.vcf")]);
  assert.deepEqual(result.rows[0].predictions.logofunc, {
    scope: "allele_transcript_protein",
    matchStatus: "partial",
    matchedOn: ["chromosome", "position", "reference", "alternate"],
    values: {},
    target: {
      ensembl_transcript: "ENST_SOURCE",
      protein_position: "1",
      amino_acid_change: "ENSP_SOURCE:p.Lys1Arg",
    },
    provenance: { allele_available: true, match: "allele_only" },
  });
});

test("parses FuncVEP scores as exact allele-and-gene predictor observations", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Gene", "Feature", "PICK",
    "FuncVEP_CTI", "FuncVEP_CTE", "FuncVEP_SP", "FuncVEP_allele_available",
    "FuncVEP_match", "FuncVEP_match_status", "FuncVEP_source_gene",
  ];
  const scored = [
    "G", "missense_variant", "MODERATE", "GENE1", "ENSG00000123456",
    "ENST00000123456", "1", "0.912", "0.731", "0.445", "1",
    "allele_gene", "exact", "ENSG00000123456",
  ].join("|");
  const unscored = [
    "T", "missense_variant", "MODERATE", "GENE2", "ENSG00000654321",
    "ENST00000654321", "1", "", "", "", "1",
    "allele_only", "partial", "ENSG00000999999",
  ].join("|");
  const partialScored = [
    "G", "missense_variant", "MODERATE", "GENE3", "ENSG00000777777",
    "ENST00000777777", "1", "0.9", "0.8", "0.7", "1",
    "allele_only", "partial", "ENSG00000888888",
  ].join("|");
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t150\t.\tA\tG\t99\tPASS\tCSQ=${scored}\tGT:DP:GQ:AD\t0/1:30:99:15,15`,
    `1\t250\t.\tC\tT\t99\tPASS\tCSQ=${unscored}\tGT:DP:GQ:AD\t0/1:30:99:15,15`,
    `1\t350\t.\tA\tG\t99\tPASS\tCSQ=${partialScored}\tGT:DP:GQ:AD\t0/1:30:99:15,15`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "funcvep.vcf")]);
  const scoredRow = result.rows.find((row) => row.pos === 150);
  const unscoredRow = result.rows.find((row) => row.pos === 250);
  const partialScoredRow = result.rows.find((row) => row.pos === 350);

  assert.equal(scoredRow.funcVepCti, 0.912);
  assert.equal(scoredRow.funcVepCte, 0.731);
  assert.equal(scoredRow.funcVepSp, 0.445);
  assert.equal(scoredRow.geneId, "ENSG00000123456");
  assert.deepEqual(scoredRow.predictions.funcvep, {
    scope: "allele_gene",
    matchStatus: "exact",
    matchedOn: [
      "chromosome", "position", "reference", "alternate", "ensembl_gene",
    ],
    target: { ensembl_gene: "ENSG00000123456" },
    values: { cti: 0.912, cte: 0.731, sp: 0.445 },
    provenance: {
      allele_available: true,
      match: "allele_gene",
      match_status: "exact",
    },
  });

  assert.equal(unscoredRow.funcVepCti, null);
  assert.equal(unscoredRow.funcVepCte, null);
  assert.equal(unscoredRow.funcVepSp, null);
  assert.deepEqual(unscoredRow.predictions.funcvep, {
    scope: "allele_gene",
    matchStatus: "partial",
    matchedOn: ["chromosome", "position", "reference", "alternate"],
    target: { ensembl_gene: "ENSG00000999999" },
    values: {},
    provenance: {
      allele_available: true,
      match: "allele_only",
      match_status: "partial",
    },
  });
  assert.equal(partialScoredRow.funcVepCti, null);
  assert.equal(partialScoredRow.funcVepCte, null);
  assert.equal(partialScoredRow.funcVepSp, null);
  assert.deepEqual(partialScoredRow.predictions.funcvep.values, {});
  assert.equal(
    partialScoredRow.predictions.funcvep.provenance.withheld_metrics,
    "cte,cti,sp",
  );
});

test("uses final-publication FuncVEP binary thresholds from the predictor registry", () => {
  const cases = [
    ["cti", 0.419606448098318],
    ["cte", 0.519261866786599],
    ["sp", 0.440940891937106],
  ];
  for (const [metric, threshold] of cases) {
    assert.deepEqual(
      predictorBinaryClassification("funcvep", metric, threshold),
      {
        label: "Damaging",
        isPositive: true,
        threshold,
        comparison: "greater_than_or_equal",
        thresholdSet: "Kayaalp et al., Nature Genetics 2026, Supplementary Table 13",
        sourceUrl: "https://www.nature.com/articles/s41588-026-02727-3",
      },
    );
    assert.equal(
      predictorBinaryClassification("funcvep", metric, threshold - 1e-12).label,
      "Neutral",
    );
  }
  assert.equal(predictorBinaryClassification("funcvep", "cti", null), null);
});

test("retains reference parental genotypes and uses allele-specific GT and AD", async () => {
  const fields = ["Allele", "ALLELE_NUM", "Consequence", "IMPACT", "SYMBOL", "PICK"];
  const trioVcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCHILD\tMOTHER\tFATHER",
    "1\t400\t.\tA\tG,T\t99\tPASS\tCSQ=G|1|missense_variant|MODERATE|GENE1|1,T|2|missense_variant|MODERATE|GENE2|1\tGT:DP:GQ:AD\t0/2:30:99:15,0,15\t0/0:35:99:35,0,0\t0/0:32:99:32,0,0",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([trioVcf], "trio.vcf")]);
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].alt, "T");
  assert.equal(result.rows[0].gene, "GENE2");
  assert.equal(result.rows[0].adAlt, 15);
  assert.equal(result.rows[0].sampleGenotypes.MOTHER.gt, "0/0");
  assert.equal(result.rows[0].sampleGenotypes.MOTHER.called, true);
  assert.equal(result.rows[0].sampleGenotypes.MOTHER.carrier, false);
});

test("recognizes gzip data by magic bytes even without a .gz suffix", async () => {
  const compressed = gzipSync(Buffer.from(VCF));
  const file = new File([compressed], "patient.vep.vcf");
  const result = await parseVcfFiles([file]);
  assert.equal(result.rows.length, 1);
});

test("imports concatenated BGZF blocks produced by bgzip", async () => {
  const split = VCF.indexOf("#CHROM");
  const compressed = Buffer.concat([
    bgzfBlock(VCF.slice(0, split)),
    bgzfBlock(VCF.slice(split)),
    bgzfBlock(""),
  ]);
  const file = new File([compressed], "patient.vep.aamatch.vcf.gz", {
    type: "application/gzip",
  });
  const result = await parseVcfFiles([file]);
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].gene, "NFKB1");
});

test("parses sample- and transcript-specific frame-restoration evidence", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Feature", "PICK",
    "ClinVar_CLNSIG", "ClinVar_CLNSIGCONF",
  ];
  const event = [
    "1:300:A:AT", "PATIENT", "ENST0001", "FRAME_RESTORED_CONFIRMED",
    "1:315:AG:A", "ENSP0001:10AB%3ECD",
  ].join("|");
  const haplotypeVcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "##INFO=<ID=IEI_HAPLOTYPE_FRAME,Number=.,Type=String,Description=\"test\">",
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    `1\t300\t.\tA\tAT\t99\tPASS\tCSQ=AT|frameshift_variant|HIGH|GENE1|ENST0001|1|Conflicting_classifications_of_pathogenicity|Pathogenic(1)%26Uncertain_significance(2);IEI_HAPLOTYPE_FRAME=${event}\tGT\t1/1`,
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([haplotypeVcf], "haplotype.vcf")]);
  assert.equal(result.rows[0].haplotypeFrameStatus, "FRAME_RESTORED_CONFIRMED");
  assert.deepEqual(result.rows[0].haplotypeFramePartners, ["1:315:AG:A"]);
  assert.equal(result.rows[0].haplotypeProteinChange, "ENSP0001:10AB>CD");
  assert.deepEqual(result.rows[0].predictions.haplotype_frame, {
    scope: "sample_haplotype",
    matchStatus: "partial",
    matchedOn: [
      "chromosome", "position", "reference", "alternate", "sample",
      "ensembl_transcript",
    ],
    values: {
      frame_evidence: [
        "1:300:A:AT", "PATIENT", "ENST0001", "FRAME_RESTORED_CONFIRMED",
        "1:315:AG:A", "ENSP0001:10AB>CD",
      ].join("|"),
    },
    target: { sample: "PATIENT", ensembl_transcript: "ENST0001" },
  });
  assert.equal(
    result.rows[0].clinvarConflictingEvidence,
    "Pathogenic(1)&Uncertain_significance(2)",
  );
});

test("retains transcript-specific LOFTEE reasons, flags, and PTC recalculation", async () => {
  const fields = [
    "Allele", "Consequence", "IMPACT", "SYMBOL", "Feature", "PICK",
    "LoF", "LoF_filter", "LoF_flags", "LoF_50_BP_RULE_PTC",
    "LoF_50_BP_RULE_original", "LoF_50_BP_RULE_changed",
    "PTC_dist_from_last_exon", "PTC_calc_status",
  ];
  const lofteeVcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=19,length=58617616>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    "19\t44274643\t.\tGT\tG\t99\tPASS\tCSQ=-|frameshift_variant|HIGH|ZNF233|ENST00000683810|1|LC|END_TRUNC%26ANC_ALLELE|PHYLOCSF_WEAK|FAIL|PASS|1|-1874|ok\tGT:DP:GQ:AD\t1/1:85:99:0,85",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([lofteeVcf], "loftee.vcf")]);
  assert.equal(result.rows[0].loftee, "LC");
  assert.equal(result.rows[0].lofteeFilter, "END_TRUNC&ANC_ALLELE");
  assert.equal(result.rows[0].lofteeFlags, "PHYLOCSF_WEAK");
  assert.equal(result.rows[0].loftee50bp, "FAIL");
  assert.equal(result.rows[0].ptcDistanceFromLastExon, -1874);
  assert.equal(result.rows[0].ptcCalcStatus, "ok");
  assert.deepEqual(result.rows[0].predictions.loftee_ptc_50bp, {
    scope: "transcript_consequence",
    matchStatus: "exact",
    matchedOn: [
      "chromosome", "position", "reference", "alternate",
      "ensembl_transcript", "consequence",
    ],
    values: { ptc_distance: -1874, ptc_rule: "FAIL", rule_changed: true },
    target: {
      ensembl_transcript: "ENST00000683810",
      consequence: "frameshift_variant",
    },
    provenance: { original_rule: "PASS", calculation_status: "ok" },
  });
});

test("reports an invalid file named .vcf.gz", async () => {
  const file = new File(["not gzip"], "broken.vcf.gz", { type: "application/gzip" });
  await assert.rejects(
    parseVcfFiles([file]),
    /gzip\/BGZF decompression failed/,
  );
});

test("rejects an annotated GRCh37 VCF instead of mixing assemblies", async () => {
  const grch37 = VCF.replace(
    "##reference=GRCh38\n##contig=<ID=1,length=248956422>",
    "##reference=GRCh37\n##contig=<ID=1,length=249250621>",
  );
  await assert.rejects(
    parseVcfFiles([new File([grch37], "grch37.vep.vcf")]),
    /GRCh37\/hg19/,
  );
});

test("retains both loci for a lifted GRCh37 variant", async () => {
  const lifted = VCF
    .replace(
      "##reference=GRCh38",
      "##reference=GRCh38\n"
      + "##iei_target_assembly=GRCh38\n"
      + "##iei_liftover=<SourceAssembly=GRCh37/hg19,TargetAssembly=GRCh38>",
    )
    .replace(
      `CSQ=${PASS_CSQ}`,
      `IEI_LIFTOVER;IEI_ASSEMBLY_ALLELE_SWAP;IEI_ORIGINAL_ASSEMBLY=GRCh37;IEI_ORIGINAL_CHROM=1;IEI_ORIGINAL_POS=99;IEI_ORIGINAL_REF=A;IEI_ORIGINAL_ALT=G;CSQ=${PASS_CSQ}`,
    );
  const result = await parseVcfFiles([new File([lifted], "lifted.vep.vcf")]);
  assert.equal(result.rows[0].liftedFromGrch37, true);
  assert.equal(result.rows[0].assemblyAlleleSwap, true);
  assert.equal(result.rows[0].originalPos, 99);
  assert.equal(result.rows[0].originalAlt, "G");
});

test("legacy single-transcript VEP output without PICK gets a safe fallback", async () => {
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL", "Feature"];
  const legacyVcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    "1\t300\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|LEGACY|ENST_LEGACY\tGT\t0/1",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([legacyVcf], "legacy.vcf")]);
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].mane, false);
  assert.equal(result.rows[0].picked, true);
  assert.equal(preferredClinicalTranscriptRows(result.rows).length, 1);
});

test("compound-het grouping requires two distinct eligible variants", () => {
  const base = {
    sample: "PATIENT",
    gene: "IDUA",
    genotype: "0/1",
    chrom: "4",
    ref: "C",
    alt: "T",
  };
  const missense = { ...base, pos: 1004329 };
  const commonSynonymous = { ...base, pos: 1002080 };

  assert.equal(candidateCompoundHetKeys([missense]).size, 0);
  assert(candidateCompoundHetKeys([missense, commonSynonymous]).has("PATIENT:IDUA"));
  assert.equal(candidateCompoundHetKeys([missense, { ...missense }]).size, 0);
});

test("clinical transcript default retains MANE and uses PICK only as fallback", () => {
  const base = {
    source: "cohort.vcf.gz",
    chrom: "1",
    pos: 100,
    ref: "A",
    alt: "G",
    gene: "GENE1",
  };
  const mane = { ...base, key: "mane", mane: true, picked: false };
  const pickedSameGene = { ...base, key: "picked-same", mane: false, picked: true };
  const alternate = { ...base, key: "alternate", mane: false, picked: false };
  const noManePicked = {
    ...base, pos: 200, gene: "GENE2", key: "fallback", mane: false, picked: true,
  };
  const noManeAlternate = {
    ...base, pos: 200, gene: "GENE2", key: "fallback-alt", mane: false, picked: false,
  };

  assert.deepEqual(
    preferredClinicalTranscriptRows([
      mane, pickedSameGene, alternate, noManePicked, noManeAlternate,
    ]).map((row) => row.key),
    ["mane", "fallback"],
  );
});

test("rejects structurally unsafe annotated VCF input", async () => {
  const withoutAssembly = VCF
    .replace("##reference=GRCh38\n", "")
    .replace("##contig=<ID=1,length=248956422>\n", "");
  await assert.rejects(
    parseVcfFiles([new File([withoutAssembly], "unknown-build.vcf")]),
    /GRCh38 cannot be confirmed/,
  );

  const duplicateSample = VCF
    .replace("\tFORMAT\tPATIENT", "\tFORMAT\tPATIENT\tPATIENT")
    .replace(/\t0\/1:40:99:20,20$/, "\t0/1:40:99:20,20\t0/1:40:99:20,20");
  await assert.rejects(
    parseVcfFiles([new File([duplicateSample], "duplicate-sample.vcf")]),
    /duplicate or empty sample name/,
  );

  const duplicateRecord = VCF.replace(
    "1\t200\t.\tC\tT\t20\tLowQual\tCSQ=T|missense_variant|MODERATE|NFKB1||||\tGT:DP:GQ:AD\t0/1:20:30:10,10",
    `1\t100\trs1\tA\tG\t98\tPASS\tCSQ=${PASS_CSQ}\tGT:DP:GQ:AD\t0/1:38:90:19,19`,
  );
  const duplicateResult = await parseVcfFiles(
    [new File([duplicateRecord], "duplicate-record.vcf")],
  );
  assert.equal(duplicateResult.rows.length, 2);
  assert.equal(
    duplicateResult.summary.intakeQc.find((check) => check.id === "duplicates").status,
    "warning",
  );
  assert.equal(duplicateResult.rows.every((row) => row.duplicateRecord), true);

  const unsorted = VCF.replace(
    "1\t200\t.\tC\tT\t20\tLowQual",
    "1\t50\t.\tC\tT\t20\tLowQual",
  );
  await assert.rejects(
    parseVcfFiles([new File([unsorted], "unsorted.vcf")]),
    /records are not sorted/,
  );

  const invalidAllele = VCF.replace("\trs1\tA\tG\t99", "\trs1\tA\t?\t99");
  await assert.rejects(
    parseVcfFiles([new File([invalidAllele], "bad-allele.vcf")]),
    /invalid ALT allele/,
  );

  const noCsq = VCF.replace(
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP annotations. Format: ${CSQ_FIELDS.join("|")}">\n`,
    "",
  );
  await assert.rejects(
    parseVcfFiles([new File([noCsq], "no-csq.vcf")]),
    /no readable VEP CSQ Format schema/,
  );
});

test("warns about unavailable call fields and applies the standard preset", async () => {
  const missingCallFields = VCF
    .replace("GT:DP:GQ:AD\t0/1:40:99:20,20", "GT\t0/1")
    .replace("GT:DP:GQ:AD\t0/1:20:30:10,10", "GT\t0/1");
  const result = await parseVcfFiles([
    new File([missingCallFields], "missing-evidence.vcf"),
  ]);
  const formatCheck = result.summary.intakeQc.find((check) => check.id === "format");
  assert.equal(formatCheck.status, "warning");
  assert.match(formatCheck.detail, /AD, DP, GQ/);
  assert.deepEqual(
    variantQcFailures(result.rows[0], STANDARD_VARIANT_QC),
    ["DP unavailable", "GQ unavailable", "alternate depth unavailable", "allele balance unavailable", "allele balance unavailable"],
  );

  const good = await parseVcfFiles([new File([VCF], "good.vcf")]);
  assert.deepEqual(variantQcFailures(good.rows[0], STANDARD_VARIANT_QC), []);
  assert.deepEqual(
    variantQcFailures({ ...good.rows[0], genotypeFilter: "." }, STANDARD_VARIANT_QC),
    [],
  );
  assert.deepEqual(
    variantQcFailures({ ...good.rows[0], genotypeFilter: "PASS" }, STANDARD_VARIANT_QC),
    [],
  );
  assert.deepEqual(
    variantQcFailures(
      { ...good.rows[0], genotypeFilter: "LowGQ;AB" },
      STANDARD_VARIANT_QC,
    ),
    ["genotype FT LowGQ;AB"],
  );
  assert.deepEqual(
    variantQcFailures(
      { ...good.rows[0], genotypeFilter: "." },
      { ...STANDARD_VARIANT_QC, genotypeFtMode: "require_pass" },
    ),
    ["genotype FT ."],
  );
  assert.deepEqual(
    variantQcFailures(good.rows[0], { ...STANDARD_VARIANT_QC, minQd: 2 }),
    ["QD unavailable"],
  );
});

test("accepts arbitrary contiguous chromosome-block order but rejects reappearing contigs", async () => {
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL", "PICK"];
  const headers = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    "##contig=<ID=2,length=242193529>",
    "##contig=<ID=10,length=133797422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1",
  ];
  const record = (chrom, pos) => (
    `${chrom}\t${pos}\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE1|1\tGT:DP:GQ:AD\t0/1:30:99:15,15`
  );
  const lexical = [...headers, record("1", 100), record("10", 100), record("2", 100), ""].join("\n");
  const result = await parseVcfFiles([new File([lexical], "lexical-contigs.vcf")]);
  assert.equal(result.rows.length, 3);

  const reappearing = [...headers, record("1", 100), record("2", 100), record("1", 200), ""].join("\n");
  await assert.rejects(
    parseVcfFiles([new File([reappearing], "reappearing-contig.vcf")]),
    /contig 1 reappears/,
  );
});

test("streams a full WGS review BGZF without materializing decompressed text", {
  skip: !process.env.IEI_REAL_WGS_REVIEW,
}, async () => {
  const path = process.env.IEI_REAL_WGS_REVIEW;
  const compressed = await readFile(path);
  const result = await parseVcfFiles([
    new File([compressed], "real-wgs.review.prefiltered.vcf.gz", {
      type: "application/gzip",
    }),
  ]);
  assert.ok(result.summary.passRecords > 100000);
  assert.ok(result.rows.length >= result.summary.passRecords);
});

// ---- 2026-08 audit regression tests (Phase 1, webui) ----

test("preserves literal '+' in HGVS and survives a stray '%' in one CSQ field", async () => {
  // Audit UI-1/UI-2: decode() form-decoded '+' to a space (corrupting every
  // intronic HGVS) and an invalid escape aborted the whole import.
  const vcf = VCF
    .replace("ENST:c.1A>G", "ENST:c.730+1G>A")
    .replace("ENSP:p.Lys1Arg", "ENSP:p.M1V%");
  const result = await parseVcfFiles([new File([vcf], "hgvs.vcf")]);
  assert.equal(result.rows[0].hgvsC, "ENST:c.730+1G>A");
  assert.equal(result.rows[0].hgvsP, "ENSP:p.M1V%");
});

test("retains FILTER='.' records instead of discarding them as non-PASS", async () => {
  // Audit UI-3: '.' means "no filtering applied", not a failed filter.
  const vcf = VCF.replace(
    `1\t100\trs1\tA\tG\t99\tPASS\t`,
    `1\t100\trs1\tA\tG\t99\t.\t`,
  );
  const result = await parseVcfFiles([new File([vcf], "unfiltered.vcf")]);
  assert.equal(result.rows.length, 1);
  assert.equal(result.summary.excludedNonPass, 1); // only the LowQual record
});

test("indexes Number=A INFO values by allele instead of taking the maximum", async () => {
  // Audit UI-8: the rare ALT inherited the common ALT's gnomAD AF and was
  // removed by the popmax filter.
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL"];
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    "1\t100\t.\tA\tG,T\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE1,T|missense_variant|MODERATE|GENE1;gnomADg_AF=0.30,0.00001\tGT\t1/2",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "multiallelic.vcf")]);
  const byAlt = new Map(result.rows.map((row) => [row.alt, row]));
  assert.equal(byAlt.get("G").gnomadPopmax, 0.3);
  assert.equal(byAlt.get("T").gnomadPopmax, 0.00001);
});

test("does not cross-assign consequences on a multi-allelic allele-match failure", async () => {
  // Audit UI-4: without ALLELE_NUM, a minimised indel Allele ('-') matches no
  // raw ALT and every consequence was previously attached to every allele.
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL"];
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    "1\t100\t.\tCTT\tC,CT\t99\tPASS\tCSQ=-|frameshift_variant|HIGH|GENEA,-|inframe_deletion|MODERATE|GENEB\tGT\t1/2",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "indel.vcf")]);
  assert.equal(result.rows.length, 2); // both carrier alleles stay visible
  for (const row of result.rows) {
    assert.equal(row.gene, "—"); // but with no fabricated annotation
    assert.notEqual(row.gene, "GENEA");
  }
});

test("matches unique VEP-minimized alleles on multi-allelic indels", async () => {
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL"];
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT",
    "1\t100\t.\tA\tAT,AG\t99\tPASS\tCSQ=T|inframe_insertion|MODERATE|GENEA,G|inframe_insertion|MODERATE|GENEB\tGT\t1/2",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "indel-minimized.vcf")]);
  const byAlt = new Map(result.rows.map((row) => [row.alt, row.gene]));
  assert.equal(byAlt.get("AT"), "GENEA");
  assert.equal(byAlt.get("AG"), "GENEB");
});

test("emits a row for a half-called carrier genotype", async () => {
  // Audit UI-6: ./1 was treated as a non-carrier and silently dropped.
  const vcf = VCF.replace("\tGT:DP:GQ:AD\t0/1:40:99:20,20", "\tGT:DP:GQ:AD\t./1:40:99:20,20");
  const result = await parseVcfFiles([new File([vcf], "halfcall.vcf")]);
  assert.equal(result.rows.length, 1);
  const evidence = result.rows[0].sampleGenotypes.PATIENT;
  assert.equal(evidence.carrier, true);
  assert.equal(evidence.called, false);
  assert.equal(evidence.genotypeClass, "other");
});

test("a no-call with higher GQ does not overwrite a real called genotype", async () => {
  // Audit UI-5: the merge's GQ tiebreak was not gated on callability.
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL"];
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT\tMOTHER",
    "1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE1\tGT:GQ\t0/1:99\t0/0:50",
    "1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE1\tGT:GQ\t./.:80\t./.:80",
    "",
  ].join("\n");
  const result = await parseVcfFiles([new File([vcf], "dupes.vcf")]);
  const mother = result.rows[0].sampleGenotypes.MOTHER;
  assert.equal(mother.gt, "0/0");
  assert.equal(mother.gq, 50);
  assert.equal(result.rows[0].sampleGenotypes.PATIENT.gt, "0/1");
});

test("absent AD and PL parse to null, not fabricated zeros", async () => {
  // Audit UI-7: Number("") === 0 made a missing AD read as measured zero depth.
  const vcf = VCF.replace("\tGT:DP:GQ:AD\t0/1:40:99:20,20", "\tGT:DP:GQ\t0/1:40:99");
  const result = await parseVcfFiles([new File([vcf], "noad.vcf")]);
  const evidence = result.rows[0].sampleGenotypes.PATIENT;
  assert.equal(evidence.adRef, null);
  assert.equal(evidence.adAlt, null);
  assert.equal(evidence.pl, null);
  assert.equal(result.rows[0].adRef, null);
});

test("counts 1/2 as heterozygous for compound-het candidacy", () => {
  // Audit UI-11: two distinct ALT alleles at one locus are necessarily in
  // trans; the reference-allele requirement excluded exactly that case.
  assert.equal(isHeterozygousGenotype("1/2"), true);
  assert.equal(isHeterozygousGenotype("0/1"), true);
  assert.equal(isHeterozygousGenotype("1/1"), false);
  assert.equal(isHeterozygousGenotype("./1"), false);
  const rows = [
    { sample: "S", gene: "G1", genotype: "1/2", chrom: "1", pos: 100, ref: "A", alt: "G" },
    { sample: "S", gene: "G1", genotype: "1/2", chrom: "1", pos: 100, ref: "A", alt: "T" },
  ];
  assert.equal(candidateCompoundHetKeys(rows).has("S:G1"), true);
});

test("QC genotype-class fallback does not classify uncalled or ref genotypes as hom-alt", () => {
  // Audit UI-12: ./. and haploid 0 were routed into hom-alt allele-balance QC.
  const base = {
    dp: 30, gq: 99, adRef: 15, adAlt: 15, alleleBalance: 0.5,
    qual: 99, qd: null, mq: null, fs: null, sor: null,
    mqRankSum: null, readPosRankSum: null, baseQRankSum: null,
    genotypeFilter: "",
  };
  for (const genotype of ["./.", "0", "0/0"]) {
    const failures = variantQcFailures({ ...base, genotype }, STANDARD_VARIANT_QC);
    assert.equal(
      failures.some((failure) => failure.includes("allele balance")),
      false,
      `${genotype}: ${failures.join("; ")}`,
    );
  }
});

test("merges genotype evidence across chr-prefixed and unprefixed files", async () => {
  // Audit UI-17: the evidence merge key was not contig-normalized, so a trio
  // delivered as mixed-naming single-sample VCFs never merged.
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL"];
  const header = (contig) => [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    `##contig=<ID=${contig},length=248956422>`,
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
  ];
  const proband = [
    ...header("chr1"),
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCHILD",
    "chr1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE1\tGT\t0/1",
    "",
  ].join("\n");
  const parent = [
    ...header("1"),
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tMOTHER",
    "1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE1\tGT\t0/0",
    "",
  ].join("\n");
  const result = await parseVcfFiles([
    new File([proband], "child.vcf"),
    new File([parent], "mother.vcf"),
  ]);
  const childRow = result.rows.find((row) => row.sample === "CHILD");
  assert.ok(childRow.sampleGenotypes.MOTHER, "mother evidence merged across naming styles");
  assert.equal(childRow.sampleGenotypes.MOTHER.gt, "0/0");
});
