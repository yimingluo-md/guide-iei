import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import ts from "typescript";

const require = createRequire(new URL("../app/DataLicensesPanel.tsx", import.meta.url));
const source = await readFile(new URL("../app/DataLicensesPanel.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
    target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const module = { exports: {} };
new Function("require", "module", "exports", compiled)(require, module, module.exports);

test("license page renders offline terms without consent controls", () => {
  const html = renderToStaticMarkup(createElement(module.exports.default));
  assert.match(html, /Databases &amp; licenses/);
  assert.match(html, /AlphaMissense/);
  assert.match(html, /CADD/);
  assert.match(html, /SpliceAI/);
  assert.match(html, /Section 8/); // Full CC legal text, not merely a web link.
  assert.match(html, /<details>/);
  assert.match(html, /not embedded in the application installer/);
  assert.doesNotMatch(html, /<input|<form|<iframe/);
  assert.doesNotMatch(source, /fetch\(|localStorage|sessionStorage/);
});
