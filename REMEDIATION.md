# Remediation tracker — 2026-08 bug audit

Source reports (in `~/Downloads/`): `vep_annotate_bug_audit.md` (summary), `pipeline_core_review.md`,
`pipeline_aux_review.md`, `service_review.md`, `scripts_review.md`, `webui_review.md`,
`static_findings.md`, `baseline_report.md`.

**114 findings** (13 CRITICAL / 32 HIGH / 51 MEDIUM / 18 LOW) mapped to 6 phases.
IDs: `CORE-n` = pipeline_core_review, `AUX-x` = pipeline_aux_review, `SVC-n` = service_review,
`SH-n` = scripts_review, `UI-n` = webui_review, `ST-x` = static_findings, `B-n` = baseline_report.

**Workflow:** one branch/PR per phase (or per theme within a phase). Every fix lands with a
regression test reproducing the audited failure (the reports' "runtime probe" blocks are
ready-made test cases). After each phase: rerun the baseline suite and compare against
`baseline_report.md` §"Baseline summary".

---

## Open decisions (gate specific items below)

- [x] **D1 — Python floor:** fix the f-string for 3.8+ (hoist `first.count(b'\t')` to a local), or raise the documented floor to 3.12? → gates CORE-1 — **decided: keep 3.8+ floor, f-string fixed**
- [ ] **D2 — CADD policy:** all-or-nothing across SNV/indel tables (current, asserted by `test_optional_cadd_requires_both_data_files_and_indexes`), or SNV-only allowed (the SpliceAI precedent)? → gates half of CORE-6
- [ ] **D3 — Allele-balance denominator** on multi-allelic sites: all-allele AD sum (current, GATK convention) or two-allele REF+ALT ratio? → gates UI-18
- [ ] **D4 — Hemizygous X in haplotype phasing:** treat as cis by construction, or keep conservative `POSSIBLE`? → gates part of CORE-2
- [ ] **D5 — PromoterAI insertions:** confirm SNV-only scope is intended; delete the misleading start/end swap and document → gates SH-8
- [ ] **D6 — `write_run_manifest.py`:** wire into `run_annotation.sh` (provenance sidecar) or delete as dead code → gates AUX-L5
- [ ] **D7 — eslint debt:** fix the 212 `VariantWorkbench.tsx` errors, or adopt an agreed suppression baseline? → gates B-5

---

## Phase 0 — Fix the safety net

Make the tests trustworthy before touching logic.

- [x] **P0-1** (MED) `test/test_dry_run.sh:55` — inert assertion (`grep -q … && echo` in tested context); aa-match check passes regardless [SH-17]
- [x] **P0-2** (MED) `test/test_hts_helper.sh:27` — inert negated assertion (`! grep …` never trips errexit); host-path-leak check passes regardless [ST-M3, B-6]
- [x] **P0-3** (CRIT) `pipeline/screen_ccre_dataset.py:678` — f-string SyntaxError on Python <3.12; whole module + its test dead on documented floor [CORE-1, ST-H1, B-1] *(per D1)*
- [x] **P0-4** `test/test_logofunc_dataset.py` — no `unittest.main()`; runs 0 tests standalone, silent pass [B-4]
- [x] **P0-5** `test/test_prepare_promoterai.py` — pytest-only fixture style; runs 0 tests standalone [B-4] *(converted to unittest; runs standalone and under pytest)*
- [x] **P0-6** 12 test files lack `sys.path` bootstrap — documented `python test/<file>.py` invocation fails without `PYTHONPATH=.` [B-2] *(11 files bootstrapped + logofunc in P0-4; the other 5 unbootstrapped files use subprocess only and never needed it)*
- [x] **P0-7** `test_build_command.py::test_full_stack_native` returns instead of asserting (`PytestReturnNotNoneWarning`) [B-7]
- [x] **P0-8** Re-record baseline numbers after P0 lands — see below

### Phase 0 baseline (2026-08-08, host: macOS, Python 3.14.4)

Every one of the 28 Python test files now passes standalone via the documented
`python3 test/<file>.py` / `python3 local_service/test_<file>.py` invocation, rc=0,
no `PYTHONPATH` needed — including `test_screen_ccre_dataset.py` (4 tests, previously
uncollectable) and `test_workbench_service.py` (44 tests **including the 2 loopback
HTTP tests** — confirming baseline B-3 was an audit-sandbox limitation, not a repo defect).
Counted tests: 162+ (a few files print custom summaries without a count line).
Both shell tests pass with their assertions now live (verified the fixed shapes fail
when the guarded regression is simulated). WebUI suite not runnable on this host
(no `node` binary); unchanged from audit baseline (42 pass / 1 skip) as Phase 0
touched no webui code. pytest is not installed on this host; per-file standalone
execution is the equivalent verification.

---

## Phase 1 — Clinically wrong results (silent)

> **Status note (2026-08-08):** Python-side items fixed on
> `fix/phase-1-clinical-correctness-python`. WebUI items (1B, most of 1D, 1E)
> are **deferred until a node runtime is available** — no `node`/`tsc` exists
> on this host, and unverifiable edits to clinical review code were ruled out.
> Install node (e.g. `brew install node`) to unblock the webui half.

### 1A. Haplotype / phase (produces confident wrong calls that *hide* real biallelic LoF)
- [x] **P1-1** (CRIT) `pipeline/haplotype_consequences.py:80-87` — GT `1/2` (definitionally trans) classified `homozygous_alt` → `FRAME_RESTORED_CONFIRMED`; also loses per-haplotype info for `1|2` [CORE-2] *(fixed: multi-alt genotypes get unknown placement → POSSIBLE/PARTIAL, never CONFIRMED; hemizygous-X half still open per D4)*
- [x] **P1-2** (CRIT) `pipeline/haplotype_consequences.py:128-136` — phased pair with **no** PS at either site returns CONFIRMED; require one populated PS on every non-hom state [CORE-3]
- [x] **P1-3** (HIGH) `pipeline/haplotype_consequences.py:66,230` — key contract mismatch: candidate keys are post-`norm` minimal reps, annotate keys pre-`norm`; indel lookups silently miss. Also `2/2` genotype credited to ALT#1 [CORE-9] *(fixed: minimal-representation key fallback on both sides, per-ALT keys for no-ID records, unmatched keys counted + warned; full left-shift normalisation still needs the reference — misses are now visible instead of silent)*

### 1B. WebUI CSQ decoding (corrupts clinical export)
- [ ] **P1-4** (CRIT) `webui/app/vcf.ts:411-413` — `decode()` form-decodes `+`→space, corrupting every intronic HGVS (lands in `iei-prioritized-variants.tsv`) [UI-1]
- [ ] **P1-5** (CRIT) `webui/app/vcf.ts:411-413` — unguarded `decodeURIComponent`; one stray `%` aborts the entire import (guarded pattern exists at vcf.ts:456) [UI-2]

### 1C. Allele-index family (per-allele values pooled across ALTs — one shared fix pattern)
- [x] **P1-6** (HIGH) `local_service/wgs_review.py:216-222` — `_numbers()` flattens per-allele fields, callers `max()` across all ALTs [SVC-7] *(fixed: `_info_numbers()` selects this ALT's comma token when arity matches ALT count)*
- [x] **P1-7** (HIGH) `local_service/wgs_review.py:300-301` — allele-match failure falls back to pooling every ALT's consequences [SVC-8] *(fixed: unattributable values can no longer EXCLUDE an allele; they may still QUALIFY the record for retention, which is record-granular and errs safe)*
- [ ] **P1-8** (HIGH) `webui/app/vcf.ts:1020-1024` — same fallback: `matching.length ? matching : consequences` cross-assigns annotations [UI-4]
- [ ] **P1-9** (HIGH) `webui/app/vcf.ts:514-519` — `Number=A` INFO collapsed with `Math.max` across alternates; rare allele inherits common allele's AF and is filtered out [UI-8]
- [x] **P1-10** (MED) `pipeline/annotation_qc.py:228-238` — LoGoFunc class from first CSQ entry in file order, not picked/MANE transcript [CORE-19] *(fixed: `preferred_entry()` — MANE, then PICK, then order)*

### 1D. Genotype semantics (webui + service)
- [ ] **P1-11** (HIGH) `webui/app/vcf.ts:977-980` — `FILTER=.` records silently discarded as non-PASS [UI-3]
- [ ] **P1-12** (HIGH) `webui/app/vcf.ts:567-568,1014-1016` — half-calls (`./1`) treated as non-carriers; variant row dropped entirely [UI-6]
- [ ] **P1-13** (HIGH) `webui/app/vcf.ts:1005-1012` — no-call with higher GQ overwrites a real called genotype in the merge [UI-5]
- [ ] **P1-14** (HIGH) `webui/app/vcf.ts:579-588` — absent AD/PL parse to fabricated zeros instead of null; feeds QC thresholds [UI-7]
- [x] **P1-15** (MED) `local_service/cohort_store.py:419-427` — half-call `./1` classified hemizygous [SVC-16] *(fixed: new `half_called` zygosity; true haploid calls keep `hemizygous`)*
- [x] **P1-16** (MED) `local_service/cohort_store.py:436-441` — unparseable AD component becomes 0, skewing allele balance [SVC-17] *(fixed: missing components → None; balance suppressed instead of fabricated 0.0)*
- [ ] **P1-17** (MED) `webui/app/vcf.ts:370-373` — `isHeterozygousGenotype` requires a ref allele; `1/2` comp-hets excluded from non-trio path [UI-11]
- [ ] **P1-18** (MED) `webui/app/vcf.ts:307-315` — QC genotype-class fallback classifies `./.` and `0` as homozygous-alt [UI-12]
- [ ] **P1-19** (MED) `webui/app/vcf.ts:602` — allele-balance denominator on multi-allelic records [UI-18] *(per D3)*

### 1E. Trio logic
- [ ] **P1-20** (HIGH) `webui/app/trio.ts:200-206` — no chromosome/sex awareness; male X de novo → `mendelian_conflict` or `likely_artifact` [UI-9]
- [ ] **P1-21** (HIGH) `webui/app/trio.ts:270-278,336-341` — absent parental genotypes reported as `de_novo`; unphaseable pairs promoted to `possible_trans` [UI-10]
- [ ] **P1-22** (MED) `webui/app/trio.ts:113-131` — `parsePedigree` builds trios for unaffected sibs; ghost parents produce zero warnings [UI-15]
- [ ] **P1-23** (MED) `webui/app/vcf.ts:1239-1244` — evidence merge keyed without contig normalization; `chr1` vs `1` trio files never merge [UI-17]
- [ ] **P1-24** (MED) `webui/app/VariantWorkbench.tsx:3740-3759` — cohort fallback fabricates AD from DP×AB; hardcodes called/carrier/phase [UI-13]

### 1F. LOFTEE PTC 50 bp rule
- [x] **P1-25** (HIGH) `pipeline/loftee_ptc_50bp.py:693-700` — fabricates `50_BP_RULE:PASS` in `LoF_info` for transcripts LOFTEE never scored (guard tests key presence, not value) [CORE-7] *(fixed: guard on the value; recomputed rule stays in the module's own field)*
- [x] **P1-26** (HIGH) `pipeline/loftee_ptc_50bp.py:355-357` — `last_coding_exon_cds`=1 for single-CDS-block transcript → negative distance, spurious FAIL [CORE-10] *(fixed: anchor is None with one merged CDS block → rule_coding suppressed)*

---

## Phase 2 — Pipeline breaks / silent empty output

### 2A. dbNSFP build (broken against the pinned release — audit triage #1)
- [ ] **P2-1** (CRIT) `scripts/prepare_dbnsfp.sh:54-55` — GRCh38 column detection matches neither `#chr` (real header) nor anything valid; hard-fails on dbNSFP 5.3.1a [SH-2]
- [ ] **P2-2** (CRIT) `scripts/prepare_dbnsfp.sh:53,61,63` — `zcat` fails on macOS; SIGPIPE/pipefail aborts mid-merge where GNU-compatible. Use `gzip -cd` + `sed -n '1p'` like `fetch_clinvar.sh` [SH-1]
- [ ] **P2-3** (MED) `scripts/prepare_dbnsfp.sh:64` — unquoted `$CHR_COL`/`$POS_COL` in `sort -k`; validate as integers [ST-M5]

### 2B. Contig handling / empty-callset guards
- [ ] **P2-4** (CRIT) `pipeline/contig_map.py:51-68` — VCF with data-record `chr` naming but no `##contig` headers → empty map → region filter matches nothing → clean exit, zero variants [AUX-C1]
- [ ] **P2-5** (HIGH) `pipeline/contig_map.py:61-64` — Ensembl→UCSC branch double-prefixes (`chr2`→`chrchr2`), silently dropping contigs [AUX-H1]
- [ ] **P2-6** (MED) `scripts/run_annotation.sh:198-204` — add `[[ "$NAFTER" -gt 0 ]] || die` (successful-but-empty pre-filter reported as DONE); drop `2>/dev/null` on the counters [SH-5]

### 2C. Liftover re-entry
- [ ] **P2-7** (HIGH) `pipeline/prepare_liftover_vcf.py:187-193` + `liftover_grch37_to_grch38.sh:187` + `vcf_assembly.py:65-131` — lifted GRCh38 output keeps GRCh37 contig `length=` → "conflicting assembly evidence" on re-entry; explicit `--input-assembly GRCh38` can't override. Rewrite lengths or let `##iei_target_assembly` take precedence [SH-10, AUX-M2]

### 2D. run_annotation.sh / VEP invocation
- [ ] **P2-8** (CRIT) `scripts/run_annotation.sh:323` — single-quote escaping malformed; any apostrophe in a path → unparseable `sh -c`, VEP never runs. Use `shlex.quote` from `build_vep_command.py` [SH-3]
- [ ] **P2-9** (HIGH) `scripts/run_annotation.sh:190-197` — region pre-filter fallback bgzips without sorting (mirror the WGS path at 141-147: `bcftools sort` + CSI fallback); also double-compresses already-bgzipped input [SH-9]
- [ ] **P2-10** (HIGH) `pipeline/build_vep_command.py:426-441` — `--json` returns 2 with errors only in swallowed stdout; `die` says "see WARN/ERROR above" with nothing printed. Print errors to stderr [CORE-8]
- [ ] **P2-11** (HIGH) `pipeline/build_vep_command.py:329-345` — `cadd.get(key,"")` → `abspath("")` = cwd emitted as real argument under `--no-check` (`if not host_path: continue`); all-or-nothing policy half per D2 [CORE-6]

### 2E. Output-format & record-robustness
- [ ] **P2-12** (CRIT) `pipeline/loftee_ptc_50bp.py:624` — `.vcf.gz` output written as plain text (`open_text` used for input only) [CORE-4]
- [ ] **P2-13** (CRIT-twin) `pipeline/reduce_vep_to_aa_reference.py:45` — identical `.gz`-as-plain-text asymmetry [CORE-4]
- [ ] **P2-14** (HIGH) `pipeline/clinvar_aa_match.py:178-193` — guards the read of `cols[7]` but assigns unconditionally; IndexError aborts run on any <8-column record [CORE-5]
- [ ] **P2-15** (HIGH) `pipeline/clingen_erepo_annotate.py:47-48,66-67` — re-run duplicates `ClinGen_ERepo` INFO keys (headers deduped, records not); pipeline feeds its own output back in [AUX-H2]
- [ ] **P2-16** (HIGH) `pipeline/validate_regression_annotations.py:57-58` — multi-allelic records keyed on raw ALT column can never match expected.yaml; also silent key collisions [AUX-H4]
- [ ] **P2-17** (HIGH) `scripts/build_coding_bed.sh:28,34-35` — `absdir("")` returns `"$ROOT/"` so the `${BED:-default}` fallback never fires; BED built at a directory path. Same latent shape in `download_references.sh:63-71` [SH-4]

---

## Phase 3 — Local-service data loss & races

### 3A. managed_path used as unique key (it isn't — multi-sample VCFs share it)
- [ ] **P3-1** (CRIT) `local_service/sample_library.py:580-581` — `update_metadata` writes capture_kit/target_bed `WHERE managed_path=?`, mutating sibling samples [SVC-1]
- [ ] **P3-2** (CRIT) `local_service/sample_library.py:648-655` — `reindex()` deletes every cohort sample sharing the managed VCF, then reclaims their variants [SVC-2]
- [ ] **P3-3** (HIGH) `local_service/sample_library.py:421-424` — cohort linkage update by managed_path repoints siblings and reverts deliberate exclusions [SVC-5]

### 3B. Migration safety
- [ ] **P3-4** (CRIT) `local_service/workbench_service.py:1421-1442` — non-OSError from `record_migration` deletes the already-activated destination root (total cohort loss) [SVC-3]
- [ ] **P3-5** (HIGH) `local_service/workbench_service.py:3737` — `/api/screen-context/install` missing from the storage-mutation guard set [SVC-10]
- [ ] **P3-6** (MED) `local_service/workbench_service.py:1543` — `open("xb")` blocks any retry of an interrupted migration [SVC-21]
- [ ] **P3-7** (MED) `local_service/workbench_service.py:1590-1598` — one column's failure aborts remaining path rewrites mid-migration [SVC-22]

### 3C. Pipe deadlocks
- [ ] **P3-8** (HIGH) `local_service/cohort_store.py:166-172` — tabix stderr drained only after stdout exhausted; import hangs on chatty stderr [SVC-4]
- [ ] **P3-9** (MED) `pipeline/screen_ccre_dataset.py:648-670` — same pattern with curl (missing `-sS`, progress meter enabled); demonstrated hang [CORE-13]

### 3D. Deterministic temp paths / concurrency (one fix pattern: uuid/PID suffix + trap)
- [ ] **P3-10** (MED) `local_service/cohort_store.py:2247` — `.{source}.building.vcf.gz` collides across same-named VCFs from different dirs [SVC-14]
- [ ] **P3-11** (MED) `local_service/cohort_store.py:2308-2310` — fixed `.partial` races concurrent imports of same content [SVC-15]
- [ ] **P3-12** (MED) `local_service/workbench_service.py:2695-2705` — upload staging `.partial` non-unique; retried upload publishes corrupt VCF that passes byte-count check [SVC-25]
- [ ] **P3-13** (MED) `local_service/gene_knowledge.py:177-179` — fixed `.new` temp DB; concurrent builds clobber [SVC-18]
- [ ] **P3-14** (MED) `scripts/run_annotation.sh:370,403,503-504,528` — four fixed `.tmp` names in shared output dir, no mktemp/trap [SH-16]
- [ ] **P3-15** (MED) `scripts/build_coding_bed.sh:66,91,105-106` — two mktemp files, no `trap … EXIT` (sole outlier vs 4 sibling scripts) [SH-12]
- [ ] **P3-16** (MED) `local_service/cohort_store.py:1301-1320` — import-active check released before maintenance lock taken; removal can race a starting import [SVC-12]

### 3E. State-integrity mediums
- [ ] **P3-17** (MED) `local_service/cohort_store.py:1166-1170` — migration rewrites `analysis_scope` on every startup, reverting operator choice [SVC-11]
- [ ] **P3-18** (MED) `local_service/cohort_store.py:2084-2085,2555` — `mane`/`picked` merged with MAX; reannotation can never clear the flag → two "preferred" transcripts [SVC-13]
- [ ] **P3-19** (MED) `local_service/workbench_service.py:3507-3518` — shutdown-interrupted jobs relabelled `failed` (only `cancelled` checked, not `interrupted`) [SVC-26]

---

## Phase 4 — Silent success / reporting integrity

### 4A. Downloads & references that poison silently
- [ ] **P4-1** (HIGH) `scripts/download_cadd_wgs.sh:46-49` — `die` inside `$(checksum …)` exits only the subshell; malformed MD5 sidecar disables verification entirely (incl. 80+ GB SNV table). Fix: assign then `|| die` [SH-6]
- [ ] **P4-2** (HIGH) `scripts/lib.sh:22-30` — wget branch lacks curl's `-f` parity; HTTP error body promoted to canonical reference path and sticky (`-s` short-circuit) [SH-7]
- [ ] **P4-3** (MED) `scripts/download_references.sh:160-161` — `fetch && gunzip || warn` conflates failures; LoF is `required: true`, should `die` [SH-13, ST-M4]
- [ ] **P4-4** (MED) `scripts/parallel_fetch.py:194-196` — `os.pwrite` return ignored; short writes (routine on OneDrive/FUSE) corrupt multi-GB downloads silently [SVC-29]

### 4B. Empty-but-stamped reference chain (ClinVar aa-match)
- [ ] **P4-5** (MED) `scripts/build_clinvar_aa_reference.sh:76-78,110-116` + `run_annotation.sh:246-247` — zero pathogenic-missense records → empty reference written, exit 0, release stamped; never rebuilt for the life of that release [SH-11]
- [ ] **P4-6** (MED) `pipeline/reduce_vep_to_aa_reference.py:35-36` — headerless input → silent empty reference, exit 0 (same end state) [CORE-18]
- [ ] **P4-7** (MED) `pipeline/clinvar_aa_match.py:218-226` — bare `except Exception` around config resolution degrades to all-zero flag with exit 0 [CORE-16]

### 4C. Version/status checks that can't fail
- [ ] **P4-8** (HIGH) `pipeline/check_dbnsfp_version.py:23-24,111-123` — unparseable release page indistinguishable from "up to date"; report a distinct `latest_unknown`/`online_error` state [AUX-H3]
- [ ] **P4-9** (HIGH) `local_service/workbench_service.py:3029-3031` — `not paths or all(...)`: unconfigured annotation source reports as installed [SVC-9]
- [ ] **P4-10** (HIGH) `pipeline/screen_ccre_dataset.py:271-281` — failed ENCODE lookup cached as empty metadata with `"complete": true`; transient network failure becomes permanent silent exclusion [CORE-11]
- [ ] **P4-11** (MED) `pipeline/screen_immune_curation.py:293-302` — metadata cache not invalidated on policy change, then re-stamped with the *new* policy sha, defeating the integrity check [CORE-14]

### 4D. QC report accuracy
- [ ] **P4-12** (MED) `pipeline/annotation_qc.py:263-270` — deliberate PTC skips counted as missing coverage → false WARN + bogus remediation list [CORE-12]
- [ ] **P4-13** (MED) `pipeline/annotation_qc.py:205-212` — critical dbNSFP field outside `columns` → permanent 0% WARN; validate critical ⊆ configured [CORE-17]
- [ ] **P4-14** (LOW) `pipeline/annotation_qc.py:513-521` — promoterAI status excluded from `overall_status` [CORE-20]
- [ ] **P4-15** (MED) `pipeline/write_liftover_qc.py:66-67,82` — missing provenance tag silently substitutes allele-record count for `lifted_records`; should be an error [AUX-M3]
- [ ] **P4-16** (MED) `pipeline/write_liftover_qc.py:68-76` — post-norm left-alignment conflated with assembly allele change in QC metrics [AUX-M4]
- [ ] **P4-17** (LOW) `pipeline/classify_liftover_records.py:190,207-208` — accounting invariant unreachable; exit 2 gives false assurance [AUX-L1]
- [ ] **P4-18** (LOW) `pipeline/classify_liftover_records.py:78,138-146` — half-missing source allele passes sentinel check, perturbing the reconciliation gate [AUX-L2]

### 4E. UI failure-state honesty
- [ ] **P4-19** (MED) `webui/app/VariantWorkbench.tsx:780-785,727-778` — SCREEN filter silently passes everything while loading and after a failure, with checkbox still checked [UI-14]
- [ ] **P4-20** (MED) `webui/app/local-service.ts:919-926` — `request()` parses JSON before checking `response.ok`; every non-JSON failure surfaces as parse noise [UI-16]

### 4F. VEP plugins
- [ ] **P4-21** (HIGH) `docker/PromoterAI.pm:109-114` — no chr-stripping (unlike LoGoFunc.pm:83): chr-prefixed VCF → every lookup empty, no warning; insertion swap per D5 [SH-8]
- [ ] **P4-22** (MED) `docker/LoGoFunc.pm:82-84,139` — query contig normalised but table contig stored verbatim and never compared; future chr-prefixed table fails silently [SH-15]

---

## Phase 5 — Long tail (mediums/lows, hygiene, cleanup)

### 5A. SQLite / resource handling
- [ ] **P5-1** (MED) `local_service/screen_context.py:108` + `gene_knowledge.py:456,468` — path interpolated into sqlite `file:` URI unescaped; `?`/`#`/`%` break or redirect the open [SVC-20]
- [ ] **P5-2** (MED) `pipeline/clingen_erepo_annotate.py:32-35` — same URI defect; creates stray zero-byte files [AUX-M1]
- [ ] **P5-3** (MED) `local_service/gene_knowledge.py:456,468,408` — `with sqlite3.connect(...)` leaks connections (2 fds per gene lookup) → EMFILE in long-lived service [SVC-19]
- [ ] **P5-4** (LOW) `pipeline/prepare_promoterai.py:61-67,105-106,249-252` — `open_text` gzip raw handle never closed by callers [AUX-L3]
- [ ] **P5-5** (LOW) `pipeline/write_run_manifest.py:81` — bare `open().read()`; module also has no caller [AUX-L5] *(per D6)*

### 5B. Service mediums/lows
- [ ] **P5-6** (HIGH, needs domain confirmation) `local_service/screen_context.py:300` — tissue matrix stride from catalog row count, not stored `shape[1]`; misaligned reads if bundle skews [SVC-6]
- [ ] **P5-7** (MED) `local_service/workbench_service.py:1817-1818` — directory branch hardcodes ClinVar filename for every resource's disk-space probe [SVC-23]
- [ ] **P5-8** (MED) `local_service/workbench_service.py:2229-2232` — WGS review registry pruned to 30 without deleting backing files; links 404 for files still on disk [SVC-24]
- [ ] **P5-9** (MED, needs domain confirmation) `scripts/build_workbench_reference_data.py:141` — required-column guard narrower than columns the parser reads [SVC-27]
- [ ] **P5-10** (MED) `scripts/build_workbench_reference_data.py:218` — row-count sanity check runs after the output is already written; validate before write or write-to-temp [SVC-28]
- [ ] **P5-11** (LOW) `local_service/gene_knowledge.py:257-258` — PMID join leaves empty middle elements (`123||456`) [SVC-30]
- [ ] **P5-12** (LOW) `local_service/phenotype_store.py:319` — `header_row=0` treated as absent (`or` on falsy); use `is None` [SVC-31]
- [ ] **P5-13** (LOW) `local_service/workbench_service.py:2593-2596` — output extension check case-sensitive while input check isn't [SVC-32]

### 5C. Pipeline aux mediums/lows
- [ ] **P5-14** (MED, needs domain confirmation) `pipeline/build_gene_tss.py:59,69-71,97` — version-stripped gene IDs silently overwrite on collision; log collisions or key on (gene_id, chrom) [AUX-M5]
- [ ] **P5-15** (MED) `pipeline/cell_ontology.py:25-28,55-57,78-80` — `ontology_ancestors` returns dangling parent IDs (obsolete/UBERON/GO) absent from `terms` [AUX-M6]
- [ ] **P5-16** (MED) `pipeline/prepare_clingen_erepo.py:344-346` — ZeroDivisionError when every export row is retracted; neighbouring code already guards with `max(1,…)` [AUX-M7]
- [ ] **P5-17** (LOW) `pipeline/extract_fasta_regions.py:22-29,43-55` — duplicate region lines → misleading byte-count abort; dedupe or report [AUX-L4]
- [ ] **P5-18** (LOW) `pipeline/validate_vep_output.py:18` (+ `validate_regression_annotations.py:38`) — CSQ header matched by loose prefix; align on `##INFO=<ID=CSQ,` [AUX-L6]

### 5D. Pipeline core mediums/lows
- [ ] **P5-19** (MED) `pipeline/screen_ccre_dataset.py:768-770` — rtree stores coords as float32 (exact only to 2^24); use `rtree_i32` or document superset-filter contract [CORE-15]
- [ ] **P5-20** (LOW) `pipeline/clinvar_aa_match.py:136` — `csq_present` measures reference-set emptiness, not CSQ presence [CORE-21]
- [ ] **P5-21** (LOW, needs domain confirmation) `screen_ccre_dataset.py:857` vs `local_service/ccre_context.py:67` — two live cCRE coordinate conventions (0-based catalog / 1-based context); record convention in metadata [CORE-22]

### 5E. Scripts / config lows
- [ ] **P5-22** (MED) `scripts/update_clingen_erepo.sh:70-77` — samtools `-r` vs Python fallback: asymmetric failure modes; diff the two extractors' output on a fixture [SH-14]
- [ ] **P5-23** (MED) `scripts/install_recommended_datasets.sh:43-44` — `VEP_RELEASE` missing the `:-113` default every sibling script applies [SH-18]
- [ ] **P5-24** (LOW, needs domain confirmation) `scripts/preflight.sh:297` — `bcftools plugin -l | grep -qx liftover` may never match (format check); verify against the container [SH-19]
- [ ] **P5-25** (LOW) `scripts/lib.sh:61,68-69` — 5 of 6 caller scripts never set/export `IMAGE`/`RUNTIME`; container fallback ignores configured runtime on standalone invocation [SH-20]
- [ ] **P5-26** (LOW) `config/annotation.config.yaml:52` — `liftover.engine` read by nothing; wire or drop [SH-21]
- [ ] **P5-27** (LOW) `config/annotation.config.yaml:88` — `region.source` read by nothing; documented switch unimplemented [SH-22]

### 5F. Static-analysis hygiene (from static_findings.md; no behavioral change expected)
- [ ] **P5-28** (HIGH-fragility) `local_service/cohort_store.py` — 12 B023 late-binding closure sites (lines 1630-1699); bind via default args [ST-H2]
- [ ] **P5-29** `pipeline/clinvar_aa_match.py:152` — same B023 pattern on `fields` [ST-H3]
- [ ] **P5-30** `pipeline/screen_immune_curation.py:670,672` — same B023 pattern in `matches()` [ST-M2]
- [ ] **P5-31** (MED-verify) `pipeline/loftee_ptc_50bp.py:649` — `chrom` unpacked and unused in PTC re-eval path; verify not a dropped contig lookup, else rename `_chrom` [ST-M1]
- [ ] **P5-32** Unused imports/vars: `sample_library.py:12` shutil, `screen_ccre_dataset.py:22,28` os/sys, `build_workbench_reference_data.py:19` defaultdict, `test_clingen_erepo.py:7` annotate_main (placeholder? no test exercises CLI main), `build_coding_bed.sh:64` FAI (dropped feature?), `run_annotation.sh:56` ORIGINAL_INPUT [ST-L1]
- [ ] **P5-33** `scripts/start_workbench.sh:88` — SC2155 declare/assign split [ST-L2]
- [ ] **P5-34** `raise` without `from` ×3 (B904): `cohort_store.py:199`, `logofunc_dataset.py:289`, `prepare_promoterai.py:376` [ST-L3]
- [ ] **P5-35** `build_vep_command.py:294` — unused loop var `label` (B007) [ST-L5]

### 5G. WebUI perf/robustness lows + lint debt
- [ ] **P5-36** (LOW) `webui/app/VariantWorkbench.tsx:3578-3584` — bare filename `slice(0,-1)` truncates into a directory name [UI-19]
- [ ] **P5-37** (LOW) `webui/app/vcf.ts:737-753,843,897` — every VCF read twice; BGZF fully materialized in memory (whole-genome tab OOM) [UI-20]
- [ ] **P5-38** Silent truncation: `getSampleLibrary` limit 1000 / `getCohortSamples` 500 vs server cap 5000, no `truncated` flag (webui report, latent note)
- [ ] **P5-39** eslint: 212 errors in `VariantWorkbench.tsx` incl. one `rules-of-hooks` violation (hook in callback, line 2799) [B-5] *(per D7)*

---

## Cannot verify locally (needs unrestricted host + Docker before release)

- [ ] `scripts/run_annotation_regression.sh` — needs Docker + VEP container + reference stack
- [ ] `local_service/test_workbench_service.py` — 2 loopback HTTP tests (sandbox forbade `bind()`; expected to pass elsewhere) [B-3]
- [ ] `perl -c` on `docker/*.pm` — only compile-checkable inside the VEP container
- [ ] SH-19 plugin-list format check (`docker run --rm --entrypoint bcftools vep-annotate:latest plugin -l | head`)

## Definition of done (from baseline_report.md)

- pytest ≥175/178 green with the screen-cCRE module collectable (the 2 loopback tests pass on an unsandboxed host)
- eslint rc=0 or an agreed suppression baseline (D7)
- `test_logofunc_dataset.py` / `test_prepare_promoterai.py` actually execute their tests
- Both shell-test assertions live (fail when their guarded feature regresses)
- Every phase-1/2 fix carries a regression test derived from the audit's runtime probes
