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
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    if (/^https?:/.test(request.url()) && !request.url().startsWith(base + "/")) {
      external.push(request.url()); void request.abort();
    } else void request.continue();
  });
  await page.setViewport({ width: 1440, height: 1000 });
  await page.goto(base, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent.trim().startsWith("Gene lists")));
  await page.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent.trim().startsWith("Gene lists")).click());
  await page.waitForFunction(() => document.body.textContent.includes("48 genes loaded"));
  const text = await page.evaluate(() => document.body.textContent);
  assert(text.includes("IEI autosomal-dominant"));
  assert(text.includes("IUIS 2024"));
  assert.deepEqual(errors, []);
  assert.deepEqual(external, [], "Offline review startup must not request external services");
  console.log("PACKAGED BROWSER SMOKE PASSED: hydrated interface, gene lists, 48-gene HI default, no external requests");
} finally {
  await browser.close();
}
