import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/VariantWorkbench.tsx", import.meta.url), "utf8");
const privacy = await readFile(new URL("../app/session-privacy.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(privacy, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { clearLegacySavedCandidates } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("legacy patient-derived bookmarks are removed without reading or copying them", () => {
  const removed = [];
  const storage = {
    getItem() { assert.fail("must not read patient-derived legacy keys"); },
    setItem() { assert.fail("must not persist patient-derived keys"); },
    removeItem(key) { removed.push(key); },
  };
  assert.equal(clearLegacySavedCandidates(storage), true);
  assert.deepEqual(removed, ["guideIeiSavedCandidates"]);
  assert.equal(clearLegacySavedCandidates({ removeItem() { throw new Error("denied"); } }), false);
  assert.doesNotMatch(source, /SAVED_CANDIDATES_STORAGE_KEY|savedStorageKey|savedKeyLoaded/);
  assert.match(source, /Legacy saved-candidate browser data could not be removed/);
  assert.match(source, /Candidate stars are session-only/);
});

test("review data and stars are owned above the resettable error boundary", async () => {
  const owner = source.slice(source.indexOf("export default function VariantWorkbench()"), source.indexOf("function WorkbenchSession("));
  for (const state of ["rows", "summary", "saved", "reviewAnalysisScope"]) {
    assert.match(owner, new RegExp(`const \\[${state},`));
  }
  assert.match(owner, /<WorkbenchErrorBoundary>[\s\S]*<WorkbenchSession/);
  const boundary = await readFile(new URL("../app/WorkbenchErrorBoundary.tsx", import.meta.url), "utf8");
  assert.match(boundary, /getDerivedStateFromError/);
  assert.match(boundary, /this\.setState\(\{ failed: false \}\)/);
  assert.doesNotMatch(boundary, /location\.reload|localStorage|sessionStorage/);
  assert.match(boundary, /role="alert"/);
});

test("failed reference filters cannot silently degrade a review", () => {
  assert.doesNotMatch(source, /STARTER_IEI/);
  assert.match(source, /referenceFilterBlocked \? \(/);
  assert.match(source, /Results paused: a selected reference filter is unavailable/);
  assert.match(source, /Some bundled references are unavailable/);
  assert.match(source, /Retry references/);
  for (const key of ["iei", "hi", "dominant", "constraints"]) {
    assert.match(source, new RegExp(`referenceUnavailable\\("${key}"\\)`));
  }
});
