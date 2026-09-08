import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("is configured as a loopback-only local application", async () => {
  const packageJson = JSON.parse(await readFile(new URL("package.json", root), "utf8"));
  assert.match(packageJson.scripts.dev, /next dev --hostname 127\.0\.0\.1/);
  assert.match(packageJson.scripts.start, /next start --hostname 127\.0\.0\.1/);
  await assert.rejects(readFile(new URL(".openai/hosting.json", root), "utf8"));
});

test("keeps the clinical review defaults visible", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  assert.match(source, /useState<VariantRow\[\]>\(\[\]\)/);
  assert.doesNotMatch(source, /demoVariants/);
  assert.match(source, /Clinical transcripts \(MANE \+ PICK fallback\)/);
  assert.doesNotMatch(source, /setManeOnly\(false\)/);
  assert.match(source, /excludeConfirmedFrameRestored, setExcludeConfirmedFrameRestored\] = useState\(true\)/);
  assert.match(source, /Hidden by default; possible\/unphased events always remain visible/);
  assert.match(source, /Exclude RepeatMasker/);
  assert.match(source, /Exclude SegDup/);
  assert.match(source, /ClinVar P \/ LP only/);
  assert.match(source, /Candidate compound het/);
  assert.match(source, /<th>ClinVar<\/th><th>GenIA<\/th><th>Flags<\/th>/);
  assert.doesNotMatch(source, /<th>SpliceAI<\/th>/);
  assert.match(source, /GENIA_LIST_CLASS_ORDER = \["P", "LP", "VUS", "LB", "B", "NC", "RF"\]/);
  assert.doesNotMatch(source, /hasVisibleFuncVepScore\(row, visibleInfo\)/);
  assert.match(source, /current-review-table/);
  assert.match(source, /<VariantIdentifier row=\{row\}\/>/);
  assert.match(source, /Research use only\. GUIDE-IEI organizes evidence but does not classify variants or generate diagnostic reports/);
  assert.match(source, /className="research-use-label">Research use only<\/span>/);
  assert.doesNotMatch(source, /Patient data processed locally/);
  assert.doesNotMatch(source, /className="secondary-button glossary-button"/);
  assert.match(source, /confirmResearchUseExport/);
  assert.match(source, /FilterSection title="Clinical database"/);
  assert.match(source, /FilterSection title="Prediction scores"/);
  assert.match(source, /const funcVepExactMatch = funcVepObservation\?\.matchStatus === "exact";/);
  assert.doesNotMatch(source, /matchStatus === "exact" \|\| hasAnyScore/);
  assert.match(source, /matchReason === "query_target_unavailable"/);
  assert.match(source, /The VCF did not provide an Ensembl gene target/);
  assert.match(source, /observation\.target\?\.ensembl_gene \|\| ""\)\.split\("&"\)/);
});

test("uses exact gnomAD popmax presets and accepts a zero custom threshold", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  assert.match(source, /const POPMAX_PRESETS = \[0\.0001, 0\.001, 0\.01\]/);
  assert.match(source, /useState<number \| null>\(0\.01\)/);
  assert.match(source, />No limit<\/button>/);
  assert.match(source, /0, 0\.00001, or 1e-5/);
  assert.match(source, /value < 0 \|\| value > 1/);
  assert.match(source, /popmax !== null && row\.gnomadPopmax !== null/);
  assert.match(source, /setPopmax\(null\)/);
  assert.doesNotMatch(source, /type="range" min="0" max="0\.05"/);
  assert.doesNotMatch(source, /setPopmax\(1\)/);
});

test("provides separate local annotation and annotated-VCF review paths", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(source, /useState<View>\("import"\)/);
  assert.match(source, /pendingFiles\.length \? "review" : "annotate"/);
  assert.match(source, /Annotate VCF/);
  assert.match(source, /Review annotated VCF/);
  assert.match(source, /Drop \.vcf or \.vcf\.gz files here/);
  assert.match(source, /Compressed VCFs/);
  assert.match(source, /const VCF_FILE_ACCEPT = "[^"]*\.gz[^"]*application\/gzip/);
  assert.match(source, /accept=\{VCF_FILE_ACCEPT\}/);
  assert.match(source, /Annotation datasets/);
  assert.match(source, /Exome region only/);
  assert.match(source, /Whole genome/);
  assert.match(source, /Local indexed intake with candidate prefiltering/);
  assert.match(source, /Patient VCF, phenotype, and analysis data are processed locally/);
  assert.match(source, /analysisScope === "whole_genome"/);
  assert.match(source, /function annotationSourceIsLocked/);
  assert.match(source, /source\.required \|\| source\.setup_mode === "bundled" \|\| source\.id === "dbnsfp"/);
  assert.match(source, /return source\.installed \|\| source\.enabled/);
  assert.doesNotMatch(source, /DEFAULT_WGS_ANNOTATION_SOURCES/);
  assert.equal(source.match(/annotationSourceIsEnabled\(source, analysisScope, sourceEnabled\)/g)?.length, 2);
  assert.match(source, /installed predictors on by default/);
  assert.match(source, /Included in every run/);
  assert.match(source, /Use in this run/);
  assert.match(source, /Install to enable/);
  assert.doesNotMatch(source, /className="dataset-enable"/);
  assert.match(source, /Indexing and prefiltering WGS/);
  assert.match(source, /Whole-genome indexing and prefiltering progress/);
  assert.match(source, /PASS-or-unfiltered\/QC AND \(popmax ≤ threshold OR popmax unavailable\)/);
  assert.match(source, /wgsImportJob\.records_scanned/);
  assert.match(await readFile(new URL("app/vcf.ts", root), "utf8"), /async function\* fileLines/);
  assert.doesNotMatch(await readFile(new URL("app/vcf.ts", root), "utf8"), /decoded\.join/);
  assert.match(source, /gnomAD popmax ≤/);
  assert.match(source, /\|promoterAI\| ≥/);
  assert.match(source, /ENCODE cCRE regions/);
  assert.match(source, /All noncoding regions/);
  assert.match(source, /No additional noncoding regions/);
  assert.doesNotMatch(source, /leaving CADD blank disables CADD/);
  assert.match(source, /PASS records only/);
  assert.match(source, /not read yet/);
  assert.match(source, /Import and review variants/);
  assert.match(source, /onImportFiles\(pendingFiles, analysisScope, submittedWgsFilters, activeWgsPaths, \{ keep:/);
  assert.match(source, /Recommended for very large files: use existing workstation paths/);
  assert.match(source, /Input genome build/);
  assert.match(source, /useState<"GRCh38" \| "GRCh37" \| "auto">\("auto"\)/);
  assert.match(source, /Detected .* logical CPU threads/);
  assert.match(source, /Use automatic/);
  // Reference datasets are never updated from the review UI (the old
  // /api/references/update surface stays gone); the SOFTWARE updater in
  // the About panel is a deliberate feature and must be present.
  assert.doesNotMatch(service, /\/api\/references\/update/);
  assert.match(source, /checkForSoftwareUpdate/);
  assert.match(source, /rollbackSoftwareUpdate/);
  assert.doesNotMatch(source, /Annotation config/);
  assert.match(service, /annotation_options/);
  assert.match(service, /annotation-files\/stage/);
  assert.match(service, /\/api\/wgs-review/);
  assert.match(service, /getWgsReviewJob/);
  assert.match(service, /http:\/\/127\.0\.0\.1:43117/);
});

test("provides a persistent Sample Library, stable identity mapping, and storage controls", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(source, />Sample library</);
  assert.match(source, /Keep in Sample Library/);
  assert.match(source, /Review once/);
  assert.match(source, /Include qualifying variants in Cohort Search/);
  assert.doesNotMatch(source, /Optional sequencing metadata/);
  assert.doesNotMatch(source, /Capture kit \/ assay/);
  assert.doesNotMatch(source, /Target BED path/);
  assert.match(source, /Who does .* belong to/);
  assert.match(source, /Link existing individual/);
  assert.match(source, /Phenotype unavailable for now/);
  assert.match(source, /Included in Cohort Search/);
  assert.match(source, /Add to Cohort Search/);
  assert.match(source, /Repair Cohort Search/);
  assert.match(source, /Checking whether these samples are already in the Sample Library/);
  assert.match(source, /Possible dataset update/);
  assert.match(source, /Replace current version/);
  assert.match(source, /Keep as separate dataset/);
  assert.match(source, /Previous versions/);
  assert.match(source, /Stored once for all/);
  assert.match(service, /sample-library\/inspect/);
  assert.match(service, /activate-version/);
  assert.match(source, /More actions/);
  assert.match(source, /Rebuild search index/);
  assert.match(source, /Remove from Cohort Search/);
  assert.doesNotMatch(source, /Refresh cohort index/);
  assert.match(service, /cohort_index_status/);
  assert.match(service, /cohort\/remove/);
  assert.match(source, /setView\("variants"\); setSelected\(null\)/);
  assert.match(source, /Build full WGS index/);
  assert.match(source, /Add to Cohort Search/);
  assert.match(source, /Assay and import profile filters/);
  assert.match(source, /not interpreted as negative|not evidence that the individual lacks a variant/);
  assert.match(source, />Storage</);
  assert.match(source, /Where data live/);
  assert.match(source, /Annotation datasets/);
  assert.match(source, /Sample Library & Cohort/);
  assert.match(source, /Temporary workspace/);
  assert.match(source, /Use for future data/);
  assert.match(source, /Copy existing data/);
  assert.match(source, /Folder check passed/);
  assert.match(source, /Original locations retained/);
  assert.match(source, /Clean temporary data/);
  assert.match(source, /Compact database/);
  assert.match(service, /\/api\/sample-library/);
  assert.match(service, /\/api\/storage/);
  assert.match(service, /\/api\/storage\/migrate/);
  assert.match(service, /getStorageConfiguration/);
  assert.doesNotMatch(source, /cohort allele frequency/i);
});

test("provides annotation dataset setup and constrained local downloads", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(source, /Set up annotation datasets/);
  assert.match(source, /Recommended for exome/);
  assert.match(source, /Retry exome setup/);
  assert.match(source, /Files that completed successfully are preserved/);
  assert.match(source, /Recommended for WGS/);
  assert.match(source, /Update installed datasets/);
  assert.match(source, /Needs registration, a license, or private files/);
  assert.match(source, /Installed with the one-click setup/);
  assert.match(source, /Optional add-ons/);
  assert.match(source, /Download bundled files/);
  assert.match(source, /<div className="dataset-grid">{datasetCards\(optionalSources\)}<OmimDatasetSetupCard downloadJob={latestResourceJobs\.get\("omim"\)}/);
  assert.equal((source.match(/<OmimDatasetSetupCard /g) ?? []).length, 1);
  assert.match(source, /Paste the complete OMIM email or four-link block/);
  assert.match(source, /Direct OMIM links and Outlook Safe Links are accepted/);
  assert.match(source, /Download and install OMIM/);
  assert.match(source, /Already downloaded the four files/);
  assert.match(source, /Registration and setup instructions/);
  assert.match(source, /Paste the private dbNSFP GRCh38 \(\.gz\) download link/);
  assert.match(source, /must end in _grch38\.gz — not _grch37\.gz/);
  assert.match(source, /accepts any dbNSFP release version/);
  assert.match(source, /source\.id !== "dbnsfp" && !\["funcvep", "logofunc", "clingen_erepo"\]\.includes\(source\.id\) && source\.version/);
  assert.match(source, /source\.id === "clingen_erepo" && source\.version && <span>Updated/);
  assert.match(source, /Download and install dbNSFP/);
  assert.match(source, /derives the \.tbi and \.md5 links/);
  assert.match(source, /Choose folder/);
  assert.match(service, /\/api\/local-resource-source\/choose/);
  assert.match(service, /chooseLocalResourceSource/);
  assert.match(source, /Download \/ resume/);
  assert.match(source, /Download \/ resume 83 GiB/);
  assert.match(service, /"cadd_wgs"/);
  assert.match(source, /Download latest/);
  assert.match(source, /Configured location/);
  assert.match(source, /WGS public core installed/);
  assert.match(source, /<summary>Dataset details<\/summary>/);
  assert.match(source, /downloadJob\?\.progress/);
  assert.match(source, /resourceProgressMessage\(quickSetupJob/);
  assert.match(source, /resourceProgressMessage\(downloadJob/);
  assert.match(source, /aria-live="polite"/);
  assert.match(service, /\/api\/resource-downloads/);
  assert.match(service, /startResourceDownload/);
  assert.match(service, /\/api\/resource-preparations\/dbnsfp/);
  assert.match(service, /startDbnsfpDownload/);
  assert.match(source, /Prepare and install/);
  assert.match(source, /Choose the licensed PromoterAI source folder/);
  assert.match(service, /\/api\/resource-preparations\/promoterai/);
  assert.match(service, /startPromoterAiPreparation/);
  assert.match(source, /PromoterAI \|score\| ≥/);
  assert.match(source, /Math\.abs\(row\.promoterAI\) < promoterAbsMin/);
  assert.match(source, /Choose the downloaded LoGoFunc file/);
  assert.match(source, /Import LoGoFunc/);
  assert.match(source, /Download from Zenodo/);
  assert.match(service, /\/api\/resource-preparations\/logofunc/);
  assert.match(service, /startLoGoFuncPreparation/);
  assert.match(source, /LoGoFunc predicted class/);
  assert.match(source, /source\.recommendation === "optional"/);
  assert.match(source, /source\.id === "funcvep" \? funcVepSourcePath/);
  assert.match(source, /Automatic Zenodo download/);
  assert.match(source, /Use automatic Zenodo download instead/);
  assert.match(source, /Download and install FuncVEP/);
  assert.match(source, /resumably downloads the pinned 4\.24 GB ZIP from official Zenodo storage/);
  assert.match(source, /polyformproject\.org\/licenses\/strict\/1\.0\.0/);
  assert.match(source, /License and download/);
  assert.match(source, /Read upstream license/);
  assert.match(source, /Open official download page/);
  assert.match(source, /I have reviewed the upstream terms and confirm that my intended use of FuncVEP is permitted/);
  assert.match(source, /After acknowledgement, GUIDE-IEI can download the archive directly and process it on this computer/);
  assert.match(
    await readFile(new URL("app/globals.css", root), "utf8"),
    /\.dataset-preparation \.dataset-local-processing \{ margin: 0 0 7px; \}/,
  );
  assert.match(source, /source\.prepare_id !== "funcvep" && !preparationPath\.trim\(\)/);
  assert.match(source, /source\.prepare_id === "funcvep" && !licenseAccepted/);
  assert.match(source, /licenseAccepted=\{source\.id === "funcvep" \? funcVepLicenseAccepted : false\}/);
  assert.match(source, /!\["funcvep", "logofunc", "clingen_erepo"\]\.includes\(source\.id\)/);
  assert.match(source, /href="https:\/\/zenodo\.org\/records\/20595206"/);
  assert.match(source, /href="https:\/\/omim\.org\/"[^>]*>OMIM website ↗/);
  assert.match(source, /source\.reference_url && <a href=\{source\.reference_url\}/);
  assert.match(service, /\/api\/resource-preparations\/funcvep/);
  assert.match(service, /startFuncVepPreparation/);
  assert.match(service, /license_accepted: licenseAccepted/);
});

test("supports explicit subset repair for an unreadable GenIA index", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(source, /Add or update GenIA files/);
  assert.match(source, /Replace the unreadable derived GenIA index/);
  assert.match(source, /Only the selected components will remain\. Any unselected components from the prior index will be removed/);
  assert.match(source, /Boolean\(status\?\.error && !replaceUnreadable\)/);
  assert.match(source, /replacingUnreadableIndex/);
  assert.doesNotMatch(source, /Select all five GenIA exports for a complete replacement/);
  assert.doesNotMatch(source, /I confirm that I am authorized to process these GenIA exports locally/);
  assert.doesNotMatch(source, /No GenIA download link, credentials, or source data are bundled or uploaded/);
  assert.match(source, /Some GenIA variants were not indexed/);
  assert.doesNotMatch(source, /Installed with validation notes/);
  assert.match(source, /Needs registration, a license, or private files/);
  assert.match(source, /datasetCards\(accessRequiredSources\)\}\{geniaSource && <GeniaDatasetSetupCard/);
  assert.doesNotMatch(source, /datasetCards\(optionalSources\).*geniaSource && <GeniaDatasetSetupCard/);
  assert.match(source, /Set up annotation datasets → User action needed/);
  assert.match(source, /https:\/\/doi\.org\/10\.1016\/j\.jaci\.2023\.11\.022/);
  assert.match(service, /replaceUnreadable = false/);
  assert.match(service, /replace_unreadable: replaceUnreadable/);
});

test("filters Variant Review by ClinGen and GenIA pathogenic assertions and GEI genes", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(source, /ClinGen P \/ LP only/);
  assert.match(source, /GenIA P \/ LP only/);
  assert.match(source, /P \/ LP same protein change · any clinical source/);
  assert.match(source, /P \/ LP different missense at same residue · any clinical source/);
  assert.match(source, /hasClinicalProteinMatch\(row, "change"\)/);
  assert.match(source, /hasClinicalProteinMatch\(row, "residue"\)/);
  assert.match(source, /hasClinGenPathogenicEvidence\(row\.clingenErepo\)/);
  assert.match(source, /hasGeniaPathogenicEvidence\(row\.genia\)/);
  assert.match(source, /GenIA GEI gene/);
  assert.match(source, /geneKnowledgeFilters\?\.genia_gei_genes/);
  assert.match(service, /genia_gei_genes: string\[\]/);
  assert.doesNotMatch(source, /GenIA-associated gene/);
});

test("builds compound-het candidates only from rows surviving active filters", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  assert.match(source, /candidateCompoundHetKeys\(screenFilteredRows\)/);
  assert.doesNotMatch(source, /candidateCompoundHetKeys\(rows\)/);
});

test("keeps SCREEN reference context separate and exposes defensible activity filtering", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const regulatory = await readFile(new URL("app/regulatory-evidence.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(source, /Regulatory evidence/);
  assert.match(source, /RegulatoryFilterControl/);
  assert.match(source, /mode: "any"/);
  assert.match(regulatory, /Detected in SCREEN reference epigenomic data/);
  assert.match(regulatory, /Element–gene links · to be developed/);
  assert.match(regulatory, /Gene-specific variant-effect prediction · to be developed/);
  assert.doesNotMatch(regulatory, /Gene links · not installed/);
  assert.match(source, /reviewAnalysisScope === "whole_genome"/);
  assert.match(source, /analysisScope === "whole_genome" && reviewSection === "regulatory"/);
  assert.match(source, /analysisScope === "whole_genome" && <RegulatorySummary/);
  // The raw source-annotation panel was removed at the user's request; the
  // old ordering assertion passed vacuously once its string vanished
  // (indexOf() === -1 compares less-than anything). Assert the removal.
  assert.equal(source.includes("RawVcfEvidencePanel"), false);
  assert.match(regulatory, /item\.state_label/);
  assert.match(regulatory, /classification unavailable/);
  assert.match(regulatory, /Classifier assays/);
  assert.match(regulatory, /Require SCREEN regulatory activity/);
  assert.match(regulatory, />Immune context</);
  assert.match(regulatory, /any selected SCREEN immune-related tissue aggregate or curated immune-cell context/);
  assert.match(regulatory, /setActiveSetId\(immuneAll\.id\)/);
  assert.match(regulatory, /Other context filters/);
  assert.match(regulatory, /regulatory activity is detected in any selected SCREEN tissue or cell context/);
  assert.doesNotMatch(regulatory, /Selected contexts must match/);
  assert.doesNotMatch(regulatory, /<option value="all">ALL/);
  assert.match(regulatory, /Manage named context sets/);
  assert.match(regulatory, /Display context set/);
  assert.match(regulatory, /assign a target gene/);
  assert.match(regulatory, /Why are both coding and regulatory annotations shown/);
  assert.match(regulatory, /same gene, another gene, or multiple genes/);
  assert.match(regulatory, /Use prepared bundle/);
  assert.match(regulatory, /stores only a local pointer/);
  assert.match(service, /\/api\/screen-context\/catalog/);
  assert.match(service, /\/api\/screen-context\/install/);
  assert.match(service, /\/api\/screen-context\/filter/);
});

test("provides a full variant review workspace with configurable evidence", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const styles = await readFile(new URL("app/globals.css", root), "utf8");
  assert.match(source, /review-workspace/);
  assert.match(source, /Information shown/);
  assert.match(source, /AlphaMissense/);
  assert.match(source, /CADD phred/);
  assert.match(source, /SpliceAI max/);
  assert.match(source, /promoterAI/);
  assert.match(source, /"loGoFunc", "funcVepCti", "loftee"/);
  assert.doesNotMatch(source, /"funcVepCti", "funcVepCte", "funcVepSp", "loftee"/);
  assert.match(source, /CTI — default/);
  assert.match(source, /CTE — optional comparison/);
  assert.match(source, /SP — optional comparison/);
  assert.match(source, /label: `FuncVEP \$\{model\.label\}`/);
  assert.match(source, /predictorBinaryClassification\("funcvep", model\.metricId, score\)/);
  assert.match(source, /classification\?\.label/);
  assert.match(source, /classification\?\.isPositive/);
  assert.match(source, /Functional-effect prediction, not a clinical pathogenicity classification/);
  assert.match(source, /const allPredictorCards = \[\.\.\.predictorCards, \.\.\.funcVepPredictorCards, \.\.\.additionalPredictorCards\]/);
  assert.match(source, /allPredictorCards\.map/);
  assert.doesNotMatch(source, /Functional evidence model/);
  assert.doesNotMatch(source, /<FuncVepEvidence/);
  assert.match(source, /item === "funcVep" \? \["funcVepCti"\]/);
  assert.match(source, /const DISPLAY_STORAGE_KEY = "iei-review-visible-evidence-v3"/);
  assert.match(source, /const PREVIOUS_DISPLAY_STORAGE_KEY = "iei-review-visible-evidence-v2"/);
  assert.match(source, /item !== "funcVepCte" && item !== "funcVepSp"/);
  assert.match(source, /if \(!migrated\.includes\("funcVepCti"\)\) migrated\.push\("funcVepCti"\)/);
  assert.doesNotMatch(source, /Exact allele \+ Ensembl gene\. CTI includes features/);
  assert.doesNotMatch(source, /Exact allele \+ Ensembl gene\. CTE excludes clinically trained predictors/);
  assert.match(source, /No score for the selected MANE transcript/);
  assert.match(source, /selectedManeAlphaMissenseMissing = selected\.mane/);
  assert.match(source, /noteWhenMissing: selectedManeAlphaMissenseMissing/);
  assert.doesNotMatch(source, /AlphaMissense scores are available on/);
  assert.doesNotMatch(source, /predictor-transcript-alternatives/);
  assert.match(source, /const restorationIdentity = row\.carriers/);
  assert.match(source, /Any indexed carrier can retrieve the same complete source/);
  assert.match(source, /attachLibraryIdentity\(row, identityBySample\)/);
  assert.doesNotMatch(styles, /\.predictor-transcript-alternatives/);
  assert.match(source, /CADD raw/);
  assert.match(source, /GERP\+\+ RS/);
  assert.match(source, /phyloP 100-way/);
  assert.match(source, /phastCons 100-way/);
  assert.match(source, /All gnomAD population frequencies/);
  assert.match(source, /Loaded automatically from bundled gnomAD/);
  assert.match(source, /Reference-disrupted transcript/);
  assert.match(source, /reference transcript CDS is already disrupted/);
  assert.match(source, /not a conventional pLoF baseline/);
  assert.match(source, /Splicing evidence/);
  assert.match(source, /isHighImpactSpliceVariant/);
  assert.match(source, /start-loss is outside LOFTEE scope/);
  assert.match(source, /No PVS1 conclusion is assigned here/);
  assert.match(source, /loftee-detail-line"><span>LOFTEE<\/span>/);
  assert.doesNotMatch(source, /Classifies predicted loss-of-function consequences as high or low confidence/);
  assert.doesNotMatch(source, /Highlighted values cross a model-specific GUIDE review threshold/);
  assert.match(source, /label=\{`\$\{source\} P\/LP report with the same protein change`\}/);
  assert.match(source, /label=\{`\$\{source\} P\/LP missense report at the same residue`\}/);
  assert.match(source, /source="ClinGen" evidence=\{proteinMatch\}/);
  assert.match(source, /source="GenIA" evidence=\{proteinMatch\}/);
  assert.match(source, /proteinMatchDisplayStatus\(evidence, "change", selectedTranscript\)/);
  assert.match(source, /proteinMatchDisplayStatus\(evidence, "residue", selectedTranscript\)/);
  assert.match(source, /Candidate PS1\/PM5 evidence only/);
  assert.match(source, /Not applicable · single-exon transcript/);
  assert.match(source, /no downstream exon–exon junction/);
  assert.match(source, /ENCODE SCREEN cCRE/);
  assert.match(source, /Does not overlap a/);
  assert.match(source, /proximity is not a target-gene assignment/);
  assert.match(source, /Every gene below is listed only because its gene-level TSS lies within/);
  assert.match(source, /simultaneously have a transcript-specific coding consequence and be a regulatory element/);
  assert.match(source, /same gene, another gene, or multiple genes/);
  assert.match(source, /Include non-protein-coding genes/);
  assert.match(source, /Protein-coding genes are shown by default/);
  assert.match(source, /shown ·.*total.*protein-coding.*other/);
  assert.match(source, /Distance from cCRE to TSS/);
  assert.match(await readFile(new URL("app/local-service.ts", root), "utf8"), /\/api\/ccre-context/);
  assert.match(source, /loadBundledReferences/);
  assert.match(styles, /\.workspace\.review-mode/);
  assert.match(styles, /\.review-list/);
  assert.match(styles, /\.review-detail/);
  assert.match(styles, /\.review-detail \{[^}]*min-height: 0;[^}]*overflow-y: auto/);
  assert.match(styles, /\.review-workspace \{[^}]*min-height: 0;[^}]*overflow: hidden/);
  assert.match(source, /detailRef\.current\?\.scrollTo\(\{ top: 0 \}\)/);
});

test("uses editable gene sets and compact coordinate-based variant IDs", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  assert.match(source, /IEI haploinsufficiency/);
  assert.match(source, /type View = .*"gene_lists"/);
  assert.match(source, />Gene lists</);
  assert.match(source, /Custom gene lists/);
  assert.match(source, /Create list/);
  assert.match(source, /Upload list/);
  assert.match(source, /CUSTOM_GENE_LISTS_STORAGE_KEY/);
  assert.match(source, /customLists\.map/);
  assert.match(source, /Restore bundled list/);
  assert.match(source, /Changes remain on this workstation/);
  assert.match(source, /LoF constrained/);
  assert.match(source, /pLI ≥ 0\.9 or LOEUF/);
  assert.match(source, /`\$\{row\.chrom\}:\$\{row\.pos\}:\$\{row\.ref\}:\$\{row\.alt\}`/);
  assert.match(source, /compactAllele/);
  assert.doesNotMatch(source, /row\.pos\.toLocaleString\(\).*row\.ref/);
});

test("keeps source-specific gene knowledge local and reviewable", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(source, />Gene knowledge</);
  assert.match(source, /No IUIS category filter/);
  assert.doesNotMatch(source, /ClinGen gene–disease validity<\/label>/);
  assert.doesNotMatch(source, /ClinGen sufficient HI evidence/);
  assert.doesNotMatch(source, /ClinGen sufficient TS evidence/);
  assert.match(source, /OMIM dataset not installed/);
  assert.match(source, /Install them under Import &amp; QC → Set up annotation datasets → Optional add-ons/);
  assert.doesNotMatch(source, /Open OMIM setup/);
  assert.doesNotMatch(source, /Set up OMIM/);
  assert.doesNotMatch(source, /OMIM is gene-level knowledge rather than a VEP annotation dataset/);
  assert.match(source, /Gene evidence is not variant evidence/);
  assert.match(source, /Score 30 denotes a gene associated with an autosomal-recessive phenotype/);
  assert.doesNotMatch(source, /Inheritance and GOF\/DN are preserved per IUIS disease row/);
  assert.match(source, /Associated features/);
  assert.match(source, /Major category/);
  assert.match(source, /Subcategory/);
  assert.match(source, /Other affected cells/);
  assert.match(service, /\/api\/gene-knowledge\/status/);
  assert.match(service, /\/api\/gene-knowledge\/filters/);
  assert.match(service, /\/api\/gene-knowledge\/omim\/install/);
  assert.match(service, /\/api\/gene-knowledge\/omim\/download/);
});

test("provides persistent genotype-first cohort indexing and carrier search", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  const styles = await readFile(new URL("app/globals.css", root), "utf8");
  assert.match(source, /Genotype-first discovery/);
  assert.match(source, /Cohort membership/);
  assert.match(source, /Gene list/);
  assert.match(source, /Exact variant/);
  assert.match(source, /Qualifying variants in gene/);
  assert.match(source, /Find carriers/);
  assert.match(service, /\/api\/cohort\/import/);
  assert.match(service, /\/api\/cohort\/import-jobs/);
  assert.doesNotMatch(source, /startCohortImport|async function indexSources|const \[sourcePaths, setSourcePaths\]/);
  assert.match(source, /role="progressbar"/);
  assert.match(service, /\/api\/cohort\/query/);
  assert.match(service, /\/api\/cohort\/samples/);
  assert.match(service, /\/api\/cohort\/variant-detail/);
  assert.match(service, /\/api\/cohort\/review-records/);
  assert.match(service, /\/api\/cohort\/sample-review/);
  assert.match(source, /Manage cohort samples/);
  assert.match(source, /Remove selected/);
  assert.match(source, /unique variant/);
  assert.match(source, /Matched carriers/);
  assert.match(source, /Review selected findings/);
  assert.match(source, /LOAD SELECTED INDIVIDUALS/);
  assert.match(source, /REVIEW THIS VARIANT/);
  assert.match(source, /Complete stored review sets were loaded using each source's original cohort import profile/);
  assert.match(source, /Complete source records were restored from the indexed VCFs/);
  assert.match(source, /Variant details/);
  assert.match(source, /Compact WGS/);
  assert.match(service, /analysis_scope/);
  assert.match(styles, /\.workspace\.cohort-mode/);
  assert.match(styles, /\.cohort-table/);
  assert.match(styles, /\.cohort-variant-table/);
  assert.match(styles, /\.cohort-variant-detail/);
  assert.match(styles, /\.cohort-sample-manager/);
});

test("provides pedigree-aware de novo and compound-heterozygous review", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const parser = await readFile(new URL("app/vcf.ts", root), "utf8");
  const trio = await readFile(new URL("app/trio.ts", root), "utf8");
  const styles = await readFile(new URL("app/globals.css", root), "utf8");
  assert.match(source, /Family analysis/);
  assert.match(source, /Upload PED/);
  assert.match(source, /High-confidence de novo/);
  assert.match(source, /Parental relationships confirmed/);
  assert.match(source, /Both variants pass active filters/);
  assert.match(source, /Trio genotypes/);
  assert.match(parser, /sampleGenotypes/);
  assert.match(parser, /phaseHaplotype/);
  assert.match(trio, /confirmed_trans_inheritance/);
  assert.match(trio, /possible_parental_mosaicism/);
  assert.match(styles, /\.family-page/);
  assert.match(styles, /\.trio-table/);
});

test("provides local manual and mapped spreadsheet phenotype intake", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  const styles = await readFile(new URL("app/globals.css", root), "utf8");
  assert.match(source, /Demographics & phenotypes/);
  assert.match(source, /Manual entry/);
  assert.match(source, /Spreadsheet import/);
  assert.match(source, /Reported race/);
  assert.match(source, /Reported ethnicity/);
  assert.match(source, /No HPO inference or phenotype-based variant ranking/);
  assert.match(source, /CSV, TSV, or XLSX/);
  assert.match(source, /Preserve unmapped columns as custom fields/);
  assert.match(service, /\/api\/phenotypes\/preview/);
  assert.match(service, /\/api\/phenotypes\/validate/);
  assert.match(service, /\/api\/phenotypes\/import/);
  assert.match(styles, /\.workspace\.phenotype-mode/);
  assert.match(styles, /\.mapping-grid/);
});

test("links phenotype records from a dedicated variant-review tab", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  const service = await readFile(new URL("app/local-service.ts", root), "utf8");
  const styles = await readFile(new URL("app/globals.css", root), "utf8");
  assert.match(source, /reviewSection === "phenotype"/);
  assert.match(source, />Phenotype<\/button>/);
  assert.match(source, /PhenotypeReviewPanel/);
  assert.match(source, /getPhenotypesBySample\(sample\)/);
  assert.match(service, /normalizePhenotypeIndividual/);
  assert.match(service, /sample_ids: phenotypeStringList\(value\.sample_ids\)/);
  assert.match(source, /Manage phenotype data/);
  assert.match(source, /Present features/);
  assert.match(source, /Explicitly absent/);
  assert.doesNotMatch(source, /function PhenotypeSummary/);
  assert.match(styles, /\.phenotype-review-panel/);
});

test("bounds the number of variant rows rendered at once (audit H9)", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  // The results table renders a bounded slice with an explicit "show more"
  // control instead of every matching row, the row component is memoised,
  // and the search value is deferred so typing never re-filters synchronously.
  assert.match(source, /const TABLE_RENDER_STEP = 500;/);
  assert.match(source, /const visibleRows = rows\.length > renderLimit \? rows\.slice\(0, renderLimit\) : rows;/);
  assert.match(source, /Showing \{visibleRows\.length\.toLocaleString\(\)\} of \{rows\.length\.toLocaleString\(\)\} matching rows/);
  assert.match(source, /const VariantTableRow = memo\(function VariantTableRow/);
  assert.match(source, /const deferredQuery = useDeferredValue\(query\);/);
  assert.match(source, /const q = deferredQuery\.trim\(\)\.toLowerCase\(\);/);
  // The review side list shows a window around the selected variant.
  assert.match(source, /reviewListWindow\.rows\.map\(/);
  assert.doesNotMatch(source, /<tbody>\{rows\.map\(\(row\) => \{/);
});

test("explains what a prediction threshold does with a missing score (review M1)", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  // The prediction-score panel states the missing-score rule in place: an
  // enabled threshold excludes unscored variants, unlike the frequency filter.
  assert.match(source, /id="prediction-threshold-help"/);
  assert.match(source, /variants with no value for the predictor are excluded while the threshold is set/);
  assert.match(source, /the optional CADD dataset adds indels/);
  // The frequency help names the source preference (review M9) …
  assert.match(source, /otherwise VEP&apos;s MAX_AF \(highest AF across 1000 Genomes, ESP and gnomAD\)/);
  // … and the variant page labels the value by its source rather than
  // calling every fallback "gnomAD popmax".
  assert.match(source, /function frequencySourceLabel\(/);
  assert.match(source, /frequencySourceLabel\(selectedFrequencySource, true\)/);
  // Cohort Search says the single-copy X/Y widening out loud.
  assert.match(source, /Homozygous \(incl\. single-copy X\/Y\)/);
  assert.match(source, /Hemizygous \(incl\. single-copy X\/Y unless recorded female\)/);
  // The CcreContextPanel allele props are not named `ref` (review M36).
  assert.doesNotMatch(source, /<CcreContextPanel[^>]* ref=\{/);
  assert.match(source, /<CcreContextPanel[^>]* refAllele=\{/);
});

test("an update that changed the interface asks for a full relaunch, not an in-app restart (audit M21)", async () => {
  const source = await readFile(new URL("app/VariantWorkbench.tsx", root), "utf8");
  // The pending-restart banner hides the Restart button and explains the
  // close-and-relaunch when the service reports full_relaunch_required…
  assert.match(source, /status\.full_relaunch_required \? "This update changed the interface, which an in-app restart cannot apply: close the GUIDE-IEI launcher window/);
  assert.match(source, /\{!status\.full_relaunch_required && <button[^>]*onClick=\{\(\) => void restartNow\(\)\}/);
  // …and the install result does the same for web_build_required, which
  // used to get the Restart button like a service-only update.
  assert.match(source, /installResult\.web_build_required\s*\?\s*" The interface changed: close the launcher window/);
  assert.match(source, /!installResult\.dependencies_changed && !installResult\.web_build_required && !installResult\.container_changed && <button/);
  const types = await readFile(new URL("app/local-service.ts", root), "utf8");
  assert.match(types, /full_relaunch_required\?: boolean;/);
  assert.match(types, /web_build_required\?: boolean;/);
});
