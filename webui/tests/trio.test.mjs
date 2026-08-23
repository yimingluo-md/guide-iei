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

// ---- 2026-08 audit regression tests (Phase 1, webui) ----

const maleTrio = { ...trio, probandSex: "male" };

test("male proband non-PAR X de novo is not a mendelian conflict or artifact", () => {
  // Audit UI-9: an X-linked de novo written as 1/1 hit the hom-alt conflict
  // branch, and a haploid 1 failed the diploid allele-balance upper bound.
  const diploidStyle = row({
    chrom: "X",
    pos: 71108276,
    alleleBalance: 1,
    sampleGenotypes: {
      CHILD: evidence("1/1", 30, 99, 0, 30),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  assert.equal(assessDeNovo(diploidStyle, maleTrio).status, "high_confidence");

  const haploidStyle = row({
    chrom: "chrX",
    pos: 71108276,
    alleleBalance: 1,
    sampleGenotypes: {
      CHILD: evidence("1", 30, 99, 0, 30),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  assert.equal(assessDeNovo(haploidStyle, maleTrio).status, "high_confidence");
});

test("male X assessment depends only on the transmitting mother", () => {
  // The father contributes the Y, so his X-locus genotype must not gate the
  // call — even when it is missing entirely.
  const fatherless = row({
    chrom: "X",
    pos: 71108276,
    alleleBalance: 1,
    sampleGenotypes: {
      CHILD: evidence("1/1", 30, 99, 0, 30),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
    },
  });
  assert.equal(assessDeNovo(fatherless, maleTrio).status, "high_confidence");

  const maternallyInherited = row({
    chrom: "X",
    pos: 71108276,
    alleleBalance: 1,
    sampleGenotypes: {
      CHILD: evidence("1/1", 30, 99, 0, 30),
      MOTHER: evidence("0/1", 32, 99, 16, 16),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  assert.equal(assessDeNovo(maternallyInherited, maleTrio).status, "inherited");
});

test("female proband X and male PAR X keep the diploid model", () => {
  const femaleX = row({
    chrom: "X",
    pos: 71108276,
    alleleBalance: 1,
    sampleGenotypes: {
      CHILD: evidence("1/1", 30, 99, 0, 30),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  assert.equal(assessDeNovo(femaleX, trio).status, "mendelian_conflict");

  const par1 = row({
    chrom: "X",
    pos: 1000000,
    alleleBalance: 1,
    sampleGenotypes: {
      CHILD: evidence("1/1", 30, 99, 0, 30),
      MOTHER: evidence("0/0", 32, 99, 32, 0),
      FATHER: evidence("0/0", 28, 99, 28, 0),
    },
  });
  assert.equal(assessDeNovo(par1, maleTrio).status, "mendelian_conflict");
});

test("absent parental genotypes no longer promote pairs to possible_trans", () => {
  // Audit UI-10: "we do not know the parental genotype" was converted into
  // "this arose de novo", labelling unphaseable pairs possible_trans.
  const unknownParents = row({
    key: "unknown",
    pos: 100,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: evidence("./.", 0, 0, 0, 0),
      FATHER: evidence("./.", 0, 0, 0, 0),
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
  const pairs = compoundHetPairs([unknownParents, paternal], trio);
  assert.equal(pairs.length, 1);
  assert.equal(pairs[0].phase, "phase_unknown");
});

test("only affected members are offered as probands", () => {
  // Audit UI-15: an unaffected sibling with two listed parents became a
  // second proband whose healthy variants were analysed as de novo.
  const result = parsePedigree(
    "F1 CHILD FATHER MOTHER 2 2\nF1 SIB FATHER MOTHER 1 1\nF1 FATHER 0 0 1 1\nF1 MOTHER 0 0 2 1\n",
    new Set(["CHILD", "SIB", "MOTHER", "FATHER"]),
  );
  assert.equal(result.trios.length, 1);
  assert.equal(result.trios[0].proband, "CHILD");
  assert.equal(result.warnings.some((warning) => warning.includes("SIB")), true);
});

test("parents that exist nowhere produce a warning, not a silent empty trio", () => {
  // Audit UI-15: ghost parents previously yielded a trio and zero warnings.
  const result = parsePedigree("F1 CHILD GHOST_DAD GHOST_MOM 1 2\n");
  assert.equal(result.trios.length, 0);
  assert.equal(
    result.warnings.some((warning) => warning.includes("GHOST_DAD")),
    true,
    result.warnings.join(" | "),
  );
});

test("male proband non-PAR X pair is excluded as hemizygous, PAR pair is not", () => {
  const xRow = (pos, key) => row({
    chrom: "chrX", pos, key, gene: "BTK",
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 90, 15, 15),
      MOTHER: evidence("0/1", 30, 90, 15, 15),
      FATHER: evidence("0/0", 30, 90, 30, 0),
    },
  });
  const nonPar = compoundHetPairs(
    [xRow(101_000_000, "x1"), xRow(101_000_500, "x2")], maleTrio,
  );
  assert.equal(nonPar.length, 1);
  assert.equal(nonPar[0].phase, "excluded_hemizygous");
  const par = compoundHetPairs(
    [xRow(1_000_000, "p1"), xRow(1_000_500, "p2")], maleTrio,
  );
  assert.equal(par[0].phase !== "excluded_hemizygous", true);
  const female = compoundHetPairs(
    [xRow(101_000_000, "x1"), xRow(101_000_500, "x2")], trio,
  );
  assert.equal(female[0].phase !== "excluded_hemizygous", true);
});

test("mitochondrial variant: father is not a transmitting parent", () => {
  const mtRow = row({
    chrom: "chrM", pos: 3243, key: "mt1", alleleBalance: 0.96,
    sampleGenotypes: {
      CHILD: evidence("1/1", 500, 99, 20, 480),
      MOTHER: evidence("0/0", 500, 99, 500, 0),
      FATHER: evidence("1/1", 500, 99, 10, 490),
    },
  });
  const result = assessDeNovo(mtRow, trio);
  assert.equal(result.status, "high_confidence");
  assert.match(result.reasons[0], /mitochondrial/);
  const inherited = assessDeNovo(row({
    chrom: "chrM", pos: 3243, key: "mt1", alleleBalance: 0.96,
    sampleGenotypes: {
      CHILD: evidence("1/1", 500, 99, 20, 480),
      MOTHER: evidence("0/1", 500, 99, 400, 100),
      FATHER: evidence("0/0", 500, 99, 500, 0),
    },
  }), trio);
  assert.equal(inherited.status, "inherited");
});

test("low-quality parental calls cannot claim inherited or confirmed trans", () => {
  const weakParent = (gt) => evidence(gt, 1, 1, gt === "0/0" ? 1 : 0, gt === "0/0" ? 0 : 1);
  const weakInherited = assessDeNovo(row({
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: weakParent("0/1"),
      FATHER: evidence("0/0", 30, 99, 30, 0),
    },
  }), trio);
  assert.equal(weakInherited.status, "possible");
  assert.match(weakInherited.reasons[0], /below the parental quality thresholds/);

  const solidInherited = assessDeNovo(row({
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: evidence("0/1", 30, 99, 15, 15),
      FATHER: evidence("0/0", 30, 99, 30, 0),
    },
  }), trio);
  assert.equal(solidInherited.status, "inherited");

  const maternalWeak = row({
    key: "m1", pos: 100,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: weakParent("0/1"),
      FATHER: weakParent("0/0"),
    },
  });
  const paternalWeak = row({
    key: "p1", pos: 200,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: weakParent("0/0"),
      FATHER: weakParent("0/1"),
    },
  });
  const pairs = compoundHetPairs([maternalWeak, paternalWeak], trio);
  assert.equal(pairs.length, 1);
  assert.notEqual(pairs[0].phase, "confirmed_trans_inheritance");
});

test("configured trio thresholds gate compound-het origins too", () => {
  const midQuality = (gt) => evidence(gt, 15, 40, gt === "0/0" ? 15 : 8, gt === "0/0" ? 0 : 7);
  const maternal = row({
    key: "m1", pos: 100,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: midQuality("0/1"),
      FATHER: midQuality("0/0"),
    },
  });
  const paternal = row({
    key: "p1", pos: 200,
    sampleGenotypes: {
      CHILD: evidence("0/1", 30, 99, 15, 15),
      MOTHER: midQuality("0/0"),
      FATHER: midQuality("0/1"),
    },
  });
  // Defaults (DP >= 10, GQ >= 20): mid-quality parents support confirmation.
  const relaxed = compoundHetPairs([maternal, paternal], trio);
  assert.equal(relaxed[0].phase, "confirmed_trans_inheritance");
  // A reviewer who tightened DP/GQ must see the same rule applied here.
  const strict = compoundHetPairs([maternal, paternal], trio, {
    ...DEFAULT_TRIO_THRESHOLDS, parentMinDp: 25, minGq: 60,
  });
  assert.notEqual(strict[0].phase, "confirmed_trans_inheritance");
});
