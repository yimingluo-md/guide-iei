// Optional browser regression: build first; all API calls mocked, synthetic data only.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";
import ts from "typescript";

const root = new URL("../", import.meta.url);
const registry = await readFile(new URL("../../config/predictor-registry.json", import.meta.url), "utf8");
const parser = (await readFile(new URL("app/vcf.ts", root), "utf8")).replace(
  'import predictorRegistry from "../../config/predictor-registry.json";', `const predictorRegistry = ${registry};`);
const compiled = ts.transpileModule(parser, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { parseVcfFiles } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const fixture = await parseVcfFiles([new File([
  '##fileformat=VCFv4.2\n##reference=GRCh38\n##contig=<ID=1,length=248956422>\n',
  '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature|HGVSc|HGVSp|MANE_SELECT|PICK">\n',
  '##INFO=<ID=GPNm447_LLR,Number=A,Type=Float,Description="test">\n##INFO=<ID=GPNm447_ABSLLR,Number=A,Type=Float,Description="test">\n',
  '##INFO=<ID=GPNv100_LLR,Number=A,Type=Float,Description="test">\n##INFO=<ID=GPNv100_ABSLLR,Number=A,Type=Float,Description="test">\n',
  '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC_ONLY\n',
  '1\t100\t.\tA\tG\t99\tPASS\tGPNm447_LLR=0;GPNm447_ABSLLR=-0.5;GPNv100_LLR=-2;GPNv100_ABSLLR=0.25;CSQ=G|missense_variant|MODERATE|AJAP1|ENSG_TEST|ENST00000378191|ENST00000378191.5:c.829+12772C>T||NM_TEST|1\tGT:DP:GQ:AD\t0/1:40:99:20,20\n',
], "synthetic-screen.vcf")]);
const base = fixture.rows[0];
fixture.rows = Array.from({ length: 1501 }, (_, i) => ({ ...base, key: `synthetic:${i}`, pos: 100 + i,
  spliceAI: i === 1 ? 0.8 : null, alphaGenomeAviPhred: i === 0 ? 25 : 0 }));
fixture.rows[0].collapsedTranscriptRows = [{ ...base, key: "other", transcript: "ENST00000378190",
  hgvsC: "ENST00000378190.7:c.829+12772C>T", mane: false, maneSelect: false, picked: false }];
fixture.rows[1499].gene = "NFKB1";
fixture.rows[1500].impact = "MODIFIER";
// A compact review retains these rows even though the CSQ PICK column is empty.
const distalRecords = [
  [9001, "intergenic_variant", "", "", "", 40],
  [9002, "regulatory_region_variant", "", "RegulatoryFeature", "ENSR_TEST", 40],
  [9003, "intron_variant", "NEARBY", "Transcript", "ENST_TEST", 40],
  [9004, "regulatory_region_variant", "", "Transcript", "ENST_TEST", 40],
  [9005, "intergenic_variant", "", "", "", 1],
];
const distal = await parseVcfFiles([new File([[
  "##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
  '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL|Feature_type|Feature|MANE_SELECT|PICK">',
  "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC_ONLY",
  ...distalRecords.map(([pos, term, gene, type, feature, dp]) =>
    `1\t${pos}\t.\tA\tG\t99\tPASS\tCSQ=G|${term}|MODIFIER|${gene}|${type}|${feature}||\tGT:DP:GQ:AD\t0/1:${dp}:99:20,20`),
  "",
].join("\n")], "distal.vcf")], {clinicalTranscriptsOnly: true});
assert.equal(distal.rows.length, distalRecords.length);
assert(distal.rows.every(row => !row.mane && !row.picked));
distal.rows.find(row => row.pos === 9002).id = "SECOND-marker";
fixture.rows.push(...distal.rows);
const catalog = { available: true, tissues: [{ id: "tissue:0", name: "blood" }], immune_contexts: [],
  presets: ["immune-core", "immune-all"].map((id) => ({ id, name: id, tissue_ids: ["tissue:0"], immune_context_ids: [] })) };

const port = Number(process.env.UI_TEST_PORT || 45391);
const origin = `http://127.0.0.1:${port}`;
const server = process.env.UI_TEST_STATIC_ROOT
  ? spawn("python3", ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", process.env.UI_TEST_STATIC_ROOT], {stdio: "ignore"})
  : spawn(process.execPath, [fileURLToPath(new URL("node_modules/next/dist/bin/next", root)),
  "start", "--hostname", "127.0.0.1", "--port", String(port)], {
  cwd: fileURLToPath(root), env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" }, stdio: "ignore",
});
let browser;
let releaseSecond;
const secondBatch = new Promise((resolve) => { releaseSecond = resolve; });
try {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(origin)).ok) break; } catch { /* starting */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  browser = await puppeteer.launch({ headless: true,
    executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  await page.setRequestInterception(true);
  const testMapping = {chrom:"chr", pos:"pos", ref:"ref", alt:"alt", strand:"strand", gene:"", transcript:""};
  const columns = ["chr", "pos", "ref", "alt", "strand", ...Array.from({length:78}, (_,i) => "detail_" + i), "overall"];
  let imported;
  let batches = 0;
  let failScreen = false;
  let missingLinks = false;
  let targetRequests = 0;
  const targetPayloads = [];
  const geneLookups = [];
  let linkIndexVersion = "release-1";
  const distalScreenPositions = new Set();
  await page.evaluateOnNewDocument(() => localStorage.setItem("iei-review-custom-gene-lists-v2", JSON.stringify([
    {id: "only-second", name: "Only SECOND", genes: ["SECOND"]},
    {id: "two-targets", name: "Two targets", genes: ["SECOND", "DISTAL"]},
  ])));
  page.on("request", async (request) => {
    const url = new URL(request.url());
    if (!url.pathname.includes("/api/")) {
      return url.origin === origin ? request.continue() : request.abort();
    }
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
    if (request.method() === "OPTIONS") return request.respond({ status: 204, headers });
    const json = body => request.respond({status: 200, headers, contentType: "application/json", body: JSON.stringify(body)});
    if (url.pathname.startsWith("/api/gene-knowledge/gene/")) {
      const identifier = decodeURIComponent(url.pathname.split("/").pop());
      geneLookups.push(identifier);
      return json({identity: identifier === "alias" ? {symbol: "APPROVED"} : null,
        aliases: [], iuis: [], clingen_validity: [], omim: []});
    }
    if (url.pathname === "/api/local-resource-source/choose") return json({paths:["/mock/score.tsv"], names:["score.tsv"]});
    if (url.pathname === "/api/custom-scores/inspect") return json({columns, mapping: testMapping,
      examples:[["1","100","A","G","+", ...Array(78).fill("1"),"0"]]});
    if (url.pathname === "/api/resource-preparations/custom-scores") {
      imported = JSON.parse(request.postData());
      return json({id:"mock",resource_id:"custom_scores",status:"succeeded",message:"Import complete"});
    }
    if (url.pathname === "/api/custom-scores" || url.pathname === "/api/custom-scores/lookup") {
      return json(imported ? [{...imported, id:"mock", rows:1, matches:[{context:{strand:"+"}, unresolved:["strand"], values:["0"]}]}] : []);
    }
    if (url.pathname === "/api/element-gene-links/search-status") {
      if (missingLinks) return request.respond({status: 400, headers, contentType: "application/json",
        body: JSON.stringify({error: "Install ENCODE-rE2G in Annotation datasets first"})});
      const {gene} = JSON.parse(request.postData());
      return json({index:["encode_re2g", linkIndexVersion, "fixture"], known:["NFKB1", "SECOND", "DISTAL"].includes(gene)});
    }
    if (url.pathname === "/api/element-gene-links/filter") {
      targetRequests++;
      if (missingLinks) return request.respond({status: 400, headers, contentType: "application/json",
        body: JSON.stringify({error: "Install ENCODE-rE2G in Annotation datasets first"})});
      const {variants, genes} = JSON.parse(request.postData());
      targetPayloads.push({genes, positions: variants.map(v => v.pos)});
      const targets = Object.fromEntries(variants.filter(v => v.pos === 1600 || v.pos >= 9001)
        .map(v => [v.key, (v.pos === 1600 ? ["NFKB1", "SECOND"] : ["DISTAL"]).filter(g => genes.includes(g))])
        .filter(([, symbols]) => symbols.length));
      return request.respond({status: 200, headers, contentType: "application/json", body: JSON.stringify({matches: Object.keys(targets), targets})});
    }
    if (url.pathname === "/api/screen-context/catalog") return request.respond({ status: 200, headers, contentType: "application/json", body: JSON.stringify(catalog) });
    if (url.pathname === "/api/screen-context/filter") {
      if (failScreen) return request.respond({ status: 503, headers, contentType: "application/json", body: '{"error":"Synthetic SCREEN failure"}' });
      batches++;
      if (batches === 2) await secondBatch;
      const data = JSON.parse(request.postData());
      for (const variant of data.variants) if (variant.pos >= 9001) distalScreenPositions.add(variant.pos);
      return request.respond({ status: 200, headers, contentType: "application/json", body: JSON.stringify({ available: true,
        matching_keys: data.variants.slice(0, batches === 1 ? 2 : 1).map((item) => item.key) }) });
    }
    return request.respond({ status: 503, headers, contentType: "application/json", body: '{"error":"Synthetic test: unavailable"}' });
  });
  await page.goto(origin, { waitUntil: "networkidle2" });
  await page.locator(".nav-item ::-p-text(Gene knowledge)").click();
  const lookup = page.locator(".gene-lookup input");
  await lookup.fill("unresolved");
  await page.click(".gene-lookup button");
  const waitGene = name => page.waitForFunction(value =>
    document.querySelector(".gene-knowledge-review h2")?.textContent === value, {}, name);
  await waitGene("unresolved");
  await lookup.fill("");
  await page.type(".gene-lookup input", "alias");
  assert.equal(await page.$eval(".gene-knowledge-review h2", el => el.textContent), "unresolved");
  assert.deepEqual(geneLookups, ["unresolved"]);
  await page.keyboard.press("Enter");
  await waitGene("APPROVED");
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/gene-knowledge/gene/alias")),
    page.click(".gene-lookup button"),
  ]);
  await waitGene("APPROVED");
  assert.deepEqual(geneLookups, ["unresolved", "alias", "alias"]);
  console.log("PASS: Gene Knowledge requests only submitted identifiers, including after an unresolved search");
  await page.evaluate(() => [...document.querySelectorAll(".nav-item")].find(el => el.textContent.includes("Import & QC")).click());
  await page.click(".intake-mode-switch button");
  await page.click(".dataset-setup-entry button");
  await page.click(".custom-score-setup > summary");
  await page.click(".custom-score-setup fieldset button");
  await page.waitForSelector(".custom-column-list input");
  assert.equal(await page.$$eval(".custom-column-list input:checked", rows => rows.length), 0);
  await page.type(".custom-score-setup input[type=search]", "overall");
  await page.click(".custom-column-list input");
  const rename = await page.$(".custom-score-setup label:has(input[maxlength]):last-of-type input");
  await rename.click({clickCount:3});
  await rename.type("Example metric");
  await page.waitForFunction(() => document.querySelector(".custom-score-setup table")?.textContent.includes("Example metric"));
  await page.click(".custom-score-setup .primary-button");
  await page.waitForFunction(() => document.querySelector(".custom-score-setup")?.textContent.includes("Import complete"));
  assert.deepEqual(imported.scores, [{column:"overall",label:"Example metric"}]);
  assert.deepEqual(imported.mapping, testMapping);
  await page.goto(origin, {waitUntil: "networkidle2"});
  await page.evaluate((data) => {
    const node = document.querySelector(".app-shell");
    let fiber = node[Object.keys(node).find((key) => key.startsWith("__reactFiber$"))];
    while (fiber && typeof fiber.memoizedProps?.setRows !== "function") fiber = fiber.return;
    fiber.memoizedProps.setRows(data.rows);
    fiber.memoizedProps.setSummary(data.summary);
    fiber.memoizedProps.setReviewAnalysisScope("whole_genome");
    [...document.querySelectorAll(".nav-item")].find((item) => item.firstElementChild.textContent === "Variants").click();
  }, fixture);
  await page.waitForSelector(".table-frame tbody tr");
  await page.click(".table-frame tbody tr");
  const customCard = () => [...document.querySelectorAll(".predictor-card")].find(card => card.textContent.includes("Example metric"));
  await page.waitForFunction(customCard);
  assert.equal(await page.evaluate(() => [...document.querySelectorAll(".predictor-card")].find(card => card.textContent.includes("Example metric")).querySelector("strong").textContent), "0");
  await page.click(".review-toolbar .display-settings > button");
  await page.waitForSelector(".custom-score-settings input");
  await page.click(".custom-score-settings input");
  await page.waitForFunction(() => ![...document.querySelectorAll(".predictor-card")].some(card => card.textContent.includes("Example metric")));
  await page.click(".custom-score-settings input");
  await page.waitForFunction(customCard);
  await page.click(".review-toolbar .display-settings > button");
  await page.click(".review-toolbar .back-button");
  console.log("PASS: custom score selection, preview, import, review display, and immediate hide/show");
  const geneSearch = await page.$('input[aria-label="Search by gene, HGVS, ID or locus"]');
  const geneMode = (includeTargets) => page.click(".review-gene-mode button:" + (includeTargets ? "last-child" : "first-child"));
  const onlySecond = () => page.evaluate(() => [...document.querySelectorAll(".check-row")].find(el => el.textContent.startsWith("Only SECOND")).click());
  const waitCount = count => page.waitForFunction(n =>
    document.querySelector(".content-header .subtitle")?.textContent.startsWith(n + " transcript-level")
    && !document.body.textContent.includes("Checking predicted target genes"), {}, count);
  const setSearch = async value => {
    await geneSearch.click({clickCount: 3});
    await geneSearch.press("Backspace");
    if (value) await geneSearch.type(value);
  };
  await setSearch("nfk");
  await waitCount(1);
  assert.equal(await page.$eval(".table-frame tbody .gene", el => el.textContent), "NFKB1");
  await setSearch("NFKB1");
  await waitCount(1);
  await geneMode(true);
  await waitCount(2);
  assert(await page.evaluate(() => document.querySelector("tbody")?.textContent.includes("AJAP1Regulatory target: NFKB1")));
  const firstSearchRequests = targetRequests;
  assert.equal(firstSearchRequests, 4);
  await setSearch("banana");
  await waitCount(0);
  assert.equal(targetRequests, firstSearchRequests, "Unknown gene text must send no alleles");
  await setSearch("NFKB1");
  await waitCount(2);
  assert.equal(targetRequests, firstSearchRequests, "Revisiting this gene must reuse its completed search");
  await setSearch("banana");
  await waitCount(0);
  linkIndexVersion = "release-2";
  await setSearch("NFKB1");
  await waitCount(2);
  assert.equal(targetRequests, firstSearchRequests * 2, "A different index must invalidate cached results");
  const initialTargetRequests = targetRequests;
  await addMetric("AVI", "AlphaGenome AVI · Phred"); // blank rule
  await page.type('input[aria-label="AlphaGenome AVI · Phred value"]', "20");
  await waitCount(0);
  await page.click('button[aria-label="Remove AlphaGenome AVI · Phred filter"]');
  await waitCount(2);
  await new Promise(resolve => setTimeout(resolve, 250)); // target search has a 150 ms debounce
  assert.equal(targetRequests, initialTargetRequests);
  await setSearch("DISTAL");
  await waitCount(2);
  await page.waitForSelector('.table-frame tbody .variant-id[title="1:9001:A:G"]');
  assert.deepEqual(await page.$$eval(".table-frame tbody .variant-id", ids =>
    ids.map(id => id.textContent).sort()), ["1:9001:A:G", "1:9002:A:G"]);
  await setSearch("nfk");
  await waitCount(1); // Partial annotated symbol stays live in regulatory mode.
  assert.equal(await page.$eval(".table-frame tbody .gene", el => el.textContent), "NFKB1");
  await setSearch("second");
  await waitCount(2); // The marker ID matches locally, plus one exact linked target.
  const twoTargets = () => page.evaluate(() => [...document.querySelectorAll(".check-row")]
    .find(el => el.textContent.startsWith("Two targets")).click());
  const beforeList = targetPayloads.length;
  await twoTargets();
  await waitCount(2);
  await page.locator(".table-frame tbody ::-p-text(Regulatory target: DISTAL)").wait();
  const mixed = targetPayloads.slice(beforeList);
  const narrow = mixed.filter(batch => batch.genes.length === 1);
  assert(narrow.length > 1);
  assert(narrow.every(batch => batch.genes[0] === "SECOND" && !batch.positions.includes(9002)));
  assert.deepEqual(mixed.filter(batch => batch.genes.length !== 1),
    [{genes: ["DISTAL", "SECOND"], positions: [9002]}]);
  const beforeText = targetPayloads.length;
  await setSearch("1:9001:A:G");
  await waitCount(1);
  assert.deepEqual(targetPayloads.slice(beforeText), [{genes: ["DISTAL", "SECOND"], positions: [9001]}]);
  await twoTargets();
  await setSearch("NFKB1");
  await waitCount(2);
  await onlySecond();
  await waitCount(0); // Two different linked genes cannot jointly satisfy one gene search.
  await setSearch("");
  await waitCount(1);
  assert(await page.evaluate(() => document.querySelector("tbody")?.textContent.includes("Regulatory target: SECOND")));
  await onlySecond();
  await setSearch("NFKB1");
  await waitCount(2);
  await page.click(".impact-chip.modifier.selected");
  await waitCount(1);
  await geneMode(false);
  assert.equal(await page.$$eval(".impact-chip.selected", buttons => buttons.length), 2);
  await geneMode(true);
  assert.equal(await page.$$eval(".impact-chip.selected", buttons => buttons.length), 3);
  missingLinks = true;
  await page.click(".impact-chip.modifier");
  await page.waitForFunction(() => document.body.textContent.includes("Install ENCODE-rE2G in Annotation datasets"));
  assert.equal(await page.$$eval(".table-frame tbody tr", rows => rows.length), 1);
  missingLinks = false;
  await geneMode(false);
  await setSearch("");
  await waitCount(1500);
  const toggleScreen = () => page.evaluate(() => [...document.querySelectorAll(".check-row")]
    .find((label) => label.textContent.startsWith("Immune context")).click());
  await toggleScreen();
  await page.waitForFunction(() => document.body.textContent.includes("Screened 1,000 of 1,501"));
  assert(await page.evaluate(() => document.body.textContent.includes("results are incomplete")));
  assert.equal(await page.$$eval(".table-frame tbody tr", (rows) => rows.length), 2);
  assert(await page.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent === "Export TSV").disabled));
  assert(!(await page.evaluate(() => document.body.textContent.includes("No variants match these filters"))));
  await addMetric("AVI", "AlphaGenome AVI · Phred");
  await page.type('input[aria-label="AlphaGenome AVI · Phred value"]', "20");
  await page.waitForFunction(() => document.querySelectorAll(".table-frame tbody tr").length === 1);
  assert.equal(batches, 2, "A score edit must preserve the pending second SCREEN batch");
  await page.click('button[aria-label="Remove AlphaGenome AVI · Phred filter"]');
  releaseSecond();
  await page.waitForFunction(() => !document.body.textContent.includes("results are incomplete"));
  assert.equal(await page.$$eval(".table-frame tbody tr", (rows) => rows.length), 3);
  assert.equal(distalScreenPositions.size, 0, "Annotated genes retains its ordinary transcript filter");
  await geneMode(true);
  await page.waitForFunction(() => !document.body.textContent.includes("results are incomplete"));
  await waitCount(2);
  assert.deepEqual([...distalScreenPositions].sort(), [9001, 9002]);
  await geneMode(false);
  await toggleScreen();
  async function addMetric(search, label) {
    await page.click(".score-filters > button");
    await page.type('input[aria-label="Search predictor metrics"]', search);
    const matches = await page.$$eval(".score-metric-list button", buttons => buttons.map(button => button.textContent));
    assert(matches.includes(label));
    await page.evaluate(name => [...document.querySelectorAll(".score-metric-list button")]
      .find(button => button.textContent === name).click(), label);
    return matches;
  }
  await addMetric("AVI", "AlphaGenome AVI · Phred");
  await page.type('input[aria-label="AlphaGenome AVI · Phred value"]', "20");
  await waitCount(1);
  assert.deepEqual(await addMetric("spliceai", "SpliceAI max"), ["SpliceAI max"]);
  await page.select('select[aria-label="SpliceAI max operator"]', "present");
  await waitCount(0);
  await page.select('select[aria-label="SpliceAI max operator"]', "missing");
  await waitCount(1);
  await page.click('button[aria-label="Remove AlphaGenome AVI · Phred filter"]');
  await waitCount(1500);
  await page.click('button[aria-label="Remove SpliceAI max filter"]');
  await waitCount(1501);
  failScreen = true;
  await toggleScreen();
  await page.waitForFunction(() => document.body.textContent.includes("SCREEN filtering could not finish"));
  assert(!(await page.evaluate(() => document.body.textContent.includes("No variants match these filters"))));
  await toggleScreen();
  await page.click(".table-frame tbody tr");
  await page.waitForSelector(".transcript-table-scroll");
  assert(!(await page.evaluate(() => document.body.textContent.includes("Missing is not evidence that the variant is benign"))));
  await page.waitForFunction(() => [...document.querySelectorAll(".predictor-card")].some(
    card => card.textContent.includes("GPN-Star-M") && card.textContent.includes("Calibrated absLLR -0.5")));
  async function setReviewScope(scope) {
    await page.evaluate((value) => {
      const shell = document.querySelector(".app-shell");
      let fiber = shell[Object.keys(shell).find(key => key.startsWith("__reactFiber$"))];
      while (fiber && typeof fiber.memoizedProps?.setReviewAnalysisScope !== "function") fiber = fiber.return;
      fiber.memoizedProps.setReviewAnalysisScope(value);
    }, scope);
  }
  await setReviewScope("exome");
  await page.waitForFunction(() => {
    const cards = [...document.querySelectorAll(".predictor-card")];
    return cards.some(card => card.textContent.includes("GPN-Star-V") && card.textContent.includes("Calibrated absLLR 0.25"))
      && !cards.some(card => card.textContent.includes("GPN-Star-M"));
  });
  await setReviewScope("whole_genome");
  for (const width of [1440, 768]) {
    await page.setViewport({ width, height: 1000 });
    const layout = await page.$eval(".transcript-table-scroll", (el) => ({
      width: el.clientWidth, table: el.querySelector("table").scrollWidth,
      containerWidth: el.closest(".evidence-section").clientWidth,
      overflow: getComputedStyle(el).overflowX,
      geneWidth: el.querySelector("th").getBoundingClientRect().width,
    }));
    assert.equal(layout.overflow, "auto");
    assert(layout.geneWidth > 34, "Inherited star-column width");
    // The application has an existing desktop minimum width. The table must
    // stay inside its own section at every viewport, not widen that section.
    assert(layout.width <= layout.containerWidth);
  }
  await page.setViewport({ width: 1440, height: 1000 });
  await page.$eval(".alt-transcripts", (el) => el.scrollIntoView({ block: "center" }));
  await (await page.$(".alt-transcripts")).screenshot({ path: "/private/tmp/guide-iei-transcript-table.png" });
  console.log("PASS: regulatory search, SCREEN filters, combined predictor rules, SpliceAI max, exome GPN-Star-V and transcript layout");
} finally {
  releaseSecond();
  if (browser) await browser.close();
  server.kill("SIGTERM");
}
