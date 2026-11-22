// Run against the local web UI with UI_TEST_PORT set to its port. All API responses are synthetic.
import assert from "node:assert/strict";
import puppeteer from "puppeteer-core";

const origin = `http://127.0.0.1:${Number(process.env.UI_TEST_PORT || 3000)}`;
const sample = "SYNTHETIC_ONLY";
const datasets = [2, 1].map(version => ({
  id: `dataset-v${version}`, callset_id: "callset", version_id: `version-${version}`,
  version_number: version, is_current: version === 2, managed_size_bytes: 512,
  original_name: "review.vcf", profile_label: "Synthetic", analysis_scope: "exome",
  sample_id: "sample", sample_label: "Sample label", vcf_sample_name: sample,
  individual_id: null, cohort_sample_entry_id: null, include_in_cohort: false,
  imported_at: "2026-01-01T00:00:00Z",
}));

function reviewVcf(alternative) {
  const transcripts = ["G|missense_variant|MODERATE|GENE1|ENST_MANE|NM_TEST|1",
    "G|missense_variant|MODERATE|GENE2|ENST_SECOND|NM_SECOND|1"];
  if (alternative) transcripts.push(`G|missense_variant|MODERATE|GENE1|${alternative}||`);
  return [
    "##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
    ...(alternative ? [] : ["##IEI_WGS_PREFILTER=<Transcripts=MANEThenPICKThenOnePerGene>"]),
    '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Feature|MANE_SELECT|PICK">',
    `#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t${sample}`,
    `1\t100\t.\tA\tG\t99\tPASS\tCSQ=${transcripts.join(",")}\tGT:DP:GQ:AD\t0/1:40:99:20,20`, "",
  ].join("\n");
}

const browser = await puppeteer.launch({ headless: true,
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  const recordRequests = [];
  let nextResponse = "success";
  let releaseObsolete;
  let alternative;
  await page.setRequestInterception(true);
  page.on("request", request => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return url.origin === origin ? request.continue() : request.abort();
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
    const json = body => request.respond({ headers, contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() === "OPTIONS") return request.respond({ status: 204, headers });
    if (url.pathname === "/api/sample-library") return json({ datasets });
    if (datasets.some(d => url.pathname === `/api/sample-library/${d.id}/file`)) {
      return request.respond({ headers, contentType: "text/vcf", body: reviewVcf() });
    }
    if (url.pathname === "/api/sample-library/review-record") {
      const body = JSON.parse(request.postData());
      recordRequests.push(body);
      const dataset = datasets.find(d => d.id === body.dataset_id);
      if (nextResponse === "fail") {
        nextResponse = "success";
        return request.respond({ status: 503, headers, contentType: "application/json",
          body: JSON.stringify({ error: "temporary source failure" }) });
      }
      if (nextResponse === "delay") {
        nextResponse = "success";
        releaseObsolete = () => json({ sample, name: "source.vcf", variant_key: "1:100:A:G",
          vcf: reviewVcf("ENST_OBSOLETE") });
        return;
      }
      if (dataset) return json({ sample, name: "source.vcf", variant_key: "1:100:A:G",
        vcf: reviewVcf(alternative ?? `ENST_ALT_V${dataset.version_number}`) });
    }
    return request.respond({ status: 503, headers, contentType: "application/json", body: '{}' });
  });
  await page.goto(origin, { waitUntil: "networkidle2" });
  async function openLibrary(dataset) {
    await page.locator('.nav-item ::-p-text(Sample library)').click();
    if (dataset.is_current) {
      await page.locator('.library-callset-actions ::-p-text(Open review)').click();
    } else {
      await page.locator('.library-version-history > summary').click();
      await page.locator('.library-version ::-p-text(Open previous review)').click();
    }
  }
  async function selectGene(gene) {
    await page.locator('input[aria-label="Search by gene, HGVS, ID or locus"]').fill(gene);
    await page.locator(`.table-frame tbody tr ::-p-text(${gene})`).click();
  }
  async function assertTable(gene, expected) {
    await page.waitForFunction((gene, expected) =>
      document.querySelector(".review-gene-link")?.textContent === gene
      && JSON.stringify([...document.querySelectorAll(".transcript-table-scroll tbody tr")]
        .map(row => row.cells[1].textContent)) === JSON.stringify(expected), {}, gene, expected);
  }
  const back = () => page.locator(".review-toolbar .back-button").click();
  for (const dataset of datasets) {
    await openLibrary(dataset);
    await selectGene("GENE1");
    await assertTable("GENE1", ["ENST_MANE", "ENST_SECOND", `ENST_ALT_V${dataset.version_number}`]);
    await back();
  }
  assert.deepEqual(recordRequests, datasets.map(dataset => ({ dataset_id: dataset.id, variant_key: "1:100:A:G" })));
  await openLibrary(datasets[0]);
  nextResponse = "fail";
  await selectGene("GENE1");
  await page.waitForFunction(() => document.querySelector(".review-detail")?.textContent
    .includes("Complete transcript annotations could not be opened"));
  await back();

  nextResponse = "delay";
  const requested = page.waitForRequest(request => request.method() === "POST"
    && request.url().endsWith("/review-record"));
  await Promise.all([requested, selectGene("GENE1")]);
  await back();
  await selectGene("GENE2");
  await assertTable("GENE2", ["ENST_SECOND", "ENST_MANE", "ENST_ALT_V2"]);
  await page.locator(".save-candidate").click();
  const settled = page.waitForResponse(response => response.url().endsWith("/review-record"));
  await releaseObsolete();
  await (await settled).text();
  await assertTable("GENE2", ["ENST_SECOND", "ENST_MANE", "ENST_ALT_V2"]);
  await back();
  await selectGene("GENE1");
  await assertTable("GENE1", ["ENST_MANE", "ENST_SECOND", "ENST_ALT_V2"]);
  assert.equal(await page.$eval(".save-candidate", button => button.textContent), "Save transcript",
    "saving the second representative should preserve the first representative's distinct row key");
  assert.equal(recordRequests.length, 5, "both representatives should reuse the successful source record");
  await back();

  alternative = "ENST_REIMPORTED";
  await openLibrary(datasets[0]);
  await selectGene("GENE1");
  await assertTable("GENE1", ["ENST_MANE", "ENST_SECOND", alternative]);
  assert.equal(recordRequests.length, 6, "reopening the same dataset should fetch fresh evidence");
  console.log("PASS: current library-only and previous individual reviews restored their own full transcript tables; retry, representative switches and reimport passed");
} finally {
  await browser.close();
}
