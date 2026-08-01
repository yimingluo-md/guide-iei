import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { deflateRawSync, gzipSync } from "node:zlib";
import ts from "typescript";

const source = await readFile(new URL("../app/vcf.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  candidateCompoundHetKeys,
  STANDARD_VARIANT_QC,
  parseVcfFiles,
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
  assert.equal(result.rows[0].pLi, 0.997);
  assert.equal(result.rows[0].loeuf, 0.21);
  assert.equal(result.rows[0].promoterAI, -0.91);
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
  assert.equal(result.summary.intakeQc.length, 10);
  assert.equal(result.summary.intakeQc.every((check) => check.status === "pass"), true);
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

test("optionally retains populated INFO, CSQ, and FORMAT fields for on-demand review", async () => {
  const result = await parseVcfFiles(
    [new File([VCF], "cohort-source.vcf")],
    { retainRawAnnotations: true },
  );
  const row = result.rows[0];
  assert.equal(row.rawVcfEvidence.info.CSQ, undefined);
  assert.equal(row.rawVcfEvidence.consequence.gnomADe_AFR_AF, "0.0003");
  assert.equal(row.rawVcfEvidence.consequence.CADD_phred, "24.6");
  assert.equal(row.rawVcfEvidence.format.GT, "0/1");
  assert.equal(row.rawVcfEvidence.format.AD, "20,20");
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
  assert.equal(maneRow.loGoFuncPrediction, "GOF");
  assert.equal(maneRow.loGoFuncSourceTranscript, "ENST_SOURCE");
  assert.equal(maneRow.loGoFuncMatch, "source_transcript_match_elsewhere");
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
  assert.equal(
    result.rows[0].clinvarConflictingEvidence,
    "Pathogenic(1)&Uncertain_significance(2)",
  );
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
