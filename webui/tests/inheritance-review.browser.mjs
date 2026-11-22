// Run against a locally built workbench with UI_TEST_PORT set to its port.
import assert from "node:assert/strict";
import puppeteer from "puppeteer-core";

const origin = `http://127.0.0.1:${process.env.UI_TEST_PORT || 3000}`;
const browser = await puppeteer.launch({ headless: true,
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
try {
  const page = await browser.newPage();
  page.on("pageerror", error => console.error(error.message));
  await page.setViewport({ width: 1440, height: 1000 });
  await page.setRequestInterception(true);
  page.on("request", req => {
    const url = new URL(req.url());
    if (!url.pathname.startsWith("/api/")) return url.origin === origin ? req.continue() : req.abort();
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
    if (req.method() === "OPTIONS") return req.respond({ status: 204, headers });
    const replies = {
      "/api/service/watch": { service: "GUIDE-IEI", instance_id: "synthetic-service", watch_supported: false },
      "/api/cohort/query": { rows: [], total: 0, variants: 0, individuals: 0, represented_profiles: [] },
    };
    return req.respond({ status: url.pathname in replies ? 200 : 503,
      headers, contentType: "application/json", body: JSON.stringify(replies[url.pathname] ?? {}) });
  });
  await page.goto(origin, { waitUntil: "networkidle2" });
  const clickText = (selector, text) => page.locator(`${selector} ::-p-text(${text})`).click();
  async function importFiles(files) {
    await clickText(".nav-item", "Import & QC");
    await clickText(".intake-mode-switch", "Review annotated VCF");
    await clickText(".library-retention-choice", "Review once");
    await page.$eval(".intake-primary-card input[type=file]", (el, files) => {
      const transfer = new DataTransfer();
      for (const [name, data] of files) transfer.items.add(new File([data], name));
      el.files = transfer.files;
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }, files);
    await clickText(".review-import-actions", "Import and review variants");
  }
  const gt = ["0/0:30:99:30,0", "0/1:30:99:15,15", "1/1:30:99:0,30"];
  const vcf = ["##fileformat=VCFv4.2", "##reference=GRCh38",
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|SYMBOL|IMPACT|MANE_SELECT|Feature|Consequence">',
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tCHILD\tMOTHER\tFATHER",
    ...[[100, "G1", 1, 1, 0], [200, "G1", 1, 0, 1], [300, "G1", 2, 1, 1],
      [400, "G2", 1, 1, 0], [500, "G2", 1, 1, 0]].map(([pos, gene, c, m, f]) =>
      [1, pos, ".", "A", "G", 99, "PASS", `CSQ=G|${gene}|MODERATE|NM_1|ENST1|missense_variant`,
        "GT:DP:GQ:AD", gt[c], gt[m], gt[f]].join("\t")), ""].join("\n");
  await importFiles([["synthetic-family.vcf", vcf]]);
  const selector = ".table-frame tbody tr";
  const genotypeSelect = 'select:has(option[value="compound"])';
  const count = n => page.waitForFunction((selector, n) => document.querySelectorAll(selector).length === n, {}, selector, n);
  const select = async value => { await page.select(genotypeSelect, value); };
  async function badge(pos, sample, expected) {
    await page.waitForSelector(selector);
    const index = await page.$$eval(selector, (rows, pos, sample) => rows.findIndex(row =>
      row.querySelector(".variant-id").title === `1:${pos}:A:G` && row.cells[1].innerText.includes(sample + " ·")), pos, sample);
    assert.notEqual(index, -1, `missing ${sample} row at ${pos}`);
    const target = `${selector}:nth-child(${index + 1})`;
    assert.equal(await page.$eval(target, el => !!el.cells[2].querySelector(".mini-badge")), expected);
    await page.click(target);
    await page.waitForSelector(".review-hero");
    assert.equal(await page.$eval(".review-kickers", el => el.textContent.includes("candidate comp het")), expected);
    await page.locator(".review-toolbar .back-button").click();
    await page.waitForSelector(selector);
  }
  await count(11);
  await badge(100, "CHILD", true);
  await badge(300, "CHILD", false);
  await select("compound");
  await count(10);
  await select("all");
  await clickText(".nav-item", "Family analysis");
  const mapping = ".family-mapping-grid label";
  for (const [index, sample] of [[2, "CHILD"], [3, "MOTHER"], [4, "FATHER"], [5, "female"]]) {
    await page.select(`${mapping}:nth-child(${index}) select`, sample);
  }
  await page.waitForSelector(".family-stats");
  await clickText(".nav-item", "Variants");
  await count(11);
  await badge(100, "CHILD", true);
  await badge(100, "MOTHER", false);
  await badge(400, "CHILD", false);
  await clickText(".nav-item", "Comp het");
  await count(2);
  await clickText(".nav-item", "Variants");
  await select("compound");
  await count(2);

  await select("all");
  // Sixteen single-sample inputs exercise browser cohort aggregation. The
  // representative is heterozygous at both sites. A second carrier is
  // diploid homozygous at 100 and haploid at 200.
  const cohortFiles = Array.from({ length: 16 }, (_, i) => {
    const sample = "SYNTHETIC_" + i;
    const header = vcf.slice(0, vcf.indexOf("#CHROM")) +
      "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + sample;
    const records = [100, 200].map(pos => [1, pos, ".", "A", "G", 99, "PASS",
      "CSQ=G|G1|MODERATE|NM_1|ENST1|missense_variant", "GT:DP:GQ:AD",
      i === 0 ? gt[1] : i > 1 ? gt[0] : pos === 100 ? gt[2] : "1:30:99:0,30"].join("\t"));
    return [sample + ".vcf", [header, ...records, ""].join("\n")];
  });
  await importFiles(cohortFiles);
  await count(2);
  await select("hom");
  await count(1);
  assert.equal(await page.$eval(selector + " .variant-id", el => el.title), "1:100:A:G");
  assert.match(await page.$eval(selector, el => el.textContent), /2 carry/);

  await clickText(".nav-item", "Cohort search");
  const selectMode = mode => clickText(".cohort-query-tabs", mode);
  async function submit(mode, impacts, maneOnly) {
    const request = page.waitForRequest(req => req.url().endsWith("/api/cohort/query") && req.method() === "POST");
    await clickText(".query-card", "Find carriers");
    const payload = JSON.parse((await request).postData());
    assert.equal(payload.mode, mode);
    assert.deepEqual(payload.impacts?.sort(), impacts?.sort());
    assert.equal(payload.mane_only, maneOnly);
  }
  await selectMode("Qualifying variants in gene");
  await page.locator('.query-card input[placeholder="NFKB1"]').fill("G1");
  await submit("gene", ["HIGH", "MODERATE"], true);
  await page.click(".query-card .impact-chip.high");
  await selectMode("Genomic region");
  await page.locator('.query-card input[placeholder*="5 Mb"]').fill("1:100-500");
  await submit("region", ["HIGH", "MODERATE", "LOW", "MODIFIER"], false);
  await page.click(".query-card .impact-chip.low");
  await selectMode("Predicted target gene");
  await submit("target_gene", ["HIGH", "MODERATE", "LOW", "MODIFIER"], false);
  await page.click(".qualifying-checks input");
  await selectMode("Exact variant");
  await page.locator('.query-card input[placeholder*="rs"]').fill("1:100:A:G");
  await submit("variant", undefined, undefined);
  await selectMode("Gene list");
  await page.locator(".query-card textarea").fill("G1");
  await submit("gene_list", ["MODERATE"], true);
  await selectMode("Predicted target gene");
  await submit("target_gene", ["HIGH", "MODERATE", "LOW", "MODIFIER"], true);
  await selectMode("Genomic region");
  await submit("region", ["HIGH", "MODERATE", "MODIFIER"], false);
  console.log("PASS: imported family, list/detail badges, both compound controls, 16-sample homozygous filtering, and seven query submissions across five modes");
} finally {
  await browser.close();
}
