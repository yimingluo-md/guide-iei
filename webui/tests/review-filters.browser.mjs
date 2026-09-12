// Optional browser regression: build first; all API calls mocked, synthetic data only.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";
import ts from "typescript";

const root = new URL("../", import.meta.url);
const registry = await readFile(new URL("../../config/predictor-registry.json", import.meta.url), "utf8");
const parser = (await readFile(new URL("app/vcf.ts", root), "utf8")).replace(
  'import predictorRegistry from "../../config/predictor-registry.json";', `const predictorRegistry = ${registry};`);
const compiled = ts.transpileModule(parser, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { parseVcfFiles } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const fixture = await parseVcfFiles([new File([
  '##fileformat=VCFv4.2\n##reference=GRCh38\n##contig=<ID=1,length=248956422>\n',
  '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature|HGVSc|HGVSp|MANE_SELECT|PICK">\n',
  '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC_ONLY\n',
  '1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|AJAP1|ENSG_TEST|ENST00000378191|ENST00000378191.5:c.829+12772C>T||NM_TEST|1\tGT:DP:GQ:AD\t0/1:40:99:20,20\n',
], "synthetic-screen.vcf")]);
const base = fixture.rows[0];
fixture.rows = Array.from({ length: 1501 }, (_, i) => ({ ...base, key: `synthetic:${i}`, pos: 100 + i,
  alphaGenomeAviPhred: i === 0 ? 25 : 0 }));
fixture.rows[0].collapsedTranscriptRows = [{ ...base, key: "other", transcript: "ENST00000378190",
  hgvsC: "ENST00000378190.7:c.829+12772C>T", mane: false, maneSelect: false, picked: false }];
const catalog = { available: true, tissues: [{ id: "tissue:0", name: "blood" }], immune_contexts: [],
  presets: ["immune-core", "immune-all"].map((id) => ({ id, name: id, tissue_ids: ["tissue:0"], immune_context_ids: [] })) };

const port = Number(process.env.UI_TEST_PORT || 45391);
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, [fileURLToPath(new URL("node_modules/next/dist/bin/next", root)),
  "start", "--hostname", "127.0.0.1", "--port", String(port)], {
  cwd: fileURLToPath(root), env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" }, stdio: "ignore",
});
let browser;
let releaseSecond;
const secondBatch = new Promise((resolve) => { releaseSecond = resolve; });
try {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(origin)).ok) break; } catch { /* starting */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  browser = await puppeteer.launch({ headless: true,
    executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  await page.setRequestInterception(true);
  let batches = 0;
  let failScreen = false;
  page.on("request", async (request) => {
    const url = new URL(request.url());
    if (!url.pathname.includes("/api/")) {
      return url.origin === origin ? request.continue() : request.abort();
    }
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
    if (request.method() === "OPTIONS") return request.respond({ status: 204, headers });
    if (url.pathname === "/api/screen-context/catalog") return request.respond({ status: 200, headers, contentType: "application/json", body: JSON.stringify(catalog) });
    if (url.pathname === "/api/screen-context/filter") {
      if (failScreen) return request.respond({ status: 503, headers, contentType: "application/json", body: '{"error":"Synthetic SCREEN failure"}' });
      batches++;
      if (batches === 2) await secondBatch;
      const data = JSON.parse(request.postData());
      return request.respond({ status: 200, headers, contentType: "application/json", body: JSON.stringify({ available: true,
        matching_keys: data.variants.slice(0, batches === 1 ? 2 : 1).map((item) => item.key) }) });
    }
    return request.respond({ status: 503, headers, contentType: "application/json", body: '{"error":"Synthetic test: unavailable"}' });
  });
  await page.goto(origin, { waitUntil: "networkidle2" });
  await page.evaluate((data) => {
    const node = document.querySelector(".app-shell");
    let fiber = node[Object.keys(node).find((key) => key.startsWith("__reactFiber$"))];
    while (fiber && typeof fiber.memoizedProps?.setRows !== "function") fiber = fiber.return;
    fiber.memoizedProps.setRows(data.rows);
    fiber.memoizedProps.setSummary(data.summary);
    fiber.memoizedProps.setReviewAnalysisScope("whole_genome");
    [...document.querySelectorAll(".nav-item")].find((item) => item.firstElementChild.textContent === "Variants").click();
  }, fixture);
  await page.waitForSelector(".table-frame tbody tr");
  const toggleScreen = () => page.evaluate(() => [...document.querySelectorAll(".check-row")]
    .find((label) => label.textContent.startsWith("Immune context")).click());
  await toggleScreen();
  await page.waitForFunction(() => document.body.textContent.includes("Screened 1,000 of 1,501"));
  assert(await page.evaluate(() => document.body.textContent.includes("results are incomplete")));
  assert.equal(await page.$$eval(".table-frame tbody tr", (rows) => rows.length), 2);
  assert(await page.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent === "Export TSV").disabled));
  assert(!(await page.evaluate(() => document.body.textContent.includes("No variants match these filters"))));
  releaseSecond();
  await page.waitForFunction(() => !document.body.textContent.includes("results are incomplete"));
  assert.equal(await page.$$eval(".table-frame tbody tr", (rows) => rows.length), 3);
  await toggleScreen();
  const avi = await page.$(".threshold:has(input[min='0']) input");
  assert(avi, "AVI threshold missing");
  await avi.type("20");
  await page.waitForFunction(() => document.querySelector(".content-header .subtitle")?.textContent.startsWith("1 transcript-level"));
  await avi.click({ clickCount: 3 });
  await avi.press("Backspace");
  failScreen = true;
  await toggleScreen();
  await page.waitForFunction(() => document.body.textContent.includes("SCREEN filtering could not finish"));
  assert(!(await page.evaluate(() => document.body.textContent.includes("No variants match these filters"))));
  await toggleScreen();
  await page.click(".table-frame tbody tr");
  await page.waitForSelector(".transcript-table-scroll");
  assert(!(await page.evaluate(() => document.body.textContent.includes("Missing is not evidence that the variant is benign"))));
  for (const width of [1440, 768]) {
    await page.setViewport({ width, height: 1000 });
    const layout = await page.$eval(".transcript-table-scroll", (el) => ({
      width: el.clientWidth, table: el.querySelector("table").scrollWidth,
      containerWidth: el.closest(".evidence-section").clientWidth,
      overflow: getComputedStyle(el).overflowX,
      geneWidth: el.querySelector("th").getBoundingClientRect().width,
    }));
    assert.equal(layout.overflow, "auto");
    assert(layout.geneWidth > 34, "Inherited star-column width");
    // The application has an existing desktop minimum width. The table must
    // stay inside its own section at every viewport, not widen that section.
    assert(layout.width <= layout.containerWidth);
  }
  await page.setViewport({ width: 1440, height: 1000 });
  await page.$eval(".alt-transcripts", (el) => el.scrollIntoView({ block: "center" }));
  await (await page.$(".alt-transcripts")).screenshot({ path: "/private/tmp/guide-iei-transcript-table.png" });
  console.log("PASS: partial/final/error SCREEN states, AVI filter, and transcript table layout");
} finally {
  releaseSecond();
  if (browser) await browser.close();
  server.kill("SIGTERM");
}
