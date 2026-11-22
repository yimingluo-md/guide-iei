import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function typeScriptUrl(source) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
}

const parser = await readFile(new URL("../app/vcf.ts", import.meta.url), "utf8");
const registryUrl = new URL("../../config/predictor-registry.json", import.meta.url);
const parserUrl = typeScriptUrl(parser.replace(
  '"../../config/predictor-registry.json"', `"${registryUrl.href}" with { type: "json" }`,
));
const { parseVcfFiles, collapseToOneRowPerVariant, candidateCompoundHetKeys } = await import(parserUrl);
const source = await readFile(new URL("../app/trio.ts", import.meta.url), "utf8");
const {
  DEFAULT_TRIO_THRESHOLDS,
  assessDeNovo,
  compoundHetPairs, isCandidatePair, trioCompoundRowKeys,
  sampleGeneAlleleKey,
  parsePedigree,
} = await import(typeScriptUrl(source.replace('"./vcf"', JSON.stringify(parserUrl))));

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
    carrier: alleles.some((allele) => allele > 0),
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

test("missing phase sets and independent files cannot confirm physical phase", () => {
  const phasedRow = (pos, phaseSet, haplotype, source = "trio.vcf.gz") => row({
    pos, source,
    sampleGenotypes: {
      CHILD: evidence(haplotype === 0 ? "1|0" : "0|1", 30, 99, 15, 15,
        { phaseSet, phaseHaplotype: haplotype }),
    },
  });
  for (const phaseSet of ["", "."]) {
    for (const haplotype of [0, 1]) {
      const pairs = compoundHetPairs([
        phasedRow(100, phaseSet, 0), phasedRow(200, phaseSet, haplotype),
      ], trio);
      assert.equal(pairs[0].phase, "phase_unknown");
    }
  }
  assert.equal(compoundHetPairs([
    phasedRow(100, "123", 0, "first.vcf"),
    phasedRow(200, "123", 1, "second.vcf"),
  ], trio)[0].phase, "phase_unknown");
  assert.equal(compoundHetPairs([
    phasedRow(100, "123", 0), phasedRow(200, "123", 1),
  ], trio)[0].phase, "confirmed_trans_phasing");
});

test("compound-het pairs require a named gene on the same contig", () => {
  for (const gene of ["", "—", ".", "-"]) {
    assert.deepEqual(compoundHetPairs([row({ gene }), row({ gene, pos: 200 })], trio), []);
  }
  assert.deepEqual(compoundHetPairs([row(), row({ chrom: "2", pos: 200 })], trio), []);
  assert.equal(compoundHetPairs([row(), row({ chrom: "chr1", pos: 200 })], trio).length, 1);
});

test("zero or missing parental allele-depth coverage cannot confirm de novo", () => {
  for (const adRef of [0, null]) {
    const variant = row();
    variant.sampleGenotypes.MOTHER = {
      ...evidence("0/0", 30, 99, 30, 0), adRef, alleleBalance: null,
    };
    assert.equal(assessDeNovo(variant, trio).status, "possible");
  }
});

test("a 1/2 child is not labelled homozygous alternate", () => {
  const variant = row();
  variant.sampleGenotypes.CHILD = {
    ...evidence("1/2", 30, 99, 0, 15), alleleBalance: 0.5,
  };
  assert.notEqual(assessDeNovo(variant, trio).status, "mendelian_conflict");
});

async function parseMultiallelicTrio(child, mother, father) {
  const vcf = [
    "##fileformat=VCFv4.2",
    "##reference=GRCh38",
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|SYMBOL">',
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCHILD\tMOTHER\tFATHER",
    ["1", "100", ".", "T", "A,G", "99", "PASS", "CSQ=A|GENE1,G|GENE1",
      "GT:DP:GQ:AD", child, mother, father].join("\t"),
  ].join("\n");
  const { rows } = await parseVcfFiles([new File([vcf], "trio.vcf")]);
  return rows.filter((row) => row.sample === trio.proband);
}

test("de novo assessment uses the queried ALT in parental calls and reads", async () => {
  for (const [mother, status] of [
    ["0/2:30:99:15,0,15", "high_confidence"],
    ["./2:30:99:15,0,15", "possible"],
    ["0/2:30:10:15,0,15", "possible"],
    ["0/2:30:99:15,.,15", "possible"],
    ["0/2:30:99:14,1,15", "possible_parental_mosaicism"],
  ]) {
    const [alt1] = await parseMultiallelicTrio("0/1:30:99:15,15,0", mother, "0/0:30:99:30,0,0");
    assert.equal(alt1.alt, "A");
    assert.equal(assessDeNovo(alt1, trio).status, status, mother);
  }
});

test("a 1/2 child's ALTs can be inherited from opposite parents at one site", async () => {
  const rows = await parseMultiallelicTrio("1/2:30:99:0,15,15", "0/1:30:99:15,15,0", "0/2:30:99:15,0,15");
  for (const row of rows) assert.equal(assessDeNovo(row, trio).status, "inherited");
  const [pair] = compoundHetPairs(rows, trio);
  assert.deepEqual({ [pair.first.alt]: pair.firstOrigin, [pair.second.alt]: pair.secondOrigin },
    { A: "maternal", G: "paternal" });
  assert.equal(pair.phase, "confirmed_trans_inheritance");
});

test("PED trios cannot reuse the child or the same parent sample", () => {
  for (const parents of ["CHILD MOTHER", "FATHER CHILD", "MOTHER MOTHER"]) {
    const result = parsePedigree(`F1 CHILD ${parents} 2 2\n`, new Set(["CHILD", "MOTHER", "FATHER"]));
    assert.deepEqual(result.trios, []);
    assert.ok(result.warnings.some(warning => warning.includes("different samples")));
  }
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

test("split trio files match padded alleles without pairing a duplicate or renumbering a call", async () => {
  function file(sample, records) {
    return new File([[
      "##fileformat=VCFv4.2", "##reference=GRCh38",
      '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|ALLELE_NUM|SYMBOL">',
      "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + sample,
      ...records.map(([chrom, pos, ref, alt, csq, call]) => [
        chrom, pos, ".", ref, alt, 99, "PASS", "CSQ=" + csq, "GT:DP:GQ:AD", call,
      ].join("\t")),
    ].join("\n")], sample + ".vcf");
  }
  const { rows } = await parseVcfFiles([
    file("MOTHER", [
      ["1", 100, "T", "G", "G|1|GENE", "0/1:30:99:15,15"],
      ["1", 100, "T", "C", "C|1|GENE", "0/0:30:99:30,0"],
    ]),
    file("CHILD", [
      ["chr1", 99, "AT", "AG,AC", "AG|1|GENE,AC|2|GENE", "1/2:30:99:0,12,18"],
      ["chr1", 100, "t", "g", "g|1|GENE", "0/1:30:90:15,15"],
    ]),
    file("FATHER", [
      ["1", 100, "T", "C,G", "C|1|GENE,G|2|GENE", "0/1:30:99:15,15,0"],
    ]),
  ]);
  const child = rows.filter(r => r.sample === "CHILD");
  assert.deepEqual(child.slice(0, 2).map(r => [r.genotype, r.adAlt, r.alleleBalance]),
    [["1/2", 12, 0.4], ["1/2", 18, 0.6]]);
  assert.deepEqual(child.map(r => assessDeNovo(r, trio).status), ["inherited", "inherited", "inherited"]);
  const sameAllele = [child[0], child[2]];
  assert.equal(sampleGeneAlleleKey(child[0]), sampleGeneAlleleKey(child[2]));
  assert.notEqual(sampleGeneAlleleKey(child[0]), sampleGeneAlleleKey(rows.find(r => r.sample === "MOTHER")));
  assert.equal(collapseToOneRowPerVariant(rows).length, 4); // two child alleles, one per parent
  assert.equal(candidateCompoundHetKeys(sameAllele).size, 0);
  assert.deepEqual(compoundHetPairs(sameAllele, trio), []);
  assert.deepEqual([...candidateCompoundHetKeys(child)], child.map(row => row.key));
  const pairs = compoundHetPairs(rows, trio);
  assert.deepEqual(pairs.map(p => [p.firstOrigin, p.secondOrigin, p.phase]),
    [["maternal", "paternal", "confirmed_trans_inheritance"]]);
});

test("Y PAR, non-PAR Y, unknown sex and incompatible child ploidy use explicit models", () => {
  function variant(chrom, pos, gt, father, mother) {
    return row({ chrom, pos, genotype: gt, sampleGenotypes: {
      CHILD: evidence(gt, 30, 99, 15, 15), MOTHER: evidence(mother, 30, 99, 30, 0),
      FATHER: evidence(father, 30, 99, 30, 0),
    } });
  }
  const y = variant("chrY", 3000000, "1", "0", "1/1");
  assert.equal(assessDeNovo(y, maleTrio).status, "high_confidence");
  assert.equal(assessDeNovo(variant("Y", 3000000, "1", "1", "0/0"), maleTrio).status, "inherited");
  assert.equal(assessDeNovo(variant("Y", 56887903, "0/1", "0/0", "0/1"), maleTrio).status, "inherited");
  for (const sex of ["unknown", "female"]) {
    assert.equal(assessDeNovo(y, { ...trio, probandSex: sex }).status, "unassessed");
  }
  for (const [chrom, pos, gt, sex] of [["1", 100, "1", "female"], ["X", 3000000, "0/1", "male"],
    ["X", 3000000, "1/1", "unknown"], ["Y", 10001, "1", "male"], ["1", 100, "1/.", "male"]]) {
    const v = variant(chrom, pos, gt, "0/0", "0/0");
    assert.equal(assessDeNovo(v, { ...trio, probandSex: sex }).status, "unassessed", [chrom, pos, gt, sex].join(" "));
  }
});

test("trio badges cover surviving transcripts of candidate pair members only", () => {
  const first = row({ key: "first", sourceRecordOrdinal: 1 });
  const second = row({ key: "second", pos: 200, sourceRecordOrdinal: 2 });
  const rows = [first, { ...first, key: "other-transcript" }, second,
    { ...first, sample: "MOTHER", key: "parent" }, { ...first, key: "hom-duplicate", genotype: "1/1", sourceRecordOrdinal: 3 },
    { ...first, key: "half", genotype: "1/.", pos: 300, sampleGenotypes: undefined }];
  const candidates = compoundHetPairs(rows, trio).filter(isCandidatePair);
  assert.deepEqual([...trioCompoundRowKeys(rows, candidates)], ["first", "other-transcript", "second"]);
  for (const [chrom, sex] of [["chrM", "female"], ["chrY", "male"], ["X", "unknown"], ["Y", "female"]]) {
    const pairRows = [first, second].map((r, i) => ({ ...r, chrom, pos: 3_000_000 + i }));
    const pairs = compoundHetPairs(pairRows, { ...trio, probandSex: sex });
    assert.equal(pairs.length, 1);
    assert.equal(isCandidatePair(pairs[0]), false);
    assert.deepEqual([...trioCompoundRowKeys(pairRows, pairs.filter(isCandidatePair))], []);
  }
});
