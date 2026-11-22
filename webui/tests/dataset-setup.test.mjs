import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../app/dataset-setup.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { groupDatasetSources, datasetForScope } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("dataset groups separate WGS downloads, user files, essentials and optional additions without duplication", () => {
  const sources = [
    ["spliceai", "required"], ["screen_context", "recommended_wgs"], ["alphagenome_avi", "recommended_wgs"], ["encode_re2g", "recommended_wgs"],
    ["promoterai", "recommended_wgs"], ["genia", "recommended"],
    ["dbnsfp", "optional"], ["logofunc", "optional"], ["funcvep", "optional"], ["cadd_wgs", "optional"],
    ["loftee", "required"], ["clinvar", "recommended"], ["clingen_erepo", "required"], ["gencc", "included"], ["ccre", "included"],
  ].map(([id, recommendation]) => ({ id, recommendation }));
  const groups = groupDatasetSources(sources);
  assert.deepEqual(groups.wgs.map((s) => s.id), ["spliceai", "screen_context", "alphagenome_avi", "encode_re2g"]);
  assert.deepEqual(groups.userProvided.map((s) => s.id), ["promoterai", "genia"]);
  assert.deepEqual(groups.optional.map((s) => s.id), ["dbnsfp", "logofunc", "funcvep", "cadd_wgs"]);
  assert.ok(groups.essential.some((s) => s.id === "gencc"));
  assert.equal(Object.values(groups).flat().length, sources.length);
  assert.equal(new Set(Object.values(groups).flat().map((s) => s.id)).size, sources.length);
  // A legacy config can still require dbNSFP until starters are supplied;
  // that must not accidentally move it into the recommended user-file group.
  assert.equal(groupDatasetSources([{ id: "dbnsfp", recommendation: "required" }]).optional.length, 1);
});

test("the V card and run selection use the installed package for the chosen scope", () => {
  const source = { id: "gpn_star_v", installed: false, available: false, download_id: "gpn_star_v",
    exome_setup: { installed: true, available: true, download_id: "gpn_star_v_exome" } };
  assert.equal(datasetForScope(source, "whole_genome").available, false);
  assert.equal(datasetForScope(source, "exome").installed, true);
  assert.equal(datasetForScope(source, "exome").download_id, "gpn_star_v_exome");
});
