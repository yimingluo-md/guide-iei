// Export the in-app glossary (webui/app/glossary-data.ts) to docs/GLOSSARY.md
// so the website and the workbench share one authored set of definitions.
// Run from webui/: node scripts/export-glossary.mjs
import { readFile, writeFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../app/glossary-data.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { GLOSSARY, GLOSSARY_CATEGORIES } = await import(moduleUrl);

let out = `---
title: Glossary
nav_order: 5
---

# Glossary

<!-- GENERATED from webui/app/glossary-data.ts by webui/scripts/export-glossary.mjs.
     Do not edit by hand — edit the glossary data and re-run the exporter. -->

Plain-language definitions of the genetics terms used across GUIDE-IEI. The
same definitions appear inside the application: click any underlined term to
see it in place.

`;

for (const category of GLOSSARY_CATEGORIES) {
  const entries = GLOSSARY.filter((entry) => entry.category === category);
  if (!entries.length) continue;
  out += `## ${category}\n\n`;
  for (const entry of entries) {
    const aliases = [...(entry.aliases ?? []), ...(entry.exactAliases ?? [])];
    out += `**${entry.term}**`;
    if (aliases.length) out += ` *(also: ${aliases.join(", ")})*`;
    out += `\n: ${entry.definition}\n\n`;
  }
}

await writeFile(new URL("../../docs/GLOSSARY.md", import.meta.url), out);
console.log(`Exported ${GLOSSARY.length} terms to docs/GLOSSARY.md`);
