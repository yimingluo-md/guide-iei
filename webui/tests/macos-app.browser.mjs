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
  await page.goto(base, { waitUntil: "networkidle0" });
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
  // Check the accepted UI state without stopping the service needed by the
  // remaining Python integration checks; that harness tests real shutdown.
  let quitRequests = 0;
  page.removeAllListeners("request");
  page.on("request", (request) => {
    if (request.url() === base + "/api/service/quit") {
      const payload = JSON.parse(request.postData());
      assert.equal(payload.confirm, true);
      assert(payload.instance_id);
      quitRequests++;
      void request.respond({ status: 202, contentType: "application/json", body: JSON.stringify({ quitting: true, message: "GUIDE-IEI is shutting down. You can close this tab." }) });
    } else if (request.url().startsWith(base + "/") || !/^https?:/.test(request.url())) void request.continue();
    else { external.push(request.url()); void request.abort(); }
  });
  page.once("dialog", (dialog) => void dialog.accept());
  await clickQuit();
  await page.waitForFunction(() => document.body.textContent.includes("GUIDE-IEI is shutting down. You can close this tab."));
  assert.equal(quitRequests, 1);
  assert.deepEqual(errors, []);
  assert.deepEqual(external, [], "Offline review startup must not request external services");
  console.log("PACKAGED BROWSER SMOKE PASSED: engine ready/queued/running/failure/retry/recovered states, collapsed diagnostics, gene lists, synthetic VCF review, Quit cancel preserves review, Quit accept, no external requests");
} finally {
  await browser.close();
}
