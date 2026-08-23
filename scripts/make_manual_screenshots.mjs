#!/usr/bin/env node
// Regenerate the user-manual screenshots from the synthetic demonstration
// exome (scripts/make_demo_vcf.py). Drives a private headless Chrome against
// a running workbench UI; imports use "Review once" so nothing is written to
// the Sample Library. All visible data is synthetic (sample DEMO01).
//
//   node scripts/make_manual_screenshots.mjs <ui-url> <demo-vcf> <out-dir>
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

// puppeteer-core is a webui devDependency; resolve it from there so the
// script runs from any working directory.
const require = createRequire(new URL("../webui/package.json", import.meta.url));
const puppeteer = require("puppeteer-core");

const [uiUrl = "http://127.0.0.1:3000", demoVcf, outDir = "docs/assets/img"] = process.argv.slice(2);
if (!demoVcf) {
  console.error("usage: make_manual_screenshots.mjs <ui-url> <demo-vcf> <out-dir>");
  process.exit(2);
}
await mkdir(outDir, { recursive: true });

const CHROME = process.env.IEI_CHROME
  ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "shell",
  args: ["--hide-scrollbars", "--force-color-profile=srgb"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
// The app asks reviewer-facing window.confirm questions; accept them, and
// surface console errors so silent failures are visible in the run log.
page.on("dialog", (dialog) => {
  console.log("dialog:", dialog.message().slice(0, 160));
  void dialog.accept();
});
page.on("console", (message) => {
  if (message.type() === "error") console.log("console.error:", message.text().slice(0, 200));
});
page.on("pageerror", (error) => console.log("pageerror:", String(error).slice(0, 200)));

// Screenshots are published in the manual: hide this workstation's own
// job history and recent-file lists, and refuse any capture whose visible
// text contains a local path or a real cohort name.
const FORBIDDEN = ["/Users/", "/Volumes/", "COLUMBIA"];
const hideHistory = () => page.addStyleTag({
  content: ".job-list, .recent-review-card, .configured-locations { display: none !important; }",
});
const shot = async (name, options = {}) => {
  const { skipHide, ...screenshotOptions } = options;
  options = screenshotOptions;
  if (!skipHide) await hideHistory();
  await new Promise((resolve) => setTimeout(resolve, 400));
  const visible = await page.evaluate(() => document.body.innerText);
  for (const needle of FORBIDDEN) {
    if (visible.includes(needle)) {
      throw new Error(`refusing ${name}: visible text contains "${needle}"`);
    }
  }
  await page.screenshot({ path: path.join(outDir, `${name}.png`), ...options });
  console.log("captured", name);
};
const clickText = async (selector, text) => {
  const clicked = await page.evaluate((sel, wanted) => {
    const target = [...document.querySelectorAll(sel)]
      .find((el) => el.textContent?.trim().toLowerCase().includes(wanted.toLowerCase()));
    if (target) { target.click(); return true; }
    return false;
  }, selector, text);
  if (!clicked) throw new Error(`no ${selector} containing "${text}"`);
};

const awaitServiceReady = () => page.waitForFunction(
  () => /service ready/i.test(document.body.textContent ?? ""),
  { timeout: 30_000 },
);
await page.goto(uiUrl, { waitUntil: "networkidle2", timeout: 60_000 });
await awaitServiceReady();
await new Promise((resolve) => setTimeout(resolve, 800));

// 1. Import screen with "Run VEP first" selected (chapter 4).
await clickText("button", "Run VEP first");
await shot("import-run-vep-first");

// 2. Back to review intake, stage the demo exome, review once.
await clickText("button", "Review annotated VCF");
const fileInput = await page.$("input[type=file]");
if (!fileInput) throw new Error("no file input on the import screen");
await fileInput.uploadFile(demoVcf);
await new Promise((resolve) => setTimeout(resolve, 1200));
// Nothing may be written to the real library: select "Review once" and
// verify it actually took — a silent miss here writes into the library.
const reviewOnce = await page.evaluate(() => {
  const labels = [...document.querySelectorAll("label")]
    .filter((el) => el.querySelector("input[type=radio]"));
  const target = labels.find((el) => /Review once/i.test(el.textContent ?? ""));
  if (!target) return "no-label";
  target.click();
  const radio = target.querySelector("input[type=radio]");
  return radio?.checked ? "selected" : "not-selected";
});
if (reviewOnce !== "selected") throw new Error(`Review once radio: ${reviewOnce}`);
await new Promise((resolve) => setTimeout(resolve, 400));
await shot("import-file-staged");
await clickText("button", "Import and review variants");
try {
  await page.waitForFunction(
    () => document.body.textContent?.includes("STAT1"),
    { timeout: 45_000 },
  );
} catch (error) {
  await shot("debug-after-import", { fullPage: true });
  const text = await page.evaluate(() => document.body.innerText.slice(0, 1200));
  console.error("timed out waiting for review; page text:\n", text);
  throw error;
}
await new Promise((resolve) => setTimeout(resolve, 1200));

// Belt and braces: if the identity dialog appears anyway, close it and abort
// so the stray library write is noticed.
const modal = await page.evaluate(() => document.body.textContent?.includes("belong to?") ?? false);
if (modal) throw new Error("identity dialog appeared — the import was NOT review-once");

// 3. Review results list with the filter rail (chapter 4).
await shot("review-variant-list");

// 4. Variant selected: header evidence (chapters 4 and 9).
await page.evaluate(() => {
  const row = [...document.querySelectorAll("tr, article, li, div[role=row]")]
    .find((el) => el.textContent?.includes("STAT1") && el.textContent?.includes("p.Gln274Arg"));
  row?.click();
});
await new Promise((resolve) => setTimeout(resolve, 1500));
await shot("review-workspace-selected");

// 5. Scrolled evidence sections: predictors, call quality, ClinVar (chapter 9).
await page.evaluate(() => {
  const heading = [...document.querySelectorAll("h2, h3")]
    .find((el) => /Predictors/i.test(el.textContent ?? ""));
  heading?.scrollIntoView({ block: "start" });
});
await shot("variant-evidence-sections");

// 6. Dataset setup screen with the dataset cards (chapter 3).
await page.goto(uiUrl, { waitUntil: "networkidle2" });
await awaitServiceReady();
await new Promise((resolve) => setTimeout(resolve, 800));
await clickText("button", "Run VEP first");
await clickText("button", "Set up annotation datasets");
await new Promise((resolve) => setTimeout(resolve, 2500));
await shot("dataset-cards");

// 7. The dbNSFP card with its guided instructions (chapter 3).
await page.evaluate(() => {
  const card = [...document.querySelectorAll("article, section, div")]
    .find((el) => /dbNSFP/.test(el.textContent ?? "")
      && el.querySelector("button, summary")
      && (el.className ?? "").toString().includes("card"));
  card?.scrollIntoView({ block: "start" });
  const expand = [...(card?.querySelectorAll("summary, button") ?? [])]
    .find((el) => /instructions|guide|details|set up|register/i.test(el.textContent ?? ""));
  expand?.click();
});
await new Promise((resolve) => setTimeout(resolve, 900));
await shot("dataset-dbnsfp-card");

// 8. "Check annotation settings" step of Run VEP first (chapter 4).
await page.goto(uiUrl, { waitUntil: "networkidle2" });
await awaitServiceReady();
await new Promise((resolve) => setTimeout(resolve, 800));
await clickText("button", "Run VEP first");
const vepInput = await page.$("input[type=file]");
await vepInput.uploadFile(demoVcf);
await new Promise((resolve) => setTimeout(resolve, 1500));
const continueClicked = await page.evaluate(() => {
  const button = [...document.querySelectorAll("button")]
    .find((el) => /continue|next|annotation settings/i.test(el.textContent ?? "")
      && !el.disabled);
  if (button) { button.click(); return button.textContent?.trim(); }
  return null;
});
console.log("step-2 via:", continueClicked);
await new Promise((resolve) => setTimeout(resolve, 2000));
await shot("annotation-settings");

await browser.close();
console.log("done");
