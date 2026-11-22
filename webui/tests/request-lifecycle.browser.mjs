// Run against the local web UI with UI_TEST_PORT. Deferred synthetic responses
// deliberately ignore AbortSignal so late completions reach the actual handlers.
import assert from "node:assert/strict";
import puppeteer from "puppeteer-core";

const origin = `http://127.0.0.1:${Number(process.env.UI_TEST_PORT || 3000)}`;
const browser = await puppeteer.launch({ headless: true,
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(10000);
  await page.evaluateOnNewDocument(() => {
    const realFetch = window.fetch.bind(window);
    window.fixture = { calls: [], gates: [], updates: 0 };
    window.addEventListener("gene-knowledge-updated", () => window.fixture.updates++);
    const datasets = ["A", "B"].map(id => ({
      id, sample_label: id, vcf_sample_name: id, profile_label: "Test", analysis_scope: "exome",
      managed_size_bytes: 200, is_current: true, callset_id: id, version_id: id, version_number: 1,
      original_name: id + ".vcf", imported_at: "2026-01-01T00:00:00Z",
    }));
    window.fetch = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input, location.href);
      if (!url.pathname.startsWith("/api/")) return realFetch(input, init);
      const { calls, gates } = window.fixture;
      calls.push(url.pathname);
      const gate = gates.find(g => g.path === url.pathname && !g.started);
      if (gate) {
        gate.started = true;
        gate.signal = init?.signal;
        return new Promise(resolve => { gate.respond = resolve; });
      }
      const replies = {
        "/api/resource-downloads": { jobs: [] },
        "/api/resource-downloads/gene_knowledge": { id: "refresh", status: "running" },
        "/api/gene-knowledge/status": { resources: [] },
        "/api/software-update/status": { restart_pending: true },
        "/api/service/restart": { restarting: true },
        "/api/annotation-files/stage": { path: "/synthetic/staged.vcf" },
        "/api/sample-library/inspect": { inspections: [] },
        "/api/sample-library": { datasets },
        "/api/cohort/profiles": { profiles: [] },
        "/api/cohort/stats": { individuals: 2, files: 2, variants: 1, carrier_observations: 2,
          full_files: 2, prefiltered_files: 0 },
      };
      return Response.json(replies[url.pathname] ?? {}, { status: url.pathname in replies ? 200 : 503 });
    };
  });
  await page.goto(origin, { waitUntil: "networkidle2" });
  const nav = label => page.locator(`.nav-item ::-p-text(${label})`).click();
  const click = (parent, label) => page.locator(`${parent} ::-p-text(${label})`).click();
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const count = path => page.evaluate(p => window.fixture.calls.filter(call => call === p).length, path);
  async function defer(path) {
    const id = await page.evaluate(path => window.fixture.gates.push({ path }) - 1, path);
    return {
      started: () => page.waitForFunction(i => window.fixture.gates[i].started, {}, id),
      aborted: () => page.evaluate(i => window.fixture.gates[i].signal?.aborted ?? false, id),
      release: (body, status = 200) => page.evaluate(async (i, body, status) => {
        const gate = window.fixture.gates[i];
        gate.respond(typeof body === "string"
          ? new Response(body, { status, headers: { "Content-Type": "text/vcf" } })
          : Response.json(body, { status }));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, id, body, status),
    };
  }

  await nav("Gene knowledge");
  const downloads = "/api/resource-downloads";
  const initialPolls = await count(downloads);
  await click(".gene-resource-section", "Check and update now");
  await page.locator(".alert ::-p-text(no longer available)").wait();
  await pause(850);
  assert.equal(await count(downloads), initialPolls + 1, "a missing job must end polling");
  const genePoll = await defer(downloads);
  await click(".gene-resource-section", "Check and update now");
  await genePoll.started();
  await nav("About & updates");
  assert.equal(await genePoll.aborted(), true);
  await genePoll.release({ jobs: [{ id: "refresh", status: "succeeded" }] });
  assert.equal(await page.evaluate(() => window.fixture.updates), 0);

  const health = await defer("/api/health");
  await click(".about-panel", "Restart the workbench");
  await health.started();
  await nav("Sample library");
  assert.equal(await health.aborted(), true);
  await health.release({ ok: true });
  await page.waitForSelector(".library-page");
  assert.equal(await count("/api/service/restart"), 1, "one authorized restart survives navigation");

  // Leave during the delay before the first poll as well as during a fetch.
  await nav("Gene knowledge");
  await click(".gene-resource-section", "Check and update now");
  await nav("About & updates");
  const previousHealth = await count("/api/health");
  await click(".about-panel", "Restart the workbench");
  await nav("Sample library");
  await pause(1100);
  assert.equal(await count(downloads), initialPolls + 2);
  assert.equal(await count("/api/health"), previousHealth);

  const vcf = [
    "##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Feature|MANE_SELECT|PICK">',
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tA",
    "1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE_A|ENST_A|NM_A|1\tGT:DP:GQ:AD\t0/1:40:99:20,20", "",
  ].join("\n");
  const combined = await defer("/api/sample-library/review-file");
  await click(".library-combined-bar", "Select all current samples");
  await click(".library-combined-bar", "Open combined review");
  await combined.started();
  const single = await defer("/api/sample-library/A/file");
  await click(".library-sample-card", "Open review");
  await single.started();
  await combined.release({ error: "obsolete combined open" }, 503);
  assert.match(await page.$eval(".library-page", el => el.innerText), /Opening the managed review VCF/);
  assert.doesNotMatch(await page.$eval(".library-page", el => el.innerText), /obsolete combined open/);
  await nav("Cohort search");
  await single.release(vcf);
  await page.waitForSelector(".cohort-page");

  const submit = async value => {
    const selector = '.query-card input[placeholder*="rs"]';
    await page.locator(selector).fill(value);
    await page.focus(selector);
    await page.keyboard.press("Enter");
  };
  const query = "/api/cohort/query";
  const oldSuccess = await defer(query);
  await submit("rs1");
  await oldSuccess.started();
  const oldFailure = await defer(query);
  await submit("rs2");
  await oldFailure.started();
  const latest = await defer(query);
  await submit("rs3");
  await latest.started();
  const result = n => ({ rows: [], variants: n, individuals: 0, total: 0, represented_profiles: [] });
  await oldFailure.release({ error: "obsolete query" }, 500);
  assert.equal(await page.$eval(".cohort-query-footer button", el => el.disabled), true);
  assert.equal(await page.$(".cohort-alert"), null);
  await latest.release(result(3));
  await oldSuccess.release(result(1));
  assert.match(await page.$eval(".cohort-results-head", el => el.innerText), /3 unique variants/);

  const samples = "/api/cohort/samples";
  const initialSamples = await defer(samples);
  await click(".cohort-page", "Manage samples");
  await initialSamples.started();
  const newerSamples = await defer(samples);
  await page.locator(".sample-manager-toolbar input").fill("SYNTHETIC_ONLY");
  await page.keyboard.press("Enter");
  await newerSamples.started();
  await initialSamples.release({ error: "obsolete sample query" }, 500);
  assert.match(await page.$eval(".sample-manager-toolbar", el => el.innerText), /Loading…/);
  assert.equal(await page.$(".cohort-alert"), null);
  await click(".cohort-page", "Close sample manager");
  await newerSamples.release({ error: "closed manager" }, 503);
  assert.equal(await page.$(".cohort-alert"), null);

  await nav("Import & QC");
  await click(".intake-mode-switch", "Review annotated VCF");
  async function stage(name) {
    await page.$eval('.intake-primary-card input[type="file"]', (input, name, data) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([data], name, { type: "text/vcf" }));
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }, name, vcf);
  }
  const importPath = "/api/sample-library/import";
  const firstImport = await defer(importPath);
  await stage("first.vcf");
  await click(".review-import-actions", "Import and review variants");
  await firstImport.started();
  const nextImport = await defer(importPath);
  await stage("second.vcf");
  await click(".review-import-actions", "Import and review variants");
  await nextImport.started();
  await firstImport.release({ error: "obsolete import" }, 503);
  assert.equal(await page.$eval(".review-import-actions button", el => el.disabled), true);
  assert.doesNotMatch(await page.$eval(".import-page", el => el.innerText), /obsolete import/);
  await nav("Sample library");
  const projections = await count("/api/sample-library/review-file");
  await nextImport.release({ datasets: [{ id: "A" }], imports: [{ datasets: [{ id: "A" }] }] });
  assert.equal(await count("/api/sample-library/review-file"), projections);
  assert.equal(await nextImport.aborted(), false, "leaving import must preserve the authorized save");
  await nav("Import & QC");
  assert.equal(await page.$eval(".review-import-actions button", el => el.disabled), false);
  assert.match(await page.$eval(".pending-review-files", el => el.innerText), /second.vcf/);
  console.log("PASS: gene update, restart, library opens, cohort query and import lifecycles");
} finally {
  await browser.close();
}
