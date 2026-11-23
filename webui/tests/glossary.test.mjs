import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/glossary-data.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { GLOSSARY, GLOSSARY_CATEGORIES, searchGlossary, segmentText } = await import(moduleUrl);

test("glossary data is well-formed", () => {
  const ids = new Set();
  const spellings = new Set();
  for (const entry of GLOSSARY) {
    assert.ok(entry.id && !ids.has(entry.id), `duplicate or missing id: ${entry.id}`);
    ids.add(entry.id);
    assert.ok(entry.definition.trim().length > 20, `definition too short: ${entry.id}`);
    assert.ok(entry.definition.length < 400, `definition too long for a popover: ${entry.id}`);
    assert.ok(GLOSSARY_CATEGORIES.includes(entry.category), `unknown category: ${entry.category}`);
    for (const spelling of [entry.term, ...(entry.aliases ?? []), ...(entry.exactAliases ?? [])]) {
      const key = spelling.toLowerCase();
      assert.ok(!spellings.has(key), `spelling maps to two entries: ${spelling}`);
      spellings.add(key);
    }
  }
  assert.ok(GLOSSARY.length >= 30, "expected the full term set");
});

test("terms are recognized inside authored copy", () => {
  const segments = segmentText("Filters use gnomAD popmax and the MANE Select transcript.");
  const marked = segments.filter((segment) => segment.entry).map((segment) => segment.entry.id);
  assert.deepEqual(marked, ["popmax", "mane", "transcript"]);
  // Longest match won: "gnomAD popmax" resolved to popmax, not gnomAD.
  assert.ok(!marked.includes("gnomad"));
});

test("each term is marked once per text block", () => {
  const segments = segmentText("A frameshift here and another frameshift there.");
  const marked = segments.filter((segment) => segment.entry);
  assert.equal(marked.length, 1);
});

test("word boundaries prevent partial-word matches", () => {
  // "trans" must not match inside "transcription"; "cis" not inside "precise".
  const segments = segmentText("Transcription is a precise process.");
  assert.ok(segments.every((segment) => !segment.entry));
});

test("short abbreviations match case-sensitively", () => {
  for (const [alias, id] of [["DP", "read-depth"], ["GQ", "genotype-quality"], ["AB", "allele-balance"]]) {
    assert.ok(segmentText(`Check ${alias} at this site.`).some((segment) => segment.entry?.id === id));
    assert.ok(segmentText(alias.toLowerCase()).every((segment) => segment.entry?.id !== id));
  }
  assert.ok(segmentText("deep intronic changes").every((segment) => !segment.entry || segment.entry.id !== "read-depth"));
});

test("glossary search prioritizes exact abbreviations and retains other search matches", () => {
  for (const [alias, id] of [["DP", "read-depth"], ["GQ", "genotype-quality"], ["AB", "allele-balance"]]) {
    assert.equal(searchGlossary(alias)[0]?.id, id);
    assert.equal(searchGlossary(` ${alias.toLowerCase()} `)[0]?.id, id);
  }
  assert.ok(searchGlossary("AB").some((entry) => entry.id !== "allele-balance"));
  assert.ok(searchGlossary("read dep").some((entry) => entry.id === "read-depth"));
  assert.ok(searchGlossary("GRCh37").some((entry) => entry.id === "genome-build"));
  assert.ok(searchGlossary("sequencing reads covered").some((entry) => entry.id === "read-depth"));
  assert.deepEqual(searchGlossary("  "), GLOSSARY);
  assert.deepEqual(searchGlossary("no such glossary entry xyz"), []);
});

test("plain text passes through untouched", () => {
  const segments = segmentText("No jargon here at all.");
  assert.equal(segments.length, 1);
  assert.equal(segments[0].text, "No jargon here at all.");
  assert.equal(segments[0].entry, undefined);
});
