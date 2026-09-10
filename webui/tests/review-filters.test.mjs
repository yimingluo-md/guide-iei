import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/review-filters.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { passesMinimumScore, screenVariantBatches } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const variants = Array.from({ length: 5 }, (_, i) => ({ key: String(i), chrom: "1", pos: i + 1, ref: "A", alt: "G" }));

test("AVI minimum permits zero, excludes missing only when enabled, and includes the boundary", () => {
  assert(passesMinimumScore(null, null));
  assert(passesMinimumScore(undefined, null));
  assert(passesMinimumScore(0, 0));
  assert(!passesMinimumScore(null, 0));
  assert(!passesMinimumScore(undefined, 0));
  assert(!passesMinimumScore(NaN, 0));
  assert(passesMinimumScore(20, 20));
  assert(!passesMinimumScore(19.99, 20));
});

test("SCREEN publishes cumulative matches only after completed batches", async () => {
  const updates = [];
  await screenVariantBatches(variants, async (batch) => ({ available: true, matching_keys: [batch[0].key, "unrequested"] }),
    (matches, tested, total) => updates.push({ keys: [...matches], tested, total }), () => true, 2);
  assert.deepEqual(updates, [
    { keys: ["0"], tested: 2, total: 5 },
    { keys: ["0", "2"], tested: 4, total: 5 },
    { keys: ["0", "2", "4"], tested: 5, total: 5 },
  ]);
});

test("SCREEN ignores a stale response when the user changes filters", async () => {
  let active = true;
  let updates = 0;
  await screenVariantBatches(variants, async () => {
    active = false;
    return { available: true, matching_keys: ["0"] };
  }, () => updates++, () => active);
  assert.equal(updates, 0);
});

test("SCREEN service failure is not a completed zero-match result", async () => {
  const updates = [];
  await assert.rejects(screenVariantBatches(variants,
    async () => ({ available: false, matching_keys: [] }),
    (...args) => updates.push(args), () => true), /unavailable/);
  assert.deepEqual(updates, []);
});

test("SCREEN handles empty inputs and genuine zero-match completion", async () => {
  const updates = [];
  const request = async () => ({ available: true, matching_keys: [] });
  await screenVariantBatches([], request, (matches, tested, total) => updates.push([matches.size, tested, total]), () => true);
  await screenVariantBatches(variants, request, (matches, tested, total) => updates.push([matches.size, tested, total]), () => true);
  assert.deepEqual(updates, [[0, 0, 0], [0, 5, 5]]);
});
