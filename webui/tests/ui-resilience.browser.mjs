// Optional browser regression: build webui first, then run with Node.
// Uses a fresh headless profile, synthetic VCF data, and blocks all API calls.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";
import ts from "typescript";

const root = new URL("../", import.meta.url);
const registry = await readFile(new URL("../../config/predictor-registry.json", import.meta.url), "utf8");
const parserSource = (await readFile(new URL("app/vcf.ts", root), "utf8")).replace(
  'import predictorRegistry from "../../config/predictor-registry.json";', `const predictorRegistry = ${registry};`,
);
const compiled = ts.transpileModule(parserSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { parseVcfFiles } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const parsed = await parseVcfFiles([new File([
  "##fileformat=VCFv4.2\n##reference=GRCh38\n##contig=<ID=1,length=248956422>\n",
  '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature|HGVSc|HGVSp|MANE_SELECT|PICK">\n',
  "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC_ONLY\n",
  "1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|NFKB1|ENSG00000109320|ENST00000226574|c.1A>G|p.Ala1Val|NM_TEST|1\tGT:DP:GQ:AD\t0/1:40:99:20,20\n",
], "synthetic-recovery.vcf")]);
assert.equal(parsed.rows.length, 1);

const port = Number(process.env.UI_TEST_PORT || 45389);
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, [fileURLToPath(new URL("node_modules/next/dist/bin/next", root)),
  "start", "--hostname", "127.0.0.1", "--port", String(port)], {
  cwd: fileURLToPath(root), env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" }, stdio: ["ignore", "pipe", "pipe"],
});
let output = "";
for (const stream of [server.stdout, server.stderr]) stream.on("data", (part) => { output = (output + part).slice(-10_000); });
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 150; attempt++) {
    if (server.exitCode !== null) throw new Error(output);
    try { ready = (await fetch(origin)).ok; } catch { /* local server starting */ }
    if (ready) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert(ready, output);
  browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  let failedReference = "iuis_2024_genes.txt";
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes("/api/")) {
      void request.respond({ status: 503, contentType: "application/json", body: '{"error":"Synthetic test: service unavailable"}' });
    } else if (failedReference && url.pathname.endsWith(failedReference)) {
      void request.respond({ status: 503, body: "Synthetic reference failure" });
    } else if (url.origin === origin) void request.continue();
    else void request.abort();
  });
  await page.evaluateOnNewDocument(() => {
    localStorage.setItem("guideIeiSavedCandidates", '{"synthetic":"SYNTHETIC_ONLY"}');
  });
  await page.goto(origin, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => document.body.textContent.includes("Some bundled references are unavailable"));
  assert.equal(await page.evaluate(() => localStorage.getItem("guideIeiSavedCandidates")), null);
  const clickText = async (text) => {
    await page.evaluate((label) => {
      const button = [...document.querySelectorAll("button")].find((item) => item.textContent.trim() === label);
      if (!button) throw new Error(`Missing button: ${label}`);
      button.click();
    }, text);
  };
  const clickNav = async (text) => {
    await page.evaluate((label) => {
      const button = [...document.querySelectorAll(".nav-item")].find((item) => item.firstElementChild.textContent === label);
      if (!button) throw new Error(`Missing navigation: ${label}`);
      button.click();
    }, text);
  };
  await clickNav("Variants");
  const disabled = await page.evaluate(() => Object.fromEntries(
    [...document.querySelectorAll(".check-row")].map((label) => [label.textContent, label.querySelector("input").disabled]),
  ));
  assert(Object.entries(disabled).some(([label, value]) => label.startsWith("IUIS 2024 IEI") && value));
  assert(Object.entries(disabled).some(([label, value]) => label.startsWith("Haploinsufficiency") && !value));
  await page.evaluate(() => [...document.querySelectorAll(".check-row")]
    .find((label) => label.textContent.startsWith("Haploinsufficiency")).click());
  failedReference = "iei_haploinsufficiency_genes.txt";
  await clickText("Retry references");
  await page.waitForFunction(() => document.body.textContent.includes("iei_haploinsufficiency_genes.txt could not be loaded"));
  assert(await page.evaluate(() => document.body.textContent.includes("Results paused: a selected reference filter is unavailable")));
  assert(!(await page.evaluate(() => [...document.querySelectorAll("button")].some((button) => button.textContent === "Export TSV"))));
  failedReference = "";
  await clickText("Retry references");
  await page.waitForFunction(() => !document.body.textContent.includes("Some bundled references are unavailable"));

  // Test-only React fiber inspection locates the real data-owner callbacks;
  // no production fault-injection endpoint or hook is added to the app.
  await page.evaluate((fixture) => {
    const node = document.querySelector(".app-shell");
    let fiber = node[Object.keys(node).find((key) => key.startsWith("__reactFiber$"))];
    while (fiber && typeof fiber.memoizedProps?.setRows !== "function") fiber = fiber.return;
    if (!fiber) throw new Error("Review session owner not found");
    const props = fiber.memoizedProps;
    window.__reviewFixture = fixture;
    window.__reviewCallbacks = props;
    props.setRows(fixture.rows);
    props.setSummary(fixture.summary);
    props.setSaved(new Set([fixture.rows[0].key]));
  }, parsed);
  await page.waitForFunction(() => document.querySelector(".table-frame")?.textContent.includes("NFKB1"));
  await page.evaluate(() => {
    const fixture = window.__reviewFixture;
    const gene = fixture.rows[0].gene;
    window.__throwSyntheticReview = true;
    Object.defineProperty(fixture.rows[0], "gene", { get() {
      if (window.__throwSyntheticReview) throw new Error("Synthetic render failure");
      return gene;
    } });
    window.__reviewCallbacks.setRows([...fixture.rows]);
    window.__reviewCallbacks.setSaved(new Set([fixture.rows[0].key]));
  });
  await page.waitForSelector("#review-recovery-title");
  assert.equal(await page.evaluate(() => document.activeElement.id), "review-recovery-title");
  await page.evaluate(() => { window.__throwSyntheticReview = false; });
  await clickText("Reopen interface");
  await page.waitForSelector(".nav-item");
  await clickNav("Saved");
  await page.waitForFunction(() => document.querySelector(".table-frame")?.textContent.includes("NFKB1"));
  assert(await page.evaluate(() => document.body.textContent.includes("Candidate stars are session-only")));
  assert(!(await page.evaluate(() => JSON.stringify(Object.entries(localStorage)))).includes("SYNTHETIC_ONLY"));
  await page.reload({ waitUntil: "networkidle0" });
  assert.equal(await page.evaluate(() => localStorage.getItem("guideIeiSavedCandidates")), null);
  assert.equal(await page.evaluate(() => [...document.querySelectorAll(".nav-item")]
    .find((node) => node.firstElementChild.textContent === "Saved").lastElementChild.textContent), "0");
  console.log("PASS: reference failure/retry, legacy bookmark cleanup, real render recovery retaining rows/stars, and refresh privacy.");
} catch (error) {
  if (browser) {
    const pages = await browser.pages();
    console.error(await pages[pages.length - 1].evaluate(() => document.body.innerText.slice(-5000)));
  }
  throw error;
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  if (server.exitCode === null) await new Promise((resolve) => server.once("exit", resolve));
}
