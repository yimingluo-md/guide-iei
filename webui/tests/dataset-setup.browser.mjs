// Synthetic browser regression; no patient data, real APIs, or downloads.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const root = new URL("../", import.meta.url);
const port = Number(process.env.UI_TEST_PORT || 45392);
const origin = `http://127.0.0.1:${port}`;
const definitions = [
  ["spliceai", "SpliceAI", "recommended_wgs", "download"],
  ["screen_context", "ENCODE tissue and immune contexts (SCREEN)", "recommended_wgs", "download"],
  ["alphagenome_avi", "AlphaGenome AVI", "recommended_wgs", "download"],
  ["promoterai", "PromoterAI", "recommended_wgs", "prepare"],
  ["genia", "GenIA", "recommended", "manual"],
  ["dbnsfp", "dbNSFP", "optional", "manual"],
  ["logofunc", "LoGoFunc", "optional", "download"],
  ["clinvar", "ClinVar", "recommended", "download"],
  ["clingen_erepo", "ClinGen", "required", "download"],
  ["loftee", "LOFTEE", "required", "bundled"],
];
const sources = definitions.map(([id, label, recommendation, setup_mode]) => ({
  id, label, recommendation, setup_mode, description: `Synthetic ${label} dataset`,
  available: ["spliceai", "loftee", "clinvar", "clingen_erepo"].includes(id),
  installed: ["spliceai", "loftee", "clinvar", "clingen_erepo"].includes(id),
  enabled: true, required: ["spliceai", "loftee", "clingen_erepo"].includes(id),
  compact_coverage: id === "spliceai", version: "", configured_paths: [],
  available_in: ["exome", "whole_genome"], access: ["dbnsfp", "genia", "promoterai"].includes(id) ? "registration" : "public",
  reference_url: "", reference_label: "", instructions: [], size_hint: "",
  download_id: setup_mode === "download" ? id : undefined,
  prepare_id: ["dbnsfp", "promoterai"].includes(id) ? id : undefined,
}));
const capabilities = {
  platform: "darwin", pipeline_root: "/synthetic", profiles: [{ id: "local", label: "Local" }],
  defaults: { input_assembly: "auto" }, hardware: { recommended_vep_workers: 2, max_vep_workers: 4, logical_cpus: 4 },
  annotation_profile: { ready: true, datasets_ready: true, sources, foundations: [], dbnsfp_predictors: [],
    recommended_profiles: { whole_genome: { installed: false, missing: ["spliceai", "screen_context", "alphagenome_avi"],
      download_bytes: 111617245229, setup_bytes: 117 * 1024 ** 3 } } },
};
const server = spawn(process.execPath, [fileURLToPath(new URL("node_modules/next/dist/bin/next", root)),
  "start", "--hostname", "127.0.0.1", "--port", String(port)], {
  cwd: fileURLToPath(root), env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" }, stdio: "ignore",
});
let browser;
const submitted = [];
try {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(origin)).ok) break; } catch { /* starting */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  browser = await puppeteer.launch({ headless: true,
    executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1100 });
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return url.origin === origin ? request.continue() : request.abort();
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
    if (request.method() === "OPTIONS") return request.respond({ status: 204, headers });
    let body;
    if (url.pathname === "/api/capabilities") body = capabilities;
    if (["/api/jobs", "/api/resource-downloads"].includes(url.pathname)) body = { jobs: [] };
    if (url.pathname === "/api/gene-knowledge/status") body = { genia: { installed: false, components: {} }, omim: { installed: false } };
    if (request.method() === "POST") {
      submitted.push(url.pathname);
      body = { id: "synthetic", resource_id: "recommended_wgs", status: "queued" };
    }
    return request.respond({ status: body ? 200 : 503, headers, contentType: "application/json",
      body: JSON.stringify(body || { error: "Synthetic unavailable resource" }) });
  });
  await page.goto(origin, { waitUntil: "networkidle2" });
  const clickText = (text) => page.evaluate((text) => {
    const button = [...document.querySelectorAll("button")].find((el) => el.textContent.trim() === text);
    if (!button) throw new Error(`Missing button ${text}`);
    button.click();
  }, text);
  await clickText("Databases & licenses");
  await page.waitForSelector(".data-licenses-panel");
  for (const width of [1440, 1024]) {
    await page.setViewport({ width, height: 1100 });
    const aligned = await page.$eval(".nav-item.active", (el) => {
      const label = el.firstElementChild.getBoundingClientRect();
      const icon = el.lastElementChild.getBoundingClientRect();
      const button = el.getBoundingClientRect();
      return getComputedStyle(el).textAlign === "left" && label.right <= icon.left
        && label.top >= button.top && label.bottom <= button.bottom;
    });
    assert(aligned, "Navigation label must fit without overlapping its icon or adjacent rows");
  }
  await page.setViewport({ width: 1440, height: 1100 });
  await clickText("Import & QC");
  await clickText("Set up annotation datasets");
  await page.waitForSelector(".dataset-group.optional .dataset-card");
  assert.deepEqual(await page.$$eval(".dataset-quick-setup .dataset-card-title > strong", (els) => els.map((el) => el.textContent)),
    ["SpliceAI", "ENCODE tissue and immune contexts (SCREEN)", "AlphaGenome AVI"]);
  assert.deepEqual(await page.$$eval(".access-required .dataset-card-title > strong", (els) => els.map((el) => el.textContent)), ["PromoterAI", "GenIA"]);
  const optional = await page.$$eval(".dataset-group.optional .dataset-card-title > strong", (els) => els.map((el) => el.textContent));
  assert(optional.includes("dbNSFP") && optional.includes("LoGoFunc"));
  assert(await page.evaluate(() => document.body.textContent.includes("111.6 GB")));
  assert.equal(await page.$$eval(".dataset-quick-actions button", (els) => els.length), 2);
  if (process.env.UI_TEST_SCREENSHOT) await page.screenshot({ path: process.env.UI_TEST_SCREENSHOT, fullPage: true });
  await page.evaluate(() => [...document.querySelectorAll(".dataset-quick-actions button")][0].click());
  await page.waitForFunction(() => document.body.textContent.includes("Preparing recommended datasets"));
  assert.deepEqual(submitted, ["/api/resource-downloads/recommended_wgs"]);
  console.log("PASS: navigation alignment, four dataset groups, estimates, and WGS-only dispatch");
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}
