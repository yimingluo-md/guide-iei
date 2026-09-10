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
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    if (request.url() === base + "/api/annotation-engine/setup") {
      assert.equal(JSON.parse(request.postData()).confirm, true);
      engineRequests++;
      engineJob = { id: `test-engine-${engineRequests}`, resource_id: "annotation_engine", operation: "installation",
        status: engineRequests === 1 ? "failed" : "succeeded", progress: null,
        error: engineRequests === 1 ? "Synthetic setup failure" : "", message: "Test setup",
        log: "Synthetic engine setup log", created_at: new Date().toISOString() };
      void request.respond({ status: 202, contentType: "application/json", body: JSON.stringify(engineJob) });
    } else if (engineJob && request.url() === base + "/api/resource-downloads") {
      void request.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ jobs: [engineJob] }) });
    } else if (/^https?:/.test(request.url()) && !request.url().startsWith(base + "/")) {
      external.push(request.url()); void request.abort();
    } else void request.continue();
  });
  await page.setViewport({ width: 1440, height: 1000 });
  await page.goto(base, { waitUntil: "networkidle0" });
  const clickText = (text) => page.evaluate((text) => [...document.querySelectorAll("button")].find((button) => button.textContent.trim() === text).click(), text);
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent.trim() === "Set up annotation datasets"));
  await clickText("Set up annotation datasets");
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent.trim() === "Set up annotation engine"));
  const cancelled = new Promise((resolve) => page.once("dialog", async (dialog) => { await dialog.dismiss(); resolve(); }));
  await clickText("Set up annotation engine"); await cancelled;
  assert.equal(engineRequests, 0);
  page.once("dialog", (dialog) => void dialog.accept());
  await clickText("Set up annotation engine");
  await page.waitForFunction(() => document.body.textContent.includes("Synthetic setup failure"));
  assert((await page.evaluate(() => document.body.textContent)).includes("Annotation-engine setup log"));
  page.once("dialog", (dialog) => void dialog.accept());
  await clickText("Retry annotation-engine setup");
  await page.waitForFunction(() => document.body.textContent.includes("Annotation engine setup completed."));
  assert.equal(engineRequests, 2);
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
  console.log("PACKAGED BROWSER SMOKE PASSED: gene lists, synthetic VCF review, Quit cancel preserves review, Quit accept, no external requests");
} finally {
  await browser.close();
}
