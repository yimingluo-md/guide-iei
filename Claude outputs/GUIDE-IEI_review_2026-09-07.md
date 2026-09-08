# GUIDE-IEI — Comprehensive Project Review

**Date:** 2026-09-07  
**Reviewed state:** `main` at `9ced321` ("Fix GenIA tests in clean-clone CI", 2026-09-05) **plus the 53 uncommitted files** in the working tree (`+1047 / −312`, 15 untracked). Version 0.6.1.  
**Mode:** read-only. No file in the repository was modified. (One incidental note: an early `git status` left a stale `.git/index.lock` because the review session cannot delete files; it was removed with your permission and all later git reads used `GIT_OPTIONAL_LOCKS=0`.)

**Method.** The full source tree (255 text files, ~75k lines) was copied into an isolated workspace. All 47 Python suites, 6 shell suites, the Node UI suites, `tsc`, `eslint`, `shellcheck`, `bash -n`, and `check_documentation.py` were run. Six parallel focused reviews (shell orchestration/packaging; pipeline Python + Perl plugins; loopback HTTP service + updater; SQLite stores; Next.js UI; docs/config/tests/licensing) each read their area in full; every High finding below was then independently re-verified against the code, and several were confirmed empirically (query plans, phase classification, id reuse, signal disposition).

---

## 1. Executive summary

GUIDE-IEI is a large, unusually well-engineered project for a single-developer clinical-research tool. The core genomics is careful and mostly correct (the BGZF/FASTA reader, 50-bp rule recomputation, multi-allelic `ALLELE_NUM` discipline, half-call handling, sex-chromosome-aware trio logic were all verified); the loopback service takes DNS-rebinding and CSRF seriously; the updater, storage migration and dataset installers use atomic-publish patterns; secrets for licensed URLs never touch argv or logs; and there are ~510 Python and 103 UI tests that are behavioural rather than cosmetic. **All test suites pass** in a clean environment (the only failures are artefacts of files not copied to the review snapshot).

The problems are concentrated at the **seams** — between modules that each re-implement the same concept, between the shell layer and the container, between what documentation promises and what the tests actually assert — rather than inside individual algorithms. The findings that most deserve attention, in order:

1. **Wrong-dataset linkage in the Sample Library** — `cohort_files.id` is reused after deletion and the library keeps a dangling pointer, so a later dataset with the same VCF sample name can be resolved as the earlier one, and removing one can delete the other's cohort rows (§3, H1). This is a wrong-patient-data path.
2. **Full-WGS reindex double-counts co-resident samples** in Cohort Search, contradicting the docs (H2).
3. **GRCh37 intake has two independent failure modes**: rCRS mitochondrial records are lifted through the hg19 `NC_001807` chain — the plugin aborts when the header declares the MT length, and silently shifts coordinates when it does not (H3); and the script calls `hts bcftools +liftover`, which dispatches to whatever native `bcftools` is on PATH without checking for the plugin — while `setup_environment.sh --install` deliberately installs a native bcftools that does not ship it (H4).
4. **Exact-allele lookups are representation-sensitive** (ClinGen eRepo, cohort variant keys) while the GenIA path normalises — silent false negatives on padded multi-allelic alleles (H5).
5. **The service is never shut down gracefully under the real launcher** (no SIGTERM/SIGHUP handler) and orphans running VEP jobs; a second instance runs destructive recovery before discovering the port is taken (H6, H7).
6. **Every Cohort Search query full-scans `cohort_annotations` twice**; the docs claim the test suite asserts covering-index plans, but the test checks hand-written SQL, not `query()`'s (H8).
7. **UI scale**: the variant table renders every matching row unvirtualised on the main thread, as does the parser — a scaling risk in the "relax the filters" workflow, not a reproduced freeze under the (restrictive) defaults (H9).
8. **Safety framing**: two bullets in the README's "What GUIDE-IEI is not" list are missing the word "Not" (a wording defect in the scope statement, not a reversal of meaning), and the research-use notice never reaches any output file — VCF header, QC report, manifest, or TSV (H10).
9. **`curl | sudo sh` is auto-approved by `--yes`**, which the launcher passes on native Linux (H11).

Everything else is Medium/Low: duplicated semantics that have already drifted (promoterAI, genotype classes, missing-score handling), monolith modules (`workbench_service.py` 6.2k, `cohort_store.py` 6.6k, `VariantWorkbench.tsx` 5.5k lines), documentation sprawl (five overlapping dataset pages), and test-harness details that let a test silently drop out of CI.

**Overall assessment:** production-quality foundations with a handful of correctness defects that matter for clinical use, all fixable in bounded effort. Nothing found suggests the project needs architectural rework; it needs a consolidation pass on shared helpers and a few targeted fixes before the next release. **Release recommendation:** hold the next tag until H1, H2, H3, H4, H5, H6 and H7 are fixed — the passing test suites do not establish release readiness for those paths, because the relevant tests mock or simplify exactly the behaviour that is wrong.

### Independent verification of the High findings
A second, independent reviewer re-checked all eleven High findings against the current tree (including the uncommitted fixes) and ran isolated live reproductions. Outcome: **H1, H2, H5, H6, H7 confirmed as serious with live reproductions** (a real service process's annotation child survived SIGTERM and startup recovery; a second real service instance marked the first's running job interrupted and deleted its resource-secret file before failing with "Address already in use"; a ClinGen hit for a normalised allele and no hit for its padded equivalent). **H3, H4, H8, H11 confirmed with qualifications** that are now reflected in the text above (abort location inside the plugin and header-dependent silent shift for H3; conditional on the native install for H4; slowdown proportional to cohort size for H8). **H9 and H10 were judged overstated in their original wording** — the table-scale trigger and "declares itself both" — and have been rewritten; both remain worth fixing. One fix suggestion was corrected: `AUTOINCREMENT` alone does not survive `_reset_cohort_tables`' `DROP`/recreate.

---

## 2. Verification results

| Check | Result |
|---|---|
| Python unit suites (`python3 <file>` per CI, 47 files) | **47/47 exit 0**, ≈510 tests, 0 failures, 8 skips (all "no bcftools/bgzip on host"). NumPy present so SCREEN tests ran. |
| Shell suites (`test_dry_run`, `test_hts_helper`, `test_dataset_installer`, `test_download_dbnsfp`, `test_fetch_clinvar`, `test_macos_launcher`) | all pass |
| `npm test` (webui, 103 tests) | 101 pass, 1 skip (`IEI_REAL_WGS_REVIEW`), 1 fail — `reference-data.test.mjs` `ENOENT gnomad_v4.1.1_gene_constraint.tsv`: **snapshot artefact** (file is tracked in git and present on your disk) |
| `npx tsc --noEmit` | clean |
| `npm run lint` | 0 errors, **276 warnings** (REMEDIATION.md recorded a baseline of 210 — warnings do not gate, so the number drifts) |
| `shellcheck -S warning -x` on all 44 scripts | 3 warnings, none real (`SC2054` intentional comma; two unused variables) |
| `bash -n` on all scripts | clean |
| Bash 3.2 (macOS) portability audit | clean — no `declare -A`, `mapfile`, `${var,,}`, etc. |
| `scripts/check_documentation.py` | fails only on 9 image links — images exist on disk (snapshot artefact); `test_documentation.py` 6/6 |
| `export-glossary.mjs --check` | current (39 terms) |

Snapshot caveat: `docs/assets/img/*.png`, `webui/public/bundled-data/*.sqlite3|*.tsv`, and the `.app` shell shim were not copied to the review workspace; all were confirmed present and tracked in the real repository. Findings that depended only on their absence were discarded.

---

## 3. High-severity findings

Each item: location → what → why it matters → suggested fix.

### H1. `cohort_files.id` reuse + dangling library linkage → wrong-dataset resolution and cross-dataset deletion
- `local_service/cohort_store.py:2237` — `cohort_files(id INTEGER PRIMARY KEY, …)` (no `AUTOINCREMENT`; `_reset_cohort_tables` at `:3636-3665` also DROPs/recreates, restarting ids at 1).
- `local_service/sample_library.py:199` — `library_datasets.cohort_file_id INTEGER` with no FK; linkage resolved at `:1113-1125` by `(cohort_file_id, vcf_sample_name, profile_hash)`.
- `local_service/workbench_service.py:5924-5928` — `/api/cohort/samples/remove` calls `remove_samples` directly and never nulls `library_datasets.cohort_file_id`.
- **Reproduced:** import dataset A (sample `P1`) → id 1; remove via the sample manager → A is `needs_repair` with `cohort_file_id` still 1; import a *separate* dataset B with sample `P1` → gets id 1; A now resolves as `ready` pointing at **B's** `cohort_sample_entry_id`; `library.remove(A)` deletes **B's** cohort rows. `cohort_sample_entry_id` is what the UI uses to open an individual's stored review set.
- **Fix:** `AUTOINCREMENT` is **not sufficient on its own** — `_reset_cohort_tables` (`:3636-3665`) `DROP`s `cohort_files`, which deletes its `sqlite_sequence` row, so ids restart at 1 after any reset. Use a random per-file token (`uuid4`, `UNIQUE`) stored in `library_datasets` as the linkage key (or `AUTOINCREMENT` *plus* changing the reset to `DELETE FROM`), clear library linkage inside `remove_samples`, and have repair verify the token. Add a test that imports a second same-named dataset after removal, and one that resets the cohort tables between imports. The existing test `test_cohort_membership_is_state_driven_and_repairable` never imports a second dataset.

### H2. Full-WGS reindex double-counts co-resident samples
- `local_service/sample_library.py:1636-1655` — full-WGS reindex calls `cohort.import_vcf(path=<original multi-sample VCF>, force=True, import_profile="full")`, which indexes **every** sample column; `:1656-1669` then removes only *this* dataset's sample from the previous cohort file. The comment at `:1684-1685` ("Only this dataset's sample was re-imported into the full index") is untrue for multi-sample originals.
- **Reproduced:** two-sample WGS import + `reindex(first, full_wgs=True)` → `P2` carries the same variant in file 1 (prefiltered) and file 2 (full).
- Contradicts `docs/SAMPLE_LIBRARY_AND_STORAGE.md:48-49` ("cannot count the same samples twice"). Both existing tests (`test_sample_library.py:520-551, 569-596`) monkeypatch `cohort.import_vcf`, which hides this.
- **Fix:** restrict the full import to the addressed sample (`bcftools view -s`), or remove all co-resident samples from the previous file and repoint their datasets too.

### H3. b37/rCRS mitochondrial variants are lifted through the hg19 `chrM` (NC_001807) chain and FASTA
- `pipeline/vcf_assembly.py:26-32` maps `hg19|grch37|b37|hs37d5` → `GRCh37`; `pipeline/prepare_liftover_vcf.py:20` admits `M/MT`; the source FASTA is UCSC `hg19.fa.gz` with `chrM→MT` (`scripts/download_references.sh:480-482`) and the chain is `hg19ToHg38` (`:506-507`).
- hg19 `chrM` is NC_001807 (16,571 bp), not rCRS. Ensembl GRCh37 / Broad b37 / hs37d5 — the assemblies most clinical GRCh37 exomes are aligned to — use rCRS (16,569 bp), byte-identical to GRCh38 `MT`. Lifting rCRS coordinates through the chain shifts positions and changes REF at ~40 sites.
- **Where it fails (refined by independent reproduction):** with a valid rCRS input the run aborts **inside the `+liftover` plugin itself**, before the `bcftools norm --check-ref e` step at `:230`. The plugin has some contig-length handling: when the input carries `##contig=<ID=MT,length=16569>` the coordinate shift did not occur; when the header has **no MT length**, `MT:313 C>A` silently became `MT:311 C>A` and passed target-REF validation. So the outcome depends on the input header — abort in one case, silent mis-positioning in the other.
- **Fix:** detect the MT convention (`##contig` length 16569 vs 16571, or the reference name in the header; when neither is present, treat MT as rCRS by default and say so in the liftover QC) and pass rCRS `MT` records through unchanged rather than via the chain.

### H4. GRCh37 intake runs whichever `bcftools` is on PATH — which normally lacks `+liftover`
- `scripts/liftover_grch37_to_grch38.sh:217` — `hts bcftools +liftover …`; `scripts/lib.sh:85-87` — `hts()` uses the native binary whenever `command -v bcftools` succeeds and `HTS_VIA_CONTAINER` is unset (nothing sets it). The plugin is compiled only into the image (`docker/Dockerfile:80-92`).
- `scripts/setup_environment.sh:440-483` installs native bcftools via conda/brew/apt and its comment claims "The pinned in-container bcftools/liftover used for GRCh37 intake is unaffected" — it is affected: `hts()` dispatches to the native executable without checking plugin availability or version. The failure is **conditional** on that installation: standard conda/brew/apt bcftools packages do not bundle third-party plugins, so on those hosts the GRCh37 run dies with bcftools' "Could not load liftover"; a user who has separately installed a compatible plugin is unaffected, but then an unpinned plugin/bcftools version did the work while the provenance JSON records the *configured* `bcftools_version`/`plugin_commit` (`pipeline/write_liftover_qc.py:288`). Either way the pinned-provenance promise is broken.
- **Fix:** force the container for the `+liftover` call (and ideally the whole liftover script), and record `bcftools --version` and the plugin's reported version from the tool that actually ran.

### H5. Exact-allele lookups are representation-sensitive; the two "exact allele" annotators disagree
- `pipeline/clingen_erepo_annotate.py:34-42, 59-66` — SQL lookup on raw `chrom/pos/ref/alt` with only `removeprefix("chr")`; the database side *is* normalised (`prepare_clingen_erepo.py:153-168`). `run_annotation.sh` never runs `bcftools norm` on GRCh38 input, so padded multi-allelic representations (`REF=AT ALT=A,ATT`) silently miss expert-panel assertions. `clingen_erepo.required: true` and QC treats absence as descriptive (`annotation_qc.py:798`), so the false negative is invisible.
- `pipeline/genia_annotate.py:169-171` does normalise (reference-backed left-alignment) before the same kind of lookup.
- Same gap in `cohort_store.variant_key` (`:508-509`, uppercase only) and in `clinvar_aa_match._canonical_allele_text` (`:195-204`).
- **Fix:** one shared allele-normalisation helper (see §5 duplication) applied at the lookup side, or `bcftools norm -f <fasta> -m -any` at intake for all assemblies.

### H6. The service is never shut down gracefully under the launcher; annotation jobs are orphaned and never reclaimed
- `local_service/workbench_service.py` installs no signal handlers; `main()` reaches `service.shutdown()` only via `except KeyboardInterrupt` (`:6190-6194`).
- `scripts/start_workbench.sh:158-164` starts the service as a background child of a background subshell; bash sets SIGINT to SIG_IGN for such children, and CPython then does not install its `KeyboardInterrupt` handler (confirmed empirically). The supervisor's `trap` and `stop_service` send SIGTERM; closing the terminal sends SIGHUP — both are immediate death in Python.
- Children are started with `start_new_session=True` (`:5262`), so a running `run_annotation.sh`/VEP survives. On restart, `JobStore._initialize` only rewrites `running→interrupted` (`:620-629`); the stored `pid` is never signalled (resource downloads *are* reclaimed via `resource-job-pids.json`, `:2877-2907`; annotation jobs are not). Resubmitting produces two pipelines writing the same `output_path`.
- **Fix:** handle `SIGTERM`/`SIGHUP` → `server.shutdown()`; on startup reclaim orphaned annotation pids with the same command-line guard used for resource jobs.

### H7. A second service instance mutates shared state before discovering the port is taken
- `workbench_service.py:6182-6185` — `AnnotationJobService(...)` is constructed (marking running jobs interrupted `:620-629`, SIGTERMing registered resource jobs `:2877-2907`, deleting `resource-secrets` `:2842-2852`, rmtree-ing staging dirs of migrations recorded as running `:855-891`) **before** `create_server` binds the socket.
- `scripts/start_workbench.sh:31-45` checks only the **UI** port; during first launch the service is up long before the UI (`npm ci`/`npm run build`), so a second double-click passes, damages the live instance's state, fails to bind, and the supervisor retries twice more (`:181-191`).
- **Fix:** exclusive lock file in `state_dir` (or bind first); launcher probes `/api/health` on the service port too.

### H8. Every Cohort Search query full-scans `cohort_annotations` (twice)
- `local_service/cohort_store.py:6061-6069` — a `logofunc` CTE with a window function over the *entire* `cohort_annotations` table filtered on the unindexed `logofunc_match`, LEFT JOINed at `:6103-6104` in every mode including exact-variant lookups; run again for totals (`:6127-6135`). `EXPLAIN QUERY PLAN` on the real SQL: `MATERIALIZE logofunc / SCAN source USING INDEX sqlite_autoindex_cohort_annotations_1`.
- `docs/SAMPLE_LIBRARY_AND_STORAGE.md:222-226` says every query form "answers from covering indexes … (the test suite asserts the query plans)"; `test_cohort_store.py:1101-1126` explains hand-simplified statements, not `query()`'s.
- **Fix:** partial index on `logofunc_match` or scope the CTE to `matched_variants`; make the plan test call the real query builder.

### H9. UI renders every variant row unvirtualised, unmemoised, on the main thread
- `webui/app/VariantWorkbench.tsx:1881-1903` (`VariantTable`) and `:2220` map the whole `prioritizedRows` array to `<tr>`s, recomputing `assessDeNovo`, `variantQcFailures`, `geniaListClassifications` per row per render; no `React.memo`/`useTransition`/worker anywhere (the derived-data pipeline is `useMemo`'d, the row components are not); search input is un-debounced (`:1587`). The parser (`vcf.ts:1507-2895`) is designed for ~87k rows, capped at 350k, and `docs/guide/04-first-exome.md:141-143` says hundreds of thousands of rows are normal.
- **Trigger, stated precisely:** the defaults are restrictive (`resetFilters` at `:1513-1525` restores HIGH+MODERATE, popmax ≤ 0.01, MANE-only; "No limit" at `:1611` only removes the frequency cap), so the everyday path is fine. The exposure is the deliberate "relax and look again" workflow — all impacts + no frequency limit + MANE off on a multi-sample or trio exome — where every matching row goes into the DOM at once. This is a scaling risk that was **not benchmarked**, not a reproduced freeze; classified High because the product explicitly invites that workflow and the parser already runs on the main thread.
- **Fix:** virtualise the table (react-window or manual windowing), debounce the search, memoise row components, move parsing to a Web Worker.

### H10. Safety-framing text: wording defect in the scope statement; RUO notice absent from every output artefact
- `README.md:79, 85` — in "What GUIDE-IEI is not", bullets 3 and 4 read "**A cloud service.** …" and "**An automated classification or reporting system.** …" while bullets 1–2 begin "**Not a …**". The heading and the explanatory sentences still convey the intended exclusions, so this is a wording inconsistency rather than an actual reversal of meaning — but it sits in the section `docs/guide/01-what-guide-iei-does.md:52-54` points to as the full scope statement, and it is a two-word fix. (Severity here is "fix before the next release", not "data-integrity".)
- The research-use notice exists in the README (`:91-96`) and the UI (`RESEARCH_USE_NOTICE`, `VariantWorkbench.tsx:242, 1557`; export confirm at `:262`) but is written into **none** of the artefacts that leave the workbench: not the annotated VCF header (post-processors add only `##INFO`/`##LOFTEE_PTC50`/`##iei_haplotype_postprocessing` lines), not the QC report HTML/JSON (`annotation_qc.py:1016-1061`), not the run manifest, not the TSV export (`:5426-5433`). Once a file is shared, the disclaimer is gone.
- Terminology note (not a defect): the name expands to "…Diagnostic-analysis Environment" (`README.md:3`) and code/UI use "diagnostic profile" / "Manage bundled diagnostic sets" (`workbench_service.py:115, 5026`; `VariantWorkbench.tsx:3554`) while the output is described as non-diagnostic. Using the word in a name is a framing choice, not a contradiction in the software; it is flagged only because a consistent term ("required/core profile") would make the RUO position easier to defend.
- **Fix:** add "Not" to both bullets; embed the RUO notice as a `##GUIDE_IEI_notice=` header line and in the QC report/TSV footer; optionally rename the profile.

### H11. `curl | sudo sh` runs without consent under `--yes`
- `scripts/setup_environment.sh:577-590` — on non-WSL-launcher Linux (and "start directly in WSL"), `runtime_cmd="curl -fsSL https://get.docker.com | sudo sh && …"` is gated only by `confirm`, which returns 0 immediately when `ASSUME_YES=1` (`:103`). `scripts/start_workbench.sh:62` runs `setup_environment.sh --install --yes`. The script header (`:16`) promises "Every direct download is version-pinned and SHA-256-verified"; this one is neither.
- **Fix:** exclude sudo/system actions from `--yes` (a separate `--yes-system`), or always print and require an interactive yes.

---

## 4. Medium-severity findings (grouped)

### 4.1 Genomics semantics
- **M1. Missing-score filter semantics are inconsistent and undocumented.** gnomAD popmax: missing **passes** (`VariantWorkbench.tsx:1066`, stated at `:1618`). AlphaMissense/CADD/SpliceAI/promoterAI thresholds: missing **fails** (`:1094-1097`); call-QC also fails on missing DP (`vcf.ts:544-553`). Only the LoGoFunc control says "missing is not neutral". Typing "CADD ≥ 20" silently removes every indel and every variant outside dbNSFP coverage — the opposite of the project's stated principle (`docs/guide/03-datasets.md:177`). Defensible behaviour, indefensible silence; document it on the inputs and in the guide.
- **M2. Phase classification inconsistency** (`pipeline/haplotype_consequences.py:213-232`): unphased het + hom-alt → `PARTIAL_CONFIRMED`; phased het with **no PS** + hom-alt → `POSSIBLE_UNPHASED` (weaker), because the phased state skips the "one het is necessarily in cis with hom-alt" branch and is then rejected for lacking PS. Statistically phased VCFs (Beagle/Eagle/SHAPEIT write `|` without PS) get a worse verdict than unphased ones. The uncommitted reordering of the `common` intersection is correct; this case remains. No test covers it.
- **M3. Frame-restoration never checks that contributors are individually frameshifting** (`haplotype_consequences.py:277-294`): `bcftools view -i` keeps whole records and `norm -m -any` admits sibling alleles of a multi-allelic frameshift site, so an in-frame 3-bp deletion + any co-candidate can yield `FRAME_RESTORED_CONFIRMED`. Guard: require ≥2 contributors whose own CSQ carries `frameshift_variant`, or `sum(len(alt)−len(ref)) % 3 == 0` with a non-zero member.
- **M4. Selenoprotein transcripts silently excluded from 50-bp recomputation** (`loftee_ptc_50bp.py:402-404, 537-538`): in-frame UGA-Sec raises `cds_internal_stop`; SELENON, GPX4, TXNRD1/2 etc. keep LOFTEE's original verdict and are counted as coverage gaps (not in `DELIBERATE_PTC_SKIP_PREFIXES`, `annotation_qc.py:142-147`). Treat UGA as sense for `seleno`-tagged transcripts or classify as a deliberate skip.
- **M5. promoterAI has three divergent implementations**: `vcf.ts:1932-1956, 2641` (max over six keys, nulled unless match is exact), `cohort_store.py:1490-1492` (first value, different key set incl. `PROMOTERAI`, no gating), `wgs_review.py:44-46` (three lowercase keys, no gating). The same managed VCF can show a score in Cohort Search and "No score" in review; the WGS prefilter can retain on a route the review hides. Genotype-class taxonomies are likewise duplicated (`vcf.ts:1185-1256` vs `cohort_store.py:772-820`) and reconciled by hand (`VariantWorkbench.tsx:5455-5461`). Move field lists and aggregation rules into `predictor-registry.json`.
- **M6. `post_processing.clinvar_aa_match.pathogenic_terms` is a dead config knob** (`annotation.config.yaml:432-435`): the active catalog builder hard-codes `PATHOGENIC_LABELS` (`prepare_clinical_protein_catalog.py:38-41`); only the legacy, un-wired `build_clinvar_aa_reference.sh` path reads it. Compound CLNSIG values (`Pathogenic|risk_factor`, `Likely_pathogenic,_low_penetrance`) are excluded, undocumented.
- **M7. `annotation_qc` looks for a header phrase the matcher no longer writes** (`annotation_qc.py:233-236` expects "ClinVar release …"; `clinvar_aa_match.py:492-493` writes "ClinVar snapshot …") — `ClinVar_aa_reference_release` is always `null` in every QC report.
- **M8. Indexed-score integrity check skips SHA-256 whenever `mtime_ns` matches or is absent** (`indexed_scores.py:572-577`); a manifest without `mtime_ns` is accepted on name+size. Force a hash at job start (`build_vep_command.py:543-558`) — the multi-hour VEP run is the thing being protected.
- **M9. No sex/ploidy awareness in the cohort store**: a male X `1/1` called diploid is stored `homozygous` and never matches `hemizygous` (`cohort_store.py:784-796, 6003-6008`); `sex_at_birth` from phenotypes is never consulted. `gnomad_popmax` folds `MAX_AF` and global AFs into one "popmax" number (`:1476-1479`).

### 4.2 Pipeline robustness (shell + Python)
- **M10. ClinVar (~130 MB) is re-downloaded on every annotation run; an offline workstation cannot annotate with defaults.** `fetch_clinvar.sh:51,74,111` renames the download target so the resumable fetcher's "already complete" short-circuit never triggers; `run_annotation.sh:357-359` `die`s on any network failure. The upstream MD5 is already fetched — compare it to the installed `clinvar_latest` and reuse. Also undercuts the README's "network only when the user initiates" claim (§4.5).
- **M11. Silent `set -e` exits.** `run_annotation.sh:404-405` — `CLINVAR_SHA="$(shasum … 2>/dev/null | …)"` terminates the script (exit 127, nothing printed) when `shasum` is absent; the `:-unknown` fallback is unreachable. Same shape at `:297, 317, 412, 715`, `preflight.sh:435, 453`, `download_references.sh:189, 436, 517, 521`, `verify_container_stack.sh:80`. Five other scripts already carry a `shasum`/`sha256sum` fallback — centralise in `lib.sh`.
- **M12. Non-atomic final-artifact writes with presence-only "installed" checks.** Liftover chain (`download_references.sh:503-510`, checked by `-s` at `:496`), coding BED (`build_coding_bed.sh:122`, `-s` at `:58` and `run_annotation.sh:201`), FASTA/hg19 bgzip (`:244, :489`). A truncated BGZF has no EOF marker; `bcftools view -R` on a truncated BED silently drops every variant on later contigs. `bed_track_ready()` at `:344` already shows the right pattern (`gzip -t` + `.tbi`); use it.
- **M13. Liftover leaves ~10 full-size intermediates of the patient VCF beside the deliverable** — no `rm`/`trap` in `liftover_grch37_to_grch38.sh:51-65`. Confirmed in your own `results/`: `…liftover-all`, `-normalized-unsorted`, `-plugin`, `-retained`, `-source-sorted`, `-split`, `-supported` (~2.6 GB) sit next to the final file. Haplosaurus likewise leaves stable-named `.haplo.*` files on the failure path (`run_annotation.sh:689-691, 789`), contradicting the invariant stated at `:96-99`.
- **M14. `bcftools sort` without `-T` inside containers** (`run_annotation.sh:222, 289`; liftover `:213, 232`): the uncommitted `export TMPDIR=$RUN_SCRATCH` is not forwarded by `docker run`, so a WGS sort spills into the Docker VM's disk, not the drive the user sized.
- **M15. VEP/haplo/catalog containers run without `--network=none`/`--pull=never`** (`run_annotation.sh:611, 739`; `build_clinical_protein_catalog.sh:55`; `preflight.sh:443`) while patient VCFs are bind-mounted; `container_access.py:195` and `lib.sh:130` already do it. Free defence-in-depth for PHI.
- **M16. VEP writes the deliverable in place** (`run_annotation.sh:640-649`); a VEP or validation failure leaves a partial `sample.vep.vcf.gz` under the final name. Every later stage uses tmp→mv; VEP should too.
- **M17. `bigBedToBed` is downloaded from UCSC and executed with no pinned checksum** (`screen_ccre_dataset.py:675-758`); the hash is recorded *after* running it. Every other external artefact is pinned.
- **M18. Three-to-four full passes over WGS input before VEP** (`run_annotation.sh:297, 303, 314, 317`), plus `validate_input_vcf`, `sha256(input)` in the manifest, and a second `sha256` for GRCh37 — hours on a 100 GB WGS VCF. The FILTER census awk can emit the total; `bcftools index -n` is already used when an index exists.
- **M19. Liftover cache lock has no staleness detection** (`run_annotation.sh:157-164`): after a crash the next run waits 3600 s. Store a PID and `kill -0`.

### 4.3 Local service
- **M20. Software-update trust is anchored entirely in the GitHub release** (`software_update.py:161, 237-255`): the sha256sums file and archive share one trust root, so tampering by a compromised maintainer account/CI token is undetectable, and a release executes shell/Python/npm-script code with the user's privileges on machines holding PHI. Mitigations present (HTTPS, click-only, 500 MB cap, zip-slip/setuid/local-state guards, rollback) are good; `_extract_archive` (`:405-418`) still reads each member fully into memory with no per-member size bound. Recommend signing `sha256sums.txt` (minisign/Sigstore) with a key pinned in the shipped code, and a plain sentence in the docs about what an update can run.
- **M21. After an update that changed only `webui/`, the in-app "Restart" leaves the old UI bundle serving the new API** — `web_build_required` (`software_update.py:589-597`) is consumed only by the full launcher path; the exit-75 supervisor restarts Python only, and the UI never reads the flag (`VariantWorkbench.tsx:3684-3691`).
- **M22. The single job worker has no supervision** (`:5221-5233`): an unexpected exception (e.g. `sqlite3.OperationalError` on an unplugged data drive) kills the thread and every later job stays `queued` silently until restart.
- **M23. Error propagation**: `do_GET` catches only `ValueError`, `do_POST` only `KeyError/ValueError/JSONDecodeError` (`:5401-5405, 5989-5992`); `sqlite3.Error`/`OSError`/`RuntimeError` drop the connection ("Failed to fetch" in the UI), and every POST `KeyError` is reported as "job not found".
- **M24. Resource-download read-loop failure leaves the child running and invisible to crash cleanup** (`:3147-3224`); the next start of the same download launches a second writer on the same `.part`.
- **M25. No local authentication on the loopback API**, though `_harden_private_permissions` (`:2800-2837`) explicitly targets "shared machine" exposure; any local account can read/delete/migrate the library over HTTP. The README's one-workstation assumption covers this, but the docs should say it in one place (see M35).
- **M26. Update install runs synchronously inside one HTTP request** holding the global reservation, with the whole archive in memory and no progress endpoint (`:1512-1554`).

### 4.4 Data stores
- **M27. A single multi-minute `BEGIN IMMEDIATE` merge blocks every other writer** (`cohort_store.py:5392-5607`); library/phenotype connections time out at 60 s (`sample_library.py:144-147`), so label edits and phenotype saves fail during a Full-WGS import; WAL cannot checkpoint until commit (~2× transient disk). Undocumented.
- **M28. Removing a dataset leaves per-sample projection files (patient genotypes) on disk forever**: `{checksum}.{sample}.….review.vcf.gz` written at `sample_library.py:1268-1271, 1453` are not unlinked by `remove()` (`:1928-1934`), not in `cleanup()` roots (`:2038-2046`), and not matched by `cleanup_partials` (`:2071-2074`). Retention/privacy plus unbounded growth.
- **M29. REMEDIATION P5-6 is incomplete**: `screen_context.filter_variants` (`:427`) still derives the tissue-matrix stride from the catalog row count while `_tissue_evidence` (`:315`) uses the matrix shape, with a comment explaining exactly why the catalog count is wrong. No skew test.
- **M30. No schema versioning for the three stores sharing `cohort.sqlite3`** (`cohort_store.py:2481-2625`, `sample_library.py:231-387`, `phenotype_store.py:387-446` all use `table_info` probing, autocommit ALTERs, backfills gated on the ALTER succeeding in the same process). GenIA and gene-knowledge *do* stamp versions — inconsistent within one codebase.
- **M31. WGS-scale per-record CPU dominated by decoding unread fields**: `parse_csq_entries` `unquote`s every CSQ field of every transcript (`cohort_store.py:825-835`), `evaluate_record` recomputes `available_fields` per record (`wgs_review.py:322-326`); measured ~980 µs/record → ~80 CPU-minutes per genome single-reader, then re-parsed by staging. Index-based extraction of the ~6 needed fields would cut this ~10×.
- **M32. `sample_review_files` materialises up to 200k records as one Python string** (`cohort_store.py:6516-6553, 6589`) — hundreds of MB in the service plus the browser copy.

### 4.5 Web UI
- **M33. Bundled-reference load failure silently degrades filters** (`reference-data.ts:102-118` single `Promise.all`; `VariantWorkbench.tsx:934-955`): IUIS filter falls back to a 7-gene starter list, HI/constraint filters return zero rows, and the error is shown only on the Gene lists page (`:3558`).
- **M34. Patient-derived identifiers persist in browser `localStorage`** (`SAVED_CANDIDATES_STORAGE_KEY`, `:752-788`: `<vcf filename>:<chrom>:<pos>:<ref>:<alt>:<sample name>`), outside every documented data location and invisible to the Storage page's cleanup.
- **M35. No error boundary** (no `error.tsx`/class boundary): any render exception discards an in-memory "Review once" import irrecoverably.
- **M36. 253 of the 276 lint warnings are a false positive from a prop literally named `ref`** — `<CcreContextPanel ref={selected.ref} alt={selected.alt}/>` (`:2225`) passes the REF allele as `ref`; this works only because React 19 forwards `ref` as a plain prop and will break under the React Compiler adoption planned in REMEDIATION D7. Rename to `refAllele`/`altAllele` and drop the suppression in `eslint.config.mjs:17-30`.
- **M37. Dead cohort "direct path indexing" UI** (`CohortPanel` `:3089-3127`, never invoked; 10 `no-unused-vars`) while `webui/README.md:120-134` still documents it.
- **M38. Accessibility gaps** in the primary review surface (CHANGELOG advertises screen-reader support): `<tr onClick>` rows without `tabIndex`/key handler (`:1889`); placeholder-only search label (`:1587`); unlabeled zygosity `<select>` (`:1647`); `role="tab"` without `aria-controls`/`tabpanel`; modals without focus trap/Escape (`:4186, 4222`); 279 CSS rules at ≤9 px font size; no `aria-live` on import progress/errors.

### 4.6 Documentation, licensing, tests
- **M39. Network-access claims undercount reality.** `README.md:12-14` lists three cases; verified additional contacts: ClinVar on **every run by default** (M10; `annotation.config.yaml:359`, UI checkbox default on `VariantWorkbench.tsx:4827`), first-launch runtime downloads (Python/Node/Lima/Colima/Docker CLI), image build pulls (`ensemblorg/ensembl-vep`, GitHub), ENCODE metadata API and dbnsfp.org release checks during dataset setup. None transmit patient data (verified) — the sentence just needs to enumerate them. Full endpoint inventory in Appendix A.
- **M40. SpliceAI data-use terms are not documented** and the registry auto-downloads it with `license_ack_required: false` (`predictor-registry.json:50-63`); Illumina's precomputed scores are non-commercial. CADD/dbNSFP/FuncVEP/PromoterAI are handled carefully; this one isn't. Also the LOFTEE *data files* re-hosted on the mirror are covered only by a code comment citing the code's MIT licence (`download_references.sh:90-91`), and IUIS 2024 classification (bundled) has provenance but no stated licence.
- **M41. Reference mirror is a personal Hugging Face account** (`download_references.sh:95`; `download_screen_context_bundle.sh:23`). Integrity is fine (SHA-256 pinned, canonical fallback); availability/bus-factor for a clinical tool deserves a sentence in docs, and `docs/DATASET_SETUP.md:52` says `IEI_REFERENCE_MIRROR=off` while the script tests for the empty string (`:94-95`) — `off` only "works" because DNS fails.
- **M42. Documentation sprawl with visible drift**: dataset setup is described in five places (`guide/03-datasets.md`, `DATASET_SETUP.md`, `REFERENCE_SETUP.md`, `dataset-reference.md`, `ANNOTATIONS.md`); `INSTALLATION.md:145-160` prints an "expected tail" for `test_dry_run.sh` (`argv tokens: 40`, `matched=1`, three steps) that the current script no longer produces (`49`, `residue_matched=1 change_matched=0 …`, `[2b/3]`, `[2c/3]`). `check_documentation.py` cannot catch prose drift by design.
- **M43. Contradictory guidance on cloud-synced folders** (`INSTALLATION.md:117-131` recommends a synced working copy via `sync_to_onedrive.sh`; `guide/02-install.md:46-47, 322` says avoid them), and `scripts/sync_to_onedrive.sh:31` hard-codes `/Users/yl3232/Library/CloudStorage/OneDrive-ColumbiaUniversityIrvingMedicalCenter/…`. Developer-only probe paths also ship in `setup_environment.sh:242, 368` and `start_workbench.sh:94, 126` (`~/.cache/codex-runtimes/…`).
- **M44. Hand-rolled test runners can silently drop tests**: `test/test_predictor_registry.py:79` defines `test_invalid_utf8_registry_is_reported_as_registry_error` but the runner list (`:317-329`) omits it — CI runs 12 of 13. Fifteen other files enumerate tests by hand. Env-gated integration tests (`IEI_RUN_HTS_INTEGRATION`, `IEI_REAL_WGS_REVIEW`) are never set in CI; bcftools-dependent tests `skipTest` rather than fail, so a brew failure on the macOS runner would downgrade coverage silently. A `unittest` discover step (or pytest) would remove the class of bug.
- **M45. No security/privacy model page** for IT/compliance readers: loopback-only, no auth, what lives in `localStorage`, retention/deletion semantics, log contents, and what an update can execute are scattered across five pages or absent.

---

## 5. Cross-cutting themes

**Duplication with drift.** The same low-level concept is implemented independently in many places, and several have already diverged:
- chromosome normalisation ×6 (differing `M/MT` handling; `screen_ccre_dataset.normalized_chrom:872-874` does not map `M→MT`);
- minimal-allele trimming ×4 (`haplotype_consequences.minimal_variant_id`, `write_liftover_qc._trimmed`, `genia_alleles.normalize_allele`, `prepare_clingen_erepo.normalize_allele`) with three loop orders — the root of H5;
- `open_text`/`_open_r` ×12, `info_map`/`parse_info` ×5 (flags become `"1"`, `""`, `None`, or are dropped), `sha256` ×9 across Python and shell, `utc_now` ×4 with different timespecs (so `imported_at` mixes formats), `_open_ro` ×3, BGZF/TBI parsing duplicated verbatim (`funcvep_dataset.py:456-483` vs `logofunc_dataset.py:50-74`);
- shell: the config-path idiom under seven names (`absdir`, `abs_path_optional`, `configured_path`…), the `RUNTIME/IMAGE` block copy-pasted into eight scripts, the tmp→bgzip→mv→tabix publish block five times in `run_annotation.sh`;
- client/server semantics: promoterAI (M5), genotype classes, gnomAD popmax key lists, ClinVar predicates.
A `pipeline/_vcf.py` + `_alleles.py` and a richer `lib.sh` would remove ~300–500 lines and make several Highs one-line fixes.

**Three monoliths with obvious seams.** `workbench_service.py` (job store/worker, resource jobs, storage migration, dataset catalog tables, HTTP handler with ~100 `if path ==` branches, bulk intake), `cohort_store.py` (HTS backend, parsing, predictor normalisation, staging, schema, prediction API, a ~570-line legacy importer reachable only via `IEI_COHORT_LEGACY_IMPORT=1` that duplicates the UPSERT clauses verbatim, query builder), and `VariantWorkbench.tsx` (pure helpers, filter predicate over ~35 `useState`s with two hand-written reset lists that already disagree at `:1513-1525` vs `:1694-1722`, six panels, review workspace). None of this is urgent, but each seam is where the drift above lives.

**"The test asserts X" claims that the test does not assert.** H8 (query plans), H2 (mocked `import_vcf`), M29 (skew), M44 (runner lists). The suite is large and good; the gaps are precisely at the highest-stakes behaviours (minus-strand and multi-exon junction shift in `test_loftee_ptc_50bp.py`; the mixed phased/hom-alt case; Perl plugins tested only with a stubbed base class; no DOM/React test at all; `local-app.test.mjs` pins JSX text with ~100 regexes).

**Two code paths where one is dead.** ClinVar protein catalog (`reduce_vep_to_aa_reference.py` + `build_clinvar_aa_reference.sh` vs the wired `prepare_clinical_protein_catalog.py`), cohort legacy importer, cohort direct-indexing UI, `VepPlan.command_string()`/`_shquote` (the runner re-implements quoting in bash), `vep_hg38.sh` referenced in four places but absent.

---

## 6. The uncommitted work in progress

The 53-file working-tree change is a coherent hardening batch and reads well: streaming input-VCF validation before any HTS command (`pipeline/validate_input_vcf.py`, wired into `run_annotation.sh` and `preflight.sh`); a container file-access probe (`pipeline/container_access.py`); core-dump suppression on every host and container process (`ulimit -c 0`, `--ulimit core=0:0` — sensible for PHI); a per-run scratch directory; `hts()` retry narrowed to rc 1–124 with signal deaths reported; the updater's repair path now preserves the last complete rollback snapshot and validates it before use; RFC 5987 `Content-Disposition`; `do_GET` `ValueError`→400; negative `Content-Length` rejected; CI now runs the doc checker and `release.yml` gates on the full clean-clone suite with least-privilege `contents: read`; the haplotype `common`-haplotype intersection moved after the phase-set gate (correct direction); `test/sample.mini.vcf` gained the INFO column it was missing (the new validator found a nine-column fixture). Tests for the new pieces exist and pass.

Observations on it:
- `export TMPDIR="$RUN_SCRATCH"` does not reach containers (M14) — the comment says "Host-side temporary files that later enter a container", which is accurate, but `bcftools sort` inside the container still spills to the VM.
- M2 (phase classification) is untouched by the reorder and still worth a test.
- `lib.sh` now `die`s at source time if `ulimit -c 0` fails; lowering a soft limit always succeeds, so this is fine, but note `lib.sh` is sourced by the test helpers too.
- The batch mixes ~25 documentation files with the code changes; splitting it into three or four commits (validation/probe, updater, launcher/CI, docs) would make the history bisectable.

---

## 7. Strengths worth keeping

- **Correct core genomics**, verified independently: BGZF/`.gzi`/`.fai` random reader across block boundaries; CDS reconstruction incl. split stop codons and UTR-only terminal exons; PTC scan and junction shift; HGVSp `fsTer` arithmetic; GRCh38 PAR bounds in both Python and TS; `ALLELE_NUM`-first attribution with refusal to broadcast across ALTs; AD `.`→null not 0; half-calls flagged not dropped; FILTER `.` retained; `+` preserved in HGVS.
- **Conservative refusal semantics** everywhere: named statuses (`transcript_version_mismatch`, `ref_mismatch`, `allele_mapping_failed`, `IEI_LIFTOVER_REJECT_REASON=…`) instead of fabricated values; LOFTEE-namespaced verdicts never written where LOFTEE produced none.
- **Security hygiene**: `yaml.safe_load` only; no `shell=True`/`eval`/`pickle`; argv-list subprocesses with positional user paths; Host+Origin checks on every route with loopback-only CORS echo (probed with a dozen bypass attempts — all rejected); read-only immutable SQLite for reference DBs; secrets via 0600 files never argv; `--pull=never --network=none` on probes; zip-slip/setuid/local-state guards in the updater; 0700 hardening of every PHI root including migration targets.
- **Atomic publish discipline** in installers, migration, GenIA/gene-knowledge rebuilds, run outputs (tmp→mv, rollback markers written last, verified copy→rename→registry switch, "never delete an activated root").
- **`parallel_fetch.py`** is excellent (per-range `Content-Range` validation, ETag drift, resume-state integrity, short-`pwrite` handling for FUSE/OneDrive, credentials via `curl --config -`).
- **Provenance**: image source-fingerprint label checked by preflight/doctor/installer; run manifest per output; audit JSON per post-processing step; upstream MD5 verification; SHA-256-pinned mirror with canonical fallback; typed predictor registry with per-source licence/ack fields and a CI-gated generated reference table.
- **Honest clinical language** in the guide ("candidate, not diagnosis", "blank means unavailable, not benign", "not a validated installation").
- **Release engineering** coherent end-to-end (`VERSION` ⇄ `CHANGELOG` ⇄ tag enforced twice; full suite is a release prerequisite; Linux/macOS/Windows CI on every PR).
- **Comments record the why**, often with audit IDs — which made this review tractable.

---

## 8. Suggested order of work

1. **Data-integrity fixes (small, high value):** H1 (per-file UUID token as the linkage key — not `AUTOINCREMENT` alone — plus clear linkage in `remove_samples`), H2 (restrict full import to the addressed sample), M28 (delete projection files on removal), M29 (use matrix shape). Add the missing tests alongside each.
2. **GRCh37 intake:** H4 (force container for `+liftover`, record the real bcftools version), H3 (rCRS MT passthrough), M13 (clean intermediates).
3. **Allele normalisation:** one shared helper; apply in ClinGen lookup and cohort keys (H5).
4. **Service lifecycle:** H6 (signal handlers + orphan reclaim), H7 (instance lock + health probe), M22 (worker catch-all), M23 (500 JSON), M21 (`web_build_required` in the restart path).
5. **Safety text:** H10 (two words in the README, one decision on "diagnostic", RUO line in outputs), M39 (network sentence), M45 (one security/privacy page).
6. **Setup:** H11 (`--yes` must not cover sudo), M11 (`sha256` helper in `lib.sh`), M12 (atomic publish for chain/BED/FASTA).
7. **Performance:** H8 (index/scope the logofunc CTE; make the plan test real), H9 (virtualise + worker), M31.
8. **Consolidation pass** on duplicated helpers and the promoterAI/genotype/missing-score semantics (M1, M5), then the lint `ref` rename (M36) so the suppression baseline can go.
9. **Test harness:** switch to `unittest` discovery or pytest (M44); add minus-strand/multi-exon PTC cases, the phased+hom-alt case (M2), a route-table cross-site test, path-containment negatives, and a second-instance test.

---

## Appendix A — Network endpoint inventory (runtime code)

| Host | Trigger | Where |
|---|---|---|
| `ftp.ncbi.nlm.nih.gov` (ClinVar + `.md5`) | **each annotation run** (default on); dataset setup | `annotation.config.yaml:361`, `fetch_clinvar.sh:47,75` |
| `api.github.com`, `github.com/…/releases/download/` | update check / install (click) | `software_update.py:43-44` |
| `spliceai-38-…a.run.app` (Broad SpliceAI Lookup) | opt-in per-variant button; coordinates only; cached | `workbench_service.py:455, 3720-3729` |
| `data.omim.org` (user's private URL, redirect-validated) | user pastes link | `workbench_service.py:180-195, 2584` |
| `dist.genos.us` (dbNSFP, user's academic link) | user pastes link | `download_dbnsfp.sh:50`, `parallel_fetch.py` |
| `www.dbnsfp.org/releases/` | advisory version check during setup | `check_dbnsfp_version.py:18` |
| `huggingface.co/datasets/luoyiming1991/*` | reference + SCREEN bundle mirror | `download_references.sh:95`, `download_screen_context_bundle.sh:23` |
| `ftp.ensembl.org` | VEP cache, FASTA, GTF, SpliceAI | `download_references.sh:84,87,443`, `build_coding_bed.sh:64` |
| `personal.broadinstitute.org/konradk/loftee_data` | LOFTEE data fallback | `download_references.sh:85` |
| `hgdownload.soe.ucsc.edu` | rmsk, segdups, hg19 FASTA, chain, `bigBedToBed` | `download_references.sh:86,472,499`, `screen_ccre_dataset.py:641` |
| `kircherlab.bihealth.org` | CADD v1.7 (optional) | `download_cadd_wgs.sh:27` |
| `zenodo.org` | LoGoFunc, FuncVEP (optional) | `logofunc_dataset.py:21,256`, `funcvep_dataset.py:41,86` |
| `downloads.wenglab.org`, `users.moore-lab.org`, `www.encodeproject.org` | SCREEN cCRE preparation (+ per-experiment API) | `screen_ccre_dataset.py:55,58,233,257`, `screen_immune_curation.py:239,258` |
| `github.com/obophenotype/cell-ontology` | SCREEN preparation | `cell_ontology.py:11` |
| `erepo.clinicalgenome.org` | ClinGen install/update | `annotation.config.yaml:376`, `update_clingen_erepo.sh:9,74` |
| `storage.googleapis.com`, `search/ftp.clinicalgenome.org`, IUIS S3 | maintainer rebuild scripts only | `update_gene_knowledge.sh`, `update_workbench_references.sh` |
| `github.com` (python-build-standalone, lima, colima), `nodejs.org`, `download.docker.com`, `get.docker.com` | first launch / `--install` | `setup_environment.sh:259,387,530-544,577` |
| Docker Hub `ensemblorg/ensembl-vep`, GitHub (bcftools, htslib, loftee), `raw.githubusercontent.com` | image build | `docker/Dockerfile:41,80-85,100` |

Telemetry: `NEXT_TELEMETRY_DISABLED=1` on all Next scripts; no analytics, fonts, or CDNs in the UI. No patient-derived payload leaves the machine except the opt-in SpliceAI coordinates.

## Appendix B — Licence / redistribution

| Resource | Licence | Bundled? | Docs |
|---|---|---|---|
| VEP + cache, Ensembl FASTA/GTF | Apache-2.0 / open | no (mirror + canonical) | ok |
| LOFTEE code / **data files** | MIT (code); data terms not stated | data re-hosted on mirror | **gap** |
| dbNSFP | CC BY-NC-ND academic | no; private link redacted | good |
| SpliceAI (Ensembl MANE v1.4 file) | Illumina non-commercial (not stated) | no; auto-download, no ack | **gap** |
| CADD v1.7 | non-commercial | no; ack required | good |
| PromoterAI | Illumina terms | plugin code only | good |
| LoGoFunc / FuncVEP / GenIA / OMIM | Zenodo / PolyForm Strict / registered / user licence | no | good |
| ClinVar / ClinGen / HGNC / gnomAD constraint | open | ClinGen+HGNC in bundled sqlite; gnomAD TSV | ok |
| IUIS 2024 classification | not stated | yes (172 KB + lists) | provenance yes, licence **not stated** |

## Appendix C — Low-severity items (compact)

**Shell/packaging:** `run_annotation.sh` 976 lines with five copies of the publish block; `build_clinvar_aa_reference.sh` dead; `setup_environment.sh:214` `GIT_OK` unused; VEP cache mounted `rw` for haplo (`run_annotation.sh:742`) vs `ro` for VEP; SQLite URI by string concat (`run_annotation.sh:508`; `preflight.sh:340` does it right); `fetch_clinvar.sh:105` stamps today as "release" when `##fileDate` absent; `--help` dumps every column-0 comment (`run_annotation.sh:46`); `download_references.sh:44` ignores unknown flags; `start_workbench.sh` doesn't validate `IEI_UI_PORT`/`IEI_SERVICE_PORT`; Windows `-Update` `rm -rf`s the WSL copy preserving only config+references (`GUIDE-IEI.ps1:206-207`); `prepare_dbnsfp.sh:164` legacy merge `sort` without `-T`/`LC_ALL=C`; `update_clingen_erepo.sh:57-63` hashes ~1 GB per currency check; Dockerfile not multi-stage, `sed` patches unasserted (`:121-126`); actions not SHA-pinned, `checkout@v4` vs `@v5`; `hts()` retry doubles time-to-failure for genuine errors; `hts()` mounts `$PWD` rw into every helper container (`lib.sh:128`).

**Pipeline:** `build_vep_command.py:319-320` no `.tbi` check for dbNSFP; index-sibling check on symlink not target (`:159-169`); `LoGoFunc.pm:108` exact `Feature` equality with no version strip (IndexedScores strips); Perl plugins tested only with stubbed base class; `logofunc_dataset.download_file` wedges on a completed `.part` (416 loop, `:149-178`); `extract_fasta_regions.py:47-53` quadratic fallback; `loftee_ptc_50bp.load_transcripts:295-298` regex-parses every GTF line before membership check; `genia_annotate` decompresses a BGZF block per `fetch_base` with no cache; `clinvar_aa_match.main:644` uses `set()` as a null Reference; catalog silently drops rows where VEP `SYMBOL` ≠ NCBI `GENEINFO` (`prepare_clinical_protein_catalog.py:267-272`); dbNSFP 5.4a (Ensembl 116) against VEP 113 with `transcript_match=1` — expected miss rate undocumented; PS1/PM5 flags conflated in `*_path_aa_match` (`clinvar_aa_match.py:384-391`).

**Service:** any loopback origin on any port trusted (`:6042-6044`); request lines incl. query strings printed to stdout (`:6058`) and captured by the Windows transcript; `submit()` mkdirs before validation (`:4153`); downloads don't check for running annotation (`:2094-2135`); `_RESOURCE_PERCENT` matches any `NN%` (`:3096`); `--host ::1` accepted but AF_INET (`:6092`); `clingen_erepo.py:28,64` `with sqlite3.connect` doesn't close; `open_storage_location` never waits on Popen (`:2079`); cancel race between commands (`:5148-5176` vs `:5253-5257`); log-file reads under `_resource_lock` (`:2086-2092`); dead routes (`/api/clingen-erepo/status`, `/api/phenotypes/individual/<id>`, single-job/single-sample GETs, `/api/bulk-intake/<id>`); `parse_version("0.13.0-rc1")` sorts above `0.13.0`; `SERVICE_VERSION = "0.13.0"` (`:79`) vs `VERSION` 0.6.1 vs `package.json` 0.1.0 vs UI "MVP 0.6" (`VariantWorkbench.tsx:1555`).

**Stores:** staged importer silently drops non-integer POS after counting it (`cohort_store.py:1961-1966`); hard-killed imports leak `cohort-staging/*` GBs outside every cleanup; synchronous `import_vcf` callers never take `_maintenance_lock`; `GeniaStore.status()` runs `quick_check` + `COUNT(*)` on every lookup (`genia.py:936-946`); `gene_knowledge.gene()` calls `_genia_gene` twice (`:672, 707`); no API deletes a phenotype individual; XLSX import unbounded against zip-bomb (`phenotype_store.py:177-225`); `ccre_context._overlaps` treats empty tabix result as authoritative for chr-prefixed overrides; `VACUUM` with no free-space check; `NOT IN (SELECT DISTINCT …)` (`:5593-5599`) vs `NOT EXISTS` elsewhere; DROP/recreate window (`:3647-3665`); `LIKE` wildcards unescaped.

**UI:** `assessDeNovo` fallback type hole (`trio.ts:239-252`; enable `noUncheckedIndexedAccess`); hom-alt with uncalled parents labelled "Mendelian conflict" (`trio.ts:296-330` ordering); unknown proband sex silently disables the hemizygous model with no inline cue; "Homozygous" zygosity filter never matches cohort rows (`:485-488`); literal `16` vs `COHORT_SAMPLE_GUARD` (`:1286`); unhandled rejections in `readGeneList`, `loadPed`, both `handleDrop`s; `request()` has no timeout/AbortSignal; `cancelJob`/`getJobLog` don't `encodeURIComponent`; polling without back-pressure (`:4876`, `:3738-3742`); external `href`s from service data without scheme allow-list (`:2605, 5377, 5415, 4553`); TSV export unescaped, no formula-injection guard (`:5426-5433`); `transcriptDetailCache` never evicts; `GenePanel` O(G²+G·N) (`:5423`); list keys by free-text warning strings (`:2413, 4310`); one unguarded `localStorage` access (`:1229-1233`); stray `pnpm-workspace.yaml` (npm + `package-lock.json` is canonical); `--font-geist-*` variables referenced but never defined (`globals.css:23`); `next build --webpack` with no recorded rationale; 17 MB service-only sqlite served from `public/`.

**Docs/config:** `vep_hg38.sh` referenced in four places, absent; `config/README.md:3-4` tells users to copy to `annotation.config.local.yaml` but nothing loads it; `run.force_overwrite: true` by default; REMEDIATION.md is an internal tracker (references `~/Downloads/`, a pytest DoD though pytest is unused) committed to the public repo; `webui/README.md:120-134` documents the dead cohort path; two doc trees (`docs/*.md` vs `docs/guide/*.md`) by design but with overlap.
