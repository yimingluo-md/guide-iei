import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/local-service.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { downloadFilename } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

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
