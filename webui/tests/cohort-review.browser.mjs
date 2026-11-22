// Run against the local web UI with UI_TEST_PORT set to its port.
import assert from "node:assert/strict";
import puppeteer from "puppeteer-core";

const origin = `http://127.0.0.1:${Number(process.env.UI_TEST_PORT || 3000)}`;
const variantKey = "1:100:A:G";
const carriers = ["SYNTHETIC_A", "SYNTHETIC_B"].map((sample, i) => ({
  sample, sample_entry_id: i + 1, source_path: `/synthetic/${sample}.vcf`,
  chrom: "1", pos: 100, ref: "A", alt: "G", variant_key: variantKey,
  hgvsp: "value\twith\r\ncontrols", transcript: "ENST_TEST", gene: "A1BG", impact: "MODERATE", consequence: "missense_variant", mane: true, clinvar: "",
  import_profile: "full", analysis_scope: "exome", genotype: "0/1", zygosity: "heterozygous",
  gnomad_popmax: null, cadd: null, alpha_missense: null, spliceai: null, promoterai: null,
}));

function vcf(carrier, clinical) {
  const first = carrier === carriers[0];
  const fields = ["Allele", "Consequence", "IMPACT", "SYMBOL", "Feature", "MANE_SELECT", "MANE_PLUS_CLINICAL", "PICK"];
  if (first) fields.push("am_pathogenicity");
  const annotation = (feature, select = "", plus = "") =>
    ["G", "missense_variant", "MODERATE", "A1BG", feature, select, plus, "", ...(first ? [""] : [])].join("|");
  const records = [100];
  if (first) records.push(200);
  return [
    "##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
    `##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: ${fields.join("|")}">`,
    ...(first ? [] : ['##INFO=<ID=SpliceAI,Number=.,Type=String,Description="Splicing predictions">']),
    `#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t${carrier.sample}`,
    ...records.map(pos => {
      const transcripts = [annotation("ENST_OTHER")];
      if (clinical) transcripts.push(annotation("ENST_PLUS", "", "NM_PLUS"), annotation("ENST_SELECT", "NM_SELECT"));
      return `1\t${pos}\t.\tA\tG\t99\tPASS\tCSQ=${transcripts.join(",")}\tGT:DP:GQ:AD\t0/1:2:9:1,1`;
    }), "",
  ].join("\n");
}

const browser = await puppeteer.launch({ headless: true,
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  let clinical = true;
  const handoffs = [];
  await page.setRequestInterception(true);
  page.on("request", request => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return url.origin === origin ? request.continue() : request.abort();
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
    if (request.method() === "OPTIONS") return request.respond({ status: 204, headers });
    const json = body => request.respond({ headers, contentType: "application/json", body: JSON.stringify(body) });
    const file = carrier => ({ name: carrier.sample + ".vcf", source_path: carrier.source_path,
      import_profile: "full", analysis_scope: "exome" });
    switch (url.pathname) {
      case "/api/cohort/stats": return json({ individuals: 2, files: 2, variants: 2,
        full_files: 2, prefiltered_files: 0, carrier_observations: 3 });
      case "/api/cohort/profiles": return json({ profiles: [] });
      case "/api/cohort/query": return json({ rows: carriers, variants: 1, individuals: 2, total: 2,
        represented_profiles: [], truncated: false });
      case "/api/cohort/review-records":
        handoffs.push(JSON.parse(request.postData()));
        return json({ files: carriers.map(carrier => ({ ...file(carrier), vcf: vcf(carrier, clinical),
          selections: [carrier] })), warnings: [] });
      case "/api/cohort/sample-review":
        handoffs.push(JSON.parse(request.postData()));
        return json({ files: carriers.map((carrier, i) => ({ ...file(carrier), samples: [carrier],
          record_count: i === 0 ? 2 : 1, vcf_url: `/api/synthetic/${i}` })),
          analysis_scope: "exome", records: 3, sample_entries: 2, warnings: [] });
      case "/api/synthetic/0":
      case "/api/synthetic/1":
        return request.respond({ headers, contentType: "text/vcf",
          body: vcf(carriers[Number(url.pathname.at(-1))], clinical) });
      default: return request.respond({ status: 503, headers, contentType: "application/json", body: "{}" });
    }
  });
  await page.goto(origin, { waitUntil: "networkidle2" });

  async function openSelection(action) {
    await page.locator('.nav-item ::-p-text(Cohort search)').click();
    await page.locator('.query-card input[placeholder*="rs"]').fill(variantKey);
    await page.locator('.query-card ::-p-text(Find carriers)').click();
    await page.locator('.cohort-header-actions ::-p-text(Export carriers)').wait();
    const exported = await page.evaluate(async () => {
      const create = URL.createObjectURL;
      let blob;
      try {
        URL.createObjectURL = value => { blob = value; return create(value); };
        window.confirm = () => true;
        document.querySelector(".cohort-header-actions button").click();
        return await blob.text();
      } finally {
        URL.createObjectURL = create;
      }
    });
    const [notice, header, ...records] = exported.slice(0, -1).split("\n");
    assert.match(notice, /^# Research use only/);
    const columns = header.split("\t");
    assert.equal(records.length, carriers.length);
    assert(records.every(row => row.split("\t").length === columns.length &&
      row.split("\t")[columns.indexOf("HGVSp")] === "value\\twith\\r\\ncontrols"));
    await page.locator('.cohort-result-actions ::-p-text(Select all shown individuals)').click();
    await page.locator(`.cohort-result-actions ::-p-text(${action})`).click();
  }

  for (const [action, expectedCount] of [["Review selected findings", 2], ["LOAD SELECTED INDIVIDUALS", 3]]) {
    await openSelection(action);
    await page.waitForSelector(".review-list-item.active");
    assert.equal(await page.$$eval(".review-list-item", items => items.length), expectedCount);
    assert.deepEqual(await page.$$eval(".transcript-table-scroll tbody tr", rows =>
      rows.map(row => row.cells[1].textContent)), ["ENST_SELECT", "ENST_PLUS"]);
    assert.match(await page.$eval(".review-detail", el => el.textContent), /ENST00000263100/,
      "initial representative carries the bundled A1BG constraint transcript");

    await page.locator(".review-toolbar .back-button").click();
    const resultRows = ".table-frame tbody tr:has(.impact-pill)";
    const waitCount = n => page.waitForFunction((selector, n) =>
      document.querySelectorAll(selector).length === n, {}, resultRows, n);
    await waitCount(expectedCount);
    for (const metric of ["AlphaMissense", "SpliceAI max"]) {
      await page.locator(".score-filters > button").click();
      await page.locator(`.score-metric-list ::-p-text(${metric})`).click();
      assert.match(await page.$eval(".score-rule small", el => el.textContent), /^0 of \d+ review rows have a value$/);
      const operator = `select[aria-label="${metric} operator"]`;
      await page.select(operator, "present");
      await waitCount(0);
      await page.select(operator, "missing");
      await waitCount(expectedCount);
      await page.locator(`button[aria-label="Remove ${metric} filter"]`).click();
    }
  }
  assert.deepEqual(handoffs, [{ selections: carriers.map(({ sample_entry_id, variant_key }) => ({
    sample_entry_id, variant_key })) }, { sample_ids: [1, 2] }]);

  clinical = false;
  await openSelection("Review selected findings");
  await page.locator('.empty-state ::-p-text(No variants match these filters)').wait();
  assert.equal(await page.$(".review-detail"), null);
  assert.match(await page.$eval(".content-header .subtitle", el => el.textContent), /^0 transcript-level rows/);
  console.log("PASS: both cohort handoffs preserve declared empty metrics and open the prepared review list");
} finally {
  await browser.close();
}
