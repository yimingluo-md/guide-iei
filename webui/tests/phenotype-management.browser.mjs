// Run against the built local UI on UI_TEST_PORT. Every API response is synthetic.
import assert from "node:assert/strict";
import puppeteer from "puppeteer-core";

const origin = `http://127.0.0.1:${Number(process.env.UI_TEST_PORT || 3000)}`;
const ids = Array.from({ length: 502 }, (_, i) => `Case-${String(i + 1).padStart(3, "0")}`);
const datasets = [{ id: "new-dataset", vcf_sample_name: "SAMPLE", individual_id: null }];
const vcf = [
  "##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
  '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Feature|PICK">',
  "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE",
  "1\t100\t.\tA\tG\t99\tPASS\tCSQ=G|missense_variant|MODERATE|GENE|ENST|1\tGT:DP:GQ:AD\t0/1:40:99:20,20", "",
].join("\n");
const browser = await puppeteer.launch({ headless: true,
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  await page.setRequestInterception(true);
  let mapped;
  page.on("request", request => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return url.origin === origin ? request.continue() : request.abort();
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*", "Access-Control-Expose-Headers": "Content-Disposition" };
    if (request.method() === "OPTIONS") return request.respond({ status: 204, headers });
    const json = value => request.respond({ headers, contentType: "application/json", body: JSON.stringify(value) });
    switch (url.pathname) {
      case "/api/phenotypes": {
        const matches = ids.filter(id => id.includes(url.searchParams.get("query") || ""));
        const offset = Number(url.searchParams.get("offset"));
        const limit = Number(url.searchParams.get("limit"));
        return json({ individuals: matches.slice(offset, offset + limit).map(individual_id => ({ individual_id })),
          has_more: offset + limit < matches.length });
      }
      case "/api/phenotypes/stats": return json({ individuals: ids.length });
      case "/api/phenotypes/profiles": return json({ profiles: [] });
      case "/api/phenotypes/ids": return json({ ids });
      case "/api/annotation-files/stage": return json({ path: "/synthetic/input.vcf" });
      case "/api/sample-library/inspect": return json({ inspections: [] });
      case "/api/sample-library/import": return json({ datasets, imports: [{ import_outcome: "new", datasets }] });
      case "/api/sample-library/review-file":
        return request.respond({ headers: { ...headers, "Content-Disposition": 'attachment; filename="review.vcf"' },
          contentType: "text/vcf", body: vcf });
      case "/api/sample-library/new-dataset/identity":
        mapped = JSON.parse(request.postData());
        return json(datasets[0]);
      default: return request.respond({ headers, status: 503, contentType: "application/json", body: "{}" });
    }
  });
  await page.goto(origin, { waitUntil: "networkidle2" });
  const click = (scope, label) => page.locator(`${scope} ::-p-text(${label})`).click();
  await click(".nav-item", "Phenotypes");
  for (let offset = 0; offset <= 500; offset += 50) {
    await page.waitForFunction(id => document.querySelector(".individual-card strong")?.textContent === id, {}, ids[offset]);
    if (offset < 500) await click(".library-pagination", "Next");
  }
  assert.equal(await page.$$eval(".individual-card", cards => cards.length), 2);
  assert.equal(await page.$eval(".library-pagination button:last-child", button => button.disabled), true);
  await click(".individual-grid", "Case-502");
  await page.locator(".phenotype-card-head ::-p-text(Edit Case-502)").wait();
  await click(".phenotype-tabs", "Individuals");
  await click(".library-pagination", "Previous");
  await page.locator(".individual-card ::-p-text(Case-451)").wait();
  await page.locator(".phenotype-record-toolbar input").fill("Case-502");
  await page.keyboard.press("Enter");
  await page.locator(".individual-card ::-p-text(Case-502)").wait();
  assert.equal(await page.$(".library-pagination"), null);

  await click(".nav-item", "Import & QC");
  await click(".intake-mode-switch", "Review annotated VCF");
  await page.$eval('.intake-primary-card input[type="file"]', (input, data) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([data], "synthetic.vcf"));
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, vcf);
  await click(".review-import-actions", "Import and review variants");
  await page.waitForSelector(".identity-dialog, .import-page .alert.error");
  const importError = await page.$(".import-page .alert.error");
  if (importError) throw new Error(await importError.evaluate(el => el.textContent));
  await click(".identity-dialog", "Link existing individual");
  await page.waitForSelector('.identity-dialog select option[value="Case-502"]');
  await page.select(".identity-dialog select", "Case-502");
  await page.click(".identity-dialog .primary-button");
  await page.waitForSelector(".identity-dialog", { hidden: true });
  assert.deepEqual(mapped, { mode: "existing", individual_id: "Case-502" });
  console.log("PASS: 11 pages, last-record edit, previous page, search reset, and linking individual 502");
} finally {
  await browser.close();
}
