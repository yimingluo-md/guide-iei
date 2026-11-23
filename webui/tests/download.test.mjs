import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/local-service.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { downloadFilename, cancelJob, getJobLog, openJobReviewFile, openSampleLibraryFile } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("review downloads preserve Unicode names and support legacy responses", () => {
  const name = '研究 "sample".vcf.gz';
  assert.equal(downloadFilename(
    `attachment; filename="fallback.vcf.gz"; filename*=UTF-8''${encodeURIComponent(name)}`,
    "unused.vcf",
  ), name);
  assert.equal(downloadFilename('attachment; filename="legacy.vcf.gz"', "fallback.vcf"), "legacy.vcf.gz");
  assert.equal(downloadFilename('attachment; filename="legacy.vcf"; filename*=UTF-8\'\'%INVALID', "fallback.vcf"), "legacy.vcf");
  assert.equal(downloadFilename("", "fallback.vcf.gz"), "fallback.vcf.gz");
});

test("job actions and review downloads validate the same opaque ID before fetching", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", () => Promise.resolve(Response.json({ log: "done" })));
  const actions = [cancelJob, getJobLog, openJobReviewFile, openSampleLibraryFile];
  for (const action of actions) {
    for (const invalid of ["", ".", "..", "a/b", "a?b", "a#b", "a%2fb", null]) {
      await assert.rejects(action(invalid, "review.vcf"), /Invalid identifier/);
    }
  }
  assert.equal(fetch.mock.callCount(), 0);
  for (const action of actions) await action("ID.1_test-a", "review.vcf");
  assert.deepEqual(fetch.mock.calls.map(call => new URL(call.arguments[0]).pathname), [
    "/api/jobs/ID.1_test-a/cancel", "/api/jobs/ID.1_test-a/log",
    "/api/jobs/ID.1_test-a/review-file", "/api/sample-library/ID.1_test-a/file",
  ]);
});

test("TSV chunks preserve field boundaries, Unicode, literal escapes, zero and missing values", async () => {
  const tsvModule = ts.transpileModule(await readFile(new URL("../app/tsv.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const { reviewTsvBlob } = await import(`data:text/javascript;base64,${Buffer.from(tsvModule).toString("base64")}`);
  const value = "研究\\t\t\r\n";
  const rows = Array.from({ length: 10000 }, (_, i) => [i, value, false, null]);
  const blob = reviewTsvBlob("Research use only.", ["id", "label", "flag", "missing"], rows, row => row);
  const [notice, header, ...lines] = (await blob.text()).slice(0, -1).split("\n");
  assert.equal(notice, "# Research use only.");
  assert.equal(header, "id\tlabel\tflag\tmissing");
  assert.equal(lines.length, rows.length);
  assert.equal(lines[0], "0\t研究\\\\t\\t\\r\\n\tfalse\t");
  assert.equal(lines.at(-1), "9999\t研究\\\\t\\t\\r\\n\tfalse\t");
});

test("TSV protects formula-like text while preserving numeric predictors", async () => {
  const tsvModule = ts.transpileModule(await readFile(new URL("../app/tsv.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const { reviewTsvBlob } = await import(`data:text/javascript;base64,${Buffer.from(tsvModule).toString("base64")}`);
  const cells = ["=1+1", "+SUM(A1)", "-2", "@cmd", "  =1", "\t=1", "\rtext", "\ntext", "normal", "x\t=1", -2, -0.5, 0, false, null];
  const headers = cells.map((_, index) => `column${index}`);
  const blob = reviewTsvBlob("Synthetic notice", headers, [cells], row => row);
  const lines = (await blob.text()).split("\n");
  assert.equal(lines[1], headers.join("\t"));
  assert.deepEqual(lines[2].split("\t"), ["'=1+1", "'+SUM(A1)", "'-2", "'@cmd", "'  =1", "'\\t=1", "'\\rtext", "'\\ntext", "normal", "x\\t=1", "-2", "-0.5", "0", "false", ""]);
});
