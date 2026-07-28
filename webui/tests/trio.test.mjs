import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/trio.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  DEFAULT_TRIO_THRESHOLDS,
  assessDeNovo,
  compoundHetPairs,
  parsePedigree,
} = await import(moduleUrl);

const trio = {
  familyId: "F1",
  proband: "CHILD",
  mother: "MOTHER",
  father: "FATHER",
  probandSex: "female",
  parentalRelationshipsConfirmed: false,
};

function evidence(gt, dp, gq, adRef, adAlt, options = {}) {
  const called = !gt.includes(".");
  const alleles = gt.split(/[|/]/).map(Number);
  return {
    gt,
    called,
    carrier: called && alleles.some((allele) => allele > 0),
    dp,
    gq,
    adRef,
    adAlt,
    alleleBalance: adRef + adAlt > 0 ? adAlt / (adRef + adAlt) : null,
    pl: null,
    phased: gt.includes("|"),
    phaseSet: options.phaseSet ?? "",
    phaseHaplotype: options.phaseHaplotype ?? null,
  };
}

function row(overrides = {}) {
  return {
    key: "v1",
    source: "trio.vcf.gz",
    sample: "CHILD",
    chrom: "1",
    pos: 100,
    ref: "A",
    alt: "G",
    gene: "GENE1",
    genotype: "0/1",
    dp: 30,
    gq: 99,
    adRef: 15,
    adAlt: 15,
    alleleBalance: 0.5,
    phase: "unknown",
    mane: true,
    picked: true,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
    ...overrides,
  };
}

test("parses standard PED trios and validates sample names", () => {
  const result = parsePedigree(
    "F1 CHILD FATHER MOTHER 2 2\nF1 FATHER 0 0 1 1\nF1 MOTHER 0 0 2 1\n",
    new Set(["CHILD", "MOTHER", "FATHER"]),
  );
  assert.equal(result.trios.length, 1);
  assert.equal(result.trios[0].proband, "CHILD");
  assert.equal(result.trios[0].probandSex, "female");
  assert.deepEqual(result.warnings, []);
});

test("classifies high-confidence, possible, mosaic, and artifact de novo evidence", () => {
  assert.equal(assessDeNovo(row(), trio, DEFAULT_TRIO_THRESHOLDS).status, "high_confidence");

  const missingFather = row({
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
    },
  });
  assert.equal(assessDeNovo(missingFather, trio).status, "possible");

  const mosaicMother = row({
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: evidence("0/0", 32, 99, 30, 2),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  assert.equal(assessDeNovo(mosaicMother, trio).status, "possible_parental_mosaicism");

  const poorBalance = row({
    alleleBalance: 0.05,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 29, 1),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  assert.equal(assessDeNovo(poorBalance, trio).status, "likely_artifact");
});

test("requires opposite parental origins or phase for confirmed trans pairs", () => {
  const maternal = row({
    key: "maternal",
    pos: 100,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: evidence("0/1", 32, 99, 16, 16),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  const paternal = row({
    key: "paternal",
    pos: 200,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
      FATHER: evidence("0/1", 28, 99, 14, 14),
    },
  });
  const pair = compoundHetPairs([maternal, paternal], trio);
  assert.equal(pair.length, 1);
  assert.equal(pair[0].phase, "confirmed_trans_inheritance");

  const sameParent = compoundHetPairs([
    maternal,
    {
      ...paternal,
      sampleGenotypes: {
        CHILD: evidence("0/1", 30, 99, 15, 15),
        MOTHER: evidence("0/1", 32, 99, 16, 16),
        FATHER: evidence("0/0", 28, 99, 28, 0),
      },
    },
  ], trio);
  assert.equal(sameParent[0].phase, "cis");
});
