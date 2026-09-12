// Optional smoke against an isolated packaged app, not the live workstation.
import assert from "node:assert/strict";
import puppeteer from "puppeteer-core";

const base = process.env.IEI_PACKAGED_TEST_URL;
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || "")) throw new Error("Set IEI_PACKAGED_TEST_URL to the isolated packaged app");
const browser = await puppeteer.launch({ headless: true,
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
try {
  const page = await browser.newPage();
  const errors = [];
  const external = [];
  let engineJob = null;
  let engineRequests = 0;
  let engineAvailable = true;
  const capabilities = await fetch(base + "/api/capabilities").then((response) => response.json());
  assert(capabilities.annotation_profile.foundations.some((item) => item.id === "vep_container"));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    if (request.url() === base + "/api/capabilities") {
      // Exercise ready/repair transitions independently of the host's Docker
      // state, without installing tools or altering the real engine.
      const snapshot = structuredClone(capabilities);
      const engine = snapshot.annotation_profile.foundations.find((item) => item.id === "vep_container");
      Object.assign(engine, { available: engineAvailable, state: engineAvailable ? "ready" : "runtime_unavailable",
        message: engineAvailable ? "" : "Synthetic engine unavailable" });
      snapshot.annotation_profile.execution_ready = engineAvailable;
      snapshot.annotation_profile.ready = engineAvailable && snapshot.annotation_profile.datasets_ready;
      void request.respond({ status: 200, contentType: "application/json", body: JSON.stringify(snapshot) });
    } else if (request.url() === base + "/api/annotation-engine/setup") {
      assert.equal(JSON.parse(request.postData()).confirm, true);
      engineRequests++;
      engineJob = { id: `test-engine-${engineRequests}`, resource_id: "annotation_engine", operation: "installation",
        status: engineRequests === 1 ? "failed" : "queued", progress: null,
        error: engineRequests === 1 ? "Synthetic setup failure" : "", message: "Test setup",
        log: "Synthetic engine setup log", created_at: new Date().toISOString() };
      void request.respond({ status: 202, contentType: "application/json", body: JSON.stringify(engineJob) });
    } else if (request.url() === base + "/api/resource-downloads") {
      void request.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ jobs: engineJob ? [engineJob] : [] }) });
    } else if (/^https?:/.test(request.url()) && !request.url().startsWith(base + "/")) {
      external.push(request.url()); void request.abort();
    } else void request.continue();
  });
  await page.setViewport({ width: 1440, height: 1000 });
  await page.goto(base, { waitUntil: "networkidle2" });
  await page.waitForFunction(() => {
    const mark = document.querySelector(".brand img.brand-mark");
    return mark?.complete && mark.naturalWidth > 0 && mark.getAttribute("src") === "/favicon.svg";
  });
  assert((await fetch(base + "/favicon.svg").then((response) => response.text())).includes("DNA and immune receptor"));
  if (process.env.IEI_PACKAGED_BRAND_SCREENSHOT) {
    await (await page.$(".brand")).screenshot({ path: process.env.IEI_PACKAGED_BRAND_SCREENSHOT });
  }
  const clickText = (text) => page.evaluate((text) => [...document.querySelectorAll("button")].find((button) => button.textContent.trim() === text).click(), text);
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent.trim() === "Set up annotation datasets"));
  await clickText("Set up annotation datasets");
  await page.waitForFunction(() => [...document.querySelectorAll(".foundation-row")].some((row) => row.textContent.includes("Ensembl VEP") && row.textContent.includes("Available")));
  assert.equal(await page.$(".annotation-engine-setup"), null, "A ready engine needs no setup card");
  assert.equal(await page.$(".annotation-engine-diagnostics"), null, "No log should be invented when there is no setup job");
  engineAvailable = false;
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent.trim() === "Set up annotation engine"));
  const cancelled = new Promise((resolve) => page.once("dialog", async (dialog) => { await dialog.dismiss(); resolve(); }));
  await clickText("Set up annotation engine"); await cancelled;
  assert.equal(engineRequests, 0);
  page.once("dialog", (dialog) => void dialog.accept());
  await clickText("Set up annotation engine");
  await page.waitForFunction(() => document.body.textContent.includes("Synthetic setup failure"));
  assert.equal(await page.$eval(".annotation-engine-diagnostics", (details) => details.open), false);
  await page.click(".annotation-engine-diagnostics > summary");
  assert.equal(await page.$eval(".annotation-engine-diagnostics pre", (pre) => pre.textContent), "Synthetic engine setup log");
  await page.click(".annotation-engine-diagnostics > summary");
  page.once("dialog", (dialog) => void dialog.accept());
  await clickText("Retry annotation-engine setup");
  await page.waitForSelector(".annotation-engine-setup progress");
  assert.equal(await page.$eval(".annotation-engine-setup button", (button) => button.disabled), true);
  engineAvailable = true; // A readiness poll must not hide an active job.
  engineJob = { ...engineJob, status: "running", message: "Synthetic engine preparation running" };
  await page.waitForFunction(() => document.querySelector(".annotation-engine-setup")?.textContent.includes("Synthetic engine preparation running"));
  engineJob = { ...engineJob, status: "succeeded", message: "Setup completed" };
  await page.waitForFunction(() => !document.querySelector(".annotation-engine-setup")
    && document.querySelector(".annotation-engine-diagnostics")?.textContent.includes("Latest setup: completed"));
  assert.equal(await page.$eval(".annotation-engine-diagnostics", (details) => details.open), false);
  assert(await page.evaluate(() => [...document.querySelectorAll(".foundation-row")].some((row) => row.textContent.includes("Ensembl VEP") && row.textContent.includes("Available"))));
  assert(!(await page.evaluate(() => document.body.textContent)).includes("Choose your datasets below."));
  if (process.env.IEI_PACKAGED_DATASET_SCREENSHOT) {
    await page.$eval(".foundation-row", (row) => row.scrollIntoView({ block: "start" }));
    await page.screenshot({ path: process.env.IEI_PACKAGED_DATASET_SCREENSHOT });
  }
  assert.equal(engineRequests, 2);
  // A stale failed job belongs in diagnostics, not a redundant repair card.
  engineJob = { ...engineJob, status: "failed", error: "Historical failure" };
  await page.waitForFunction(() => document.querySelector(".annotation-engine-diagnostics")?.textContent.includes("Latest setup: failed"));
  assert.equal(await page.$(".annotation-engine-setup"), null);
  engineAvailable = false;
  await page.waitForFunction(() => document.querySelector(".annotation-engine-setup")?.textContent.includes("Retry annotation-engine setup"));
  engineAvailable = true;
  engineJob = { ...engineJob, status: "succeeded", error: "" };
  await page.waitForFunction(() => !document.querySelector(".annotation-engine-setup"));
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent.trim().startsWith("Gene lists")));
  await page.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent.trim().startsWith("Gene lists")).click());
  await page.waitForFunction(() => document.body.textContent.includes("48 genes loaded"));
  const text = await page.evaluate(() => document.body.textContent);
  assert(text.includes("IEI autosomal-dominant"));
  assert(text.includes("IUIS 2024"));
  // Real offline file parsing/rendering, not just a hydrated landing page.
  assert(process.env.IEI_PACKAGED_TEST_VCF, "Python harness must supply a synthetic annotated VCF");
  await clickText("Import VCF");
  await page.evaluate(() => [...document.querySelectorAll('button[role="tab"]')].find((button) => button.textContent.startsWith("Review annotated VCF")).click());
  await page.waitForSelector('input[name="library-retention"]');
  await page.evaluate(() => [...document.querySelectorAll("label")].find((label) => label.textContent.startsWith("Review once")).click());
  const fileInput = await page.$('input[type="file"]');
  await fileInput.uploadFile(process.env.IEI_PACKAGED_TEST_VCF);
  await clickText("Import and review variants");
  await page.waitForFunction(() => document.body.textContent.includes("Prioritized variants"));
  await page.waitForFunction(() => document.body.textContent.includes("DEMO01") && document.querySelectorAll("tbody tr").length > 0);
  const reviewGene = await page.$eval("tbody tr td:nth-child(3)", (cell) => cell.textContent.trim());
  await page.type('input[placeholder="Gene, HGVS, ID, locus…"]', reviewGene);
  await page.waitForFunction((gene) => [...document.querySelectorAll("tbody tr")].every((row) => row.cells[2].textContent.trim() === gene), {}, reviewGene);
  await page.click("tbody .star-button");
  await page.evaluate(() => { window.__reviewTable = document.querySelector("tbody"); });
  const reviewRows = await page.$$eval("tbody tr", (rows) => rows.length);
  // Temporary transport failure is not a confirmed Quit. Recovery must keep
  // this exact review DOM (and its filters/stars), not remount or reload it.
  await page.setOfflineMode(true);
  await page.waitForFunction(() => document.querySelector(".workbench-connection-notice")?.textContent.includes("Connection lost—reconnecting…"));
  assert(!(await page.evaluate(() => document.body.textContent)).includes("GUIDE-IEI is no longer running."));
  await page.setOfflineMode(false);
  await page.waitForFunction(() => !document.querySelector(".workbench-connection-notice"));
  assert(await page.evaluate(() => document.querySelector("tbody") === window.__reviewTable));
  // Cancelling Quit must preserve the review and leave the service alive.
  const clickQuit = () => page.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent.trim() === "Quit GUIDE-IEI").click());
  const dialog = new Promise((resolve) => page.once("dialog", async (dialog) => {
    assert(dialog.message().includes("Docker will remain running"));
    await dialog.dismiss(); resolve();
  }));
  await clickQuit();
  await dialog;
  assert((await fetch(base + "/api/health").then((response) => response.json())).ok);
  assert((await page.evaluate(() => document.body.textContent)).includes("Prioritized variants"));
  const extraTab = await browser.newPage();
  await extraTab.goto(base);
  await extraTab.close();
  assert((await fetch(base + "/api/health").then((response) => response.json())).ok, "Closing a browser tab must not stop the service");
  // Check this page's Quit acknowledgement on a second tab. The main review
  // tab must later learn about the actual Quit from the service, not this POST.
  let quitRequests = 0;
  const quitTab = await browser.newPage();
  await quitTab.setRequestInterception(true);
  quitTab.on("request", (request) => {
    if (request.url() === base + "/api/service/quit") {
      const payload = JSON.parse(request.postData());
      assert.equal(payload.confirm, true);
      assert(payload.instance_id);
      quitRequests++;
      void request.respond({ status: 202, contentType: "application/json", body: JSON.stringify({ quitting: true, message: "GUIDE-IEI is shutting down. You can close this tab." }) });
    } else if (request.url().startsWith(base + "/") || !/^https?:/.test(request.url())) void request.continue();
    else { external.push(request.url()); void request.abort(); }
  });
  await quitTab.goto(base, { waitUntil: "networkidle2" });
  quitTab.once("dialog", (dialog) => void dialog.accept());
  await quitTab.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent.trim() === "Quit GUIDE-IEI").click());
  await quitTab.waitForFunction(() => document.querySelector(".workbench-connection-notice")?.textContent.includes("GUIDE-IEI is shutting down…"));
  assert.equal(quitRequests, 1);
  await quitTab.close();
  // Same real endpoint used by the native control window, deliberately called
  // outside the page so its browser Quit callback cannot fabricate the notice.
  const status = await fetch(base + "/api/service/status").then((response) => response.json());
  const stopped = await fetch(base + "/api/service/quit", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: true, instance_id: status.instance_id }) });
  assert.equal(stopped.status, 202);
  await page.waitForFunction(() => document.querySelector(".workbench-connection-notice")?.textContent.includes("GUIDE-IEI is no longer running."));
  assert(await page.evaluate(() => document.querySelector("tbody") === window.__reviewTable));
  assert.equal(await page.$$eval("tbody tr", (rows) => rows.length), reviewRows);
  assert.equal(await page.$eval('input[placeholder="Gene, HGVS, ID, locus…"]', (input) => input.value), reviewGene);
  assert.equal(await page.$$("tbody .star-button.saved").then((buttons) => buttons.length), 1);
  assert((await page.evaluate(() => document.body.textContent)).includes("DEMO01"));
  // Export must use the loaded data even though there is no server left.
  await page.evaluate(() => {
    const create = URL.createObjectURL.bind(URL);
    URL.createObjectURL = (blob) => { window.__shutdownExport = blob.text(); return create(blob); };
    // Inspect the generated export without writing synthetic data into the
    // machine's Downloads folder or invoking Chrome's download-close dialog.
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      if (this.download === "iei-prioritized-variants.tsv") return;
      return click.call(this);
    };
  });
  page.once("dialog", (dialog) => void dialog.accept());
  await clickText("Export TSV");
  const exported = await page.evaluate(() => window.__shutdownExport);
  assert(exported && exported.includes("DEMO01"), "Loaded review should export after shutdown");
  if (process.env.IEI_PACKAGED_SHUTDOWN_SCREENSHOT) {
    await page.screenshot({ path: process.env.IEI_PACKAGED_SHUTDOWN_SCREENSHOT });
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(external, [], "Offline review startup must not request external services");
  console.log("PACKAGED BROWSER SMOKE PASSED: engine ready/repair states, collapsed diagnostics, temporary disconnection/recovery, Quit cancel/accept, real external Quit notice, loaded review preserved/exported after shutdown, no external requests");
} finally {
  await browser.close();
}
