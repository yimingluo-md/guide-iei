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
- [x] **D2 — CADD policy:** DECIDED 2026-08-08: keep all-or-nothing (tests already lock it; a half-populated pathogenicity column is worse than a loud missing-table failure, and both tables download together). Original text: all-or-nothing across SNV/indel tables (current, asserted by `test_optional_cadd_requires_both_data_files_and_indexes`), or SNV-only allowed (the SpliceAI precedent)? → gates half of CORE-6
- [x] **D3 — Allele-balance denominator** DECIDED 2026-08-08: keep the all-allele AD sum (GATK convention, consistent with the service side); on multi-allelic sites reviewers should read the AD column directly. Original text: on multi-allelic sites: all-allele AD sum (current, GATK convention) or two-allele REF+ALT ratio? → gates UI-18
- [x] **D4 — Hemizygous X in haplotype phasing:** DECIDED 2026-08-08: cis-by-construction adopted for haploid calls on non-PAR X/Y (GRCh38 PAR bounds), matching the existing 1/1-coded path; PAR loci and autosomal haploid calls keep the conservative POSSIBLE. Original text: treat as cis by construction, or keep conservative `POSSIBLE`? → gates part of CORE-2
- [x] **D5 — PromoterAI insertions:** DECIDED 2026-08-08: SNV-only scope confirmed and documented in the plugin; the dead start/end swap removed; image rebuilt and quick-verified. Original text: confirm SNV-only scope is intended; delete the misleading start/end swap and document → gates SH-8
- [x] **D6 — `write_run_manifest.py`:** DECIDED 2026-08-08: wired into run_annotation.sh (step 9) — every run writes `<output>.run_manifest.json` with config hash, container identity, exact VEP argv, and cheap (size+mtime+sidecar) reference identity; manifest failure warns without failing a completed run. Unit test in test/test_run_manifest.py. Original text: wire into `run_annotation.sh` (provenance sidecar) or delete as dead code → gates AUX-L5
- [x] **D7 — eslint debt:** DECIDED 2026-08-08: suppression baseline adopted — react-hooks/refs and react-hooks/set-state-in-effect downgraded to warnings for app/VariantWorkbench.tsx ONLY (eslint.config.mjs, with rationale comment); eslint now exits 0 and gates every other rule at full strength. Refactor deferred to a future workbench decomposition / React Compiler adoption. Original text: fix the 212 `VariantWorkbench.tsx` errors, or adopt an agreed suppression baseline? → gates B-5

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

> **Status note (2026-08-08):** Phase 1 complete except two open decisions.
> Python side on `fix/phase-1-clinical-correctness-python`; webui side on
> `fix/phase-1-clinical-correctness-webui` (node v24 installed; tsc clean,
> 58 webui tests pass, 16 of them new audit regressions). Remaining: P1-19
> (gated on D3) and the hemizygous-X half of P1-1 (gated on D4).

### 1A. Haplotype / phase (produces confident wrong calls that *hide* real biallelic LoF)
- [x] **P1-1** (CRIT) `pipeline/haplotype_consequences.py:80-87` — GT `1/2` (definitionally trans) classified `homozygous_alt` → `FRAME_RESTORED_CONFIRMED`; also loses per-haplotype info for `1|2` [CORE-2] *(fixed: multi-alt genotypes get unknown placement → POSSIBLE/PARTIAL, never CONFIRMED; hemizygous-X half still open per D4)*
- [x] **P1-2** (CRIT) `pipeline/haplotype_consequences.py:128-136` — phased pair with **no** PS at either site returns CONFIRMED; require one populated PS on every non-hom state [CORE-3]
- [x] **P1-3** (HIGH) `pipeline/haplotype_consequences.py:66,230` — key contract mismatch: candidate keys are post-`norm` minimal reps, annotate keys pre-`norm`; indel lookups silently miss. Also `2/2` genotype credited to ALT#1 [CORE-9] *(fixed: minimal-representation key fallback on both sides, per-ALT keys for no-ID records, unmatched keys counted + warned; full left-shift normalisation still needs the reference — misses are now visible instead of silent)*

### 1B. WebUI CSQ decoding (corrupts clinical export)
- [x] **P1-4** (CRIT) `webui/app/vcf.ts:411-413` — `decode()` form-decodes `+`→space, corrupting every intronic HGVS (lands in `iei-prioritized-variants.tsv`) [UI-1]
- [x] **P1-5** (CRIT) `webui/app/vcf.ts:411-413` — unguarded `decodeURIComponent`; one stray `%` aborts the entire import (guarded pattern exists at vcf.ts:456) [UI-2]

### 1C. Allele-index family (per-allele values pooled across ALTs — one shared fix pattern)
- [x] **P1-6** (HIGH) `local_service/wgs_review.py:216-222` — `_numbers()` flattens per-allele fields, callers `max()` across all ALTs [SVC-7] *(fixed: `_info_numbers()` selects this ALT's comma token when arity matches ALT count)*
- [x] **P1-7** (HIGH) `local_service/wgs_review.py:300-301` — allele-match failure falls back to pooling every ALT's consequences [SVC-8] *(fixed: unattributable values can no longer EXCLUDE an allele; they may still QUALIFY the record for retention, which is record-granular and errs safe)*
- [x] **P1-8** (HIGH) `webui/app/vcf.ts:1020-1024` — same fallback: `matching.length ? matching : consequences` cross-assigns annotations [UI-4] *(fixed: fallback restricted to single-ALT; multi-allelic mismatch emits one unannotated row so the carrier stays visible)*
- [x] **P1-9** (HIGH) `webui/app/vcf.ts:514-519` — `Number=A` INFO collapsed with `Math.max` across alternates; rare allele inherits common allele's AF and is filtered out [UI-8] *(fixed: `alleleIndexedInfo()` selects this ALT's comma token when arity matches)*
- [x] **P1-10** (MED) `pipeline/annotation_qc.py:228-238` — LoGoFunc class from first CSQ entry in file order, not picked/MANE transcript [CORE-19] *(fixed: `preferred_entry()` — MANE, then PICK, then order)*

### 1D. Genotype semantics (webui + service)
- [x] **P1-11** (HIGH) `webui/app/vcf.ts:977-980` — `FILTER=.` records silently discarded as non-PASS [UI-3]
- [x] **P1-12** (HIGH) `webui/app/vcf.ts:567-568,1014-1016` — half-calls (`./1`) treated as non-carriers; variant row dropped entirely [UI-6] *(fixed: carrier from present numeric tokens; `called` kept separate; class `other`)*
- [x] **P1-13** (HIGH) `webui/app/vcf.ts:1005-1012` — no-call with higher GQ overwrites a real called genotype in the merge [UI-5]
- [x] **P1-14** (HIGH) `webui/app/vcf.ts:579-588` — absent AD/PL parse to fabricated zeros instead of null; feeds QC thresholds [UI-7]
- [x] **P1-15** (MED) `local_service/cohort_store.py:419-427` — half-call `./1` classified hemizygous [SVC-16] *(fixed: new `half_called` zygosity; true haploid calls keep `hemizygous`)*
- [x] **P1-16** (MED) `local_service/cohort_store.py:436-441` — unparseable AD component becomes 0, skewing allele balance [SVC-17] *(fixed: missing components → None; balance suppressed instead of fabricated 0.0)*
- [x] **P1-17** (MED) `webui/app/vcf.ts:370-373` — `isHeterozygousGenotype` requires a ref allele; `1/2` comp-hets excluded from non-trio path [UI-11]
- [x] **P1-18** (MED) `webui/app/vcf.ts:307-315` — QC genotype-class fallback classifies `./.` and `0` as homozygous-alt [UI-12] *(fixed: uncalled/partial/ref-only → `other`, no fabricated AB QC)*
- [ ] **P1-19** (MED) `webui/app/vcf.ts:602` — allele-balance denominator on multi-allelic records [UI-18] *(per D3)*

### 1E. Trio logic
- [x] **P1-20** (HIGH) `webui/app/trio.ts:200-206` — no chromosome/sex awareness; male X de novo → `mendelian_conflict` or `likely_artifact` [UI-9] *(fixed: hemizygous context for male non-PAR X (GRCh38 PAR bounds) and Y; only the transmitting parent gates the call; diploid AB upper bound skipped for hemizygous calls)*
- [x] **P1-21** (HIGH) `webui/app/trio.ts:270-278,336-341` — absent parental genotypes reported as `de_novo`; unphaseable pairs promoted to `possible_trans` [UI-10] *(fixed: `originFor` accepts only `high_confidence`)*
- [x] **P1-22** (MED) `webui/app/trio.ts:113-131` — `parsePedigree` builds trios for unaffected sibs; ghost parents produce zero warnings [UI-15] *(fixed: affected members preferred with fallback+warning; parents validated against VCF samples or PED members)*
- [x] **P1-23** (MED) `webui/app/vcf.ts:1239-1244` — evidence merge keyed without contig normalization; `chr1` vs `1` trio files never merge [UI-17]
- [x] **P1-24** (MED) `webui/app/VariantWorkbench.tsx:3740-3759` — cohort fallback fabricates AD from DP×AB; hardcodes called/carrier/phase [UI-13] *(fixed: adRef/adAlt reported as null — depths unavailable; unknown zygosity → class `other`)*

### 1F. LOFTEE PTC 50 bp rule
- [x] **P1-25** (HIGH) `pipeline/loftee_ptc_50bp.py:693-700` — fabricates `50_BP_RULE:PASS` in `LoF_info` for transcripts LOFTEE never scored (guard tests key presence, not value) [CORE-7] *(fixed: guard on the value; recomputed rule stays in the module's own field)*
- [x] **P1-26** (HIGH) `pipeline/loftee_ptc_50bp.py:355-357` — `last_coding_exon_cds`=1 for single-CDS-block transcript → negative distance, spurious FAIL [CORE-10] *(fixed: anchor is None with one merged CDS block → rule_coding suppressed)*

---

## Phase 2 — Pipeline breaks / silent empty output

> **Status (2026-08-08): complete** on `fix/phase-2-pipeline-breaks`. All 17
> items fixed; every suite green (28 Python files, 2 shell tests, tsc, 58
> webui tests). dbNSFP detection verified against the real installed 47 GB
> 5.3.1a file; the quoting fix verified end-to-end through `sh -c` on the
> stock macOS bash 3.2.

### 2A. dbNSFP build (broken against the pinned release — audit triage #1)
- [x] **P2-1** (CRIT) `scripts/prepare_dbnsfp.sh:54-55` — GRCh38 column detection matches neither `#chr` (real header) nor anything valid; hard-fails on dbNSFP 5.3.1a [SH-2] *(fixed: two-pass detection — explicit `hg38_*` wins, `#chr`/`pos(1-based)` fallback; verified against the installed file)*
- [x] **P2-2** (CRIT) `scripts/prepare_dbnsfp.sh:53,61,63` — `zcat` fails on macOS; SIGPIPE/pipefail aborts mid-merge where GNU-compatible [SH-1] *(fixed: `gzip -cd` + locally-disabled pipefail for the header read)*
- [x] **P2-3** (MED) `scripts/prepare_dbnsfp.sh:64` — unquoted `$CHR_COL`/`$POS_COL` in `sort -k`; validate as integers [ST-M5]

### 2B. Contig handling / empty-callset guards
- [x] **P2-4** (CRIT) `pipeline/contig_map.py:51-68` — VCF with data-record `chr` naming but no `##contig` headers → empty map → region filter matches nothing → clean exit, zero variants [AUX-C1] *(fixed: fall back to distinct data-record contigs)*
- [x] **P2-5** (HIGH) `pipeline/contig_map.py:61-64` — Ensembl→UCSC branch double-prefixes (`chr2`→`chrchr2`), silently dropping contigs [AUX-H1]
- [x] **P2-6** (MED) `scripts/run_annotation.sh:198-204` — `die` on a 0-variant pre-filter result with a diagnosis-oriented message; `2>/dev/null` dropped from the counters [SH-5]

### 2C. Liftover re-entry
- [x] **P2-7** (HIGH) lifted GRCh38 output keeps GRCh37 contig `length=` → "conflicting assembly evidence" on re-entry [SH-10, AUX-M2] *(fixed in `vcf_assembly.py`: the pipeline marker wins when every declared reference agrees with it and only the stale length dissents; genuine declared conflicts still raise — also repairs already-lifted files)*

### 2D. run_annotation.sh / VEP invocation
- [x] **P2-8** (CRIT) `scripts/run_annotation.sh:323` — single-quote escaping malformed; any apostrophe in a path → unparseable `sh -c`, VEP never runs [SH-3] *(fixed: `shquote_arg()` builds POSIX `'\''` via variable substitution; verified round-trip on bash 3.2 incl. the $LOFTEE_DIR splice)*
- [x] **P2-9** (HIGH) `scripts/run_annotation.sh:190-197` — region pre-filter fallback now mirrors the WGS path: `bcftools sort -O z` then tabix with CSI fallback [SH-9]
- [x] **P2-10** (HIGH) `pipeline/build_vep_command.py:426-441` — `--json` now prints errors to stderr before returning 2 [CORE-8]
- [x] **P2-11** (HIGH) `pipeline/build_vep_command.py:329-345` — unset CADD path counts as missing (never `abspath("")` = cwd); all-or-nothing policy retained pending D2 [CORE-6]

### 2E. Output-format & record-robustness
- [x] **P2-12** (CRIT) `pipeline/loftee_ptc_50bp.py:624` — `.vcf.gz` output written as plain text [CORE-4] *(fixed: suffix-aware `open_text` for the write)*
- [x] **P2-13** (CRIT-twin) `pipeline/reduce_vep_to_aa_reference.py:45` — identical asymmetry [CORE-4] *(fixed: `_open_w` gzip dispatch)*
- [x] **P2-14** (HIGH) `pipeline/clinvar_aa_match.py:178-193` — short records padded to the INFO column instead of IndexError [CORE-5]
- [x] **P2-15** (HIGH) `pipeline/clingen_erepo_annotate.py:47-48,66-67` — record-level keys stripped before re-append; re-annotation is idempotent [AUX-H2]
- [x] **P2-16** (HIGH) `pipeline/validate_regression_annotations.py:57-58` — records indexed per ALT; duplicate keys merge entries instead of discarding [AUX-H4]
- [x] **P2-17** (HIGH) `scripts/build_coding_bed.sh` + `download_references.sh` — empty yaml_get values stay empty (`absdir_opt`), so defaults fire and "not configured" never becomes a directory path [SH-4]

---

## Phase 3 — Local-service data loss & races

> **Status (2026-08-08): complete** on `fix/phase-3-service-data-loss`. All 19
> items fixed; full sweep green. The tabix stderr-drain pattern was proven
> against a 400 KB stderr flood (0.02 s vs the audit's demonstrated hang).

### 3A. managed_path used as unique key (it isn't — multi-sample VCFs share it)
- [x] **P3-1** (CRIT) `local_service/sample_library.py:580-581` — `update_metadata` writes capture_kit/target_bed `WHERE managed_path=?`, mutating sibling samples [SVC-1] *(fixed: scoped to the addressed dataset id)*
- [x] **P3-2** (CRIT) `local_service/sample_library.py:648-655` — `reindex()` deletes every cohort sample sharing the managed VCF, then reclaims their variants [SVC-2] *(fixed: deletion filtered to this dataset's vcf_sample_name)*
- [x] **P3-3** (HIGH) `local_service/sample_library.py:421-424` — cohort linkage update by managed_path repoints siblings and reverts deliberate exclusions [SVC-5] *(fixed: linkage scoped to this import's dataset rows)*

### 3B. Migration safety
- [x] **P3-4** (CRIT) `local_service/workbench_service.py:1421-1442` — non-OSError from `record_migration` deletes the already-activated destination root (total cohort loss) [SVC-3] *(fixed twice over: the history catch broadened to Exception, and a `root_switched` latch makes the cleanup path structurally unable to delete an activated root)*
- [x] **P3-5** (HIGH) `/api/screen-context/install` added to the storage-mutation guard set [SVC-10]
- [x] **P3-6** (MED) `open("xb")` → `"wb"` (writes land only in the job's own staging; destination existence guarded at start) [SVC-21]
- [x] **P3-7** (MED) per-column try/except so one missing column no longer aborts the remaining path rewrites [SVC-22]

### 3C. Pipe deadlocks
- [x] **P3-8** (HIGH) `cohort_store.iter_records` — stderr drained on a thread while stdout streams [SVC-4]
- [x] **P3-9** (MED) `screen_ccre_dataset` streaming curl gets `-sS` (progress meter was an unbounded stderr writer) [CORE-13]

### 3D. Deterministic temp paths / concurrency (one fix pattern: uuid/PID suffix + trap)
- [x] **P3-10** (MED) cohort_store `.building` staging carries a uuid [SVC-14]
- [x] **P3-11** (MED) cohort_store content-addressed `.partial` (file + index) carries a uuid [SVC-15]
- [x] **P3-12** (MED) workbench upload staging `.partial` carries a uuid [SVC-25]
- [x] **P3-13** (MED) gene_knowledge `.new` temp DBs carry a uuid (both builders) [SVC-18]
- [x] **P3-14** (MED) run_annotation.sh post-processing temps carry `$$` and an EXIT trap removes leftovers [SH-16]
- [x] **P3-15** (MED) build_coding_bed.sh installs a `trap … EXIT` for both interval temps [SH-12]
- [x] **P3-16** (MED) removal↔import exclusion is now real: the import worker's write phase holds the maintenance lock; removal uses a non-blocking acquire plus a re-check, keeping fast-fail [SVC-12]

### 3E. State-integrity mediums
- [x] **P3-17** (MED) analysis_scope backfill runs only when the column is first added — operator re-scoping survives restarts [SVC-11]
- [x] **P3-18** (MED) `mane`/`picked` take the incoming import's value at both UPSERT sites; a reannotation can clear a superseded flag [SVC-13]
- [x] **P3-19** (MED) all three job-worker status checks accept `interrupted` alongside `cancelled`; shutdown-interrupted jobs keep their status [SVC-26]

---

## Phase 4 — Silent success / reporting integrity

> **Status (2026-08-08): complete** on `fix/phase-4-silent-success`. All 22
> items fixed; full sweep green. Both Perl plugins now pass `perl -c`
> against stubbed Bio::EnsEMBL modules. Notable contract changes:
> `clinvar_aa_match.py` exits 3 on a missing/empty reference unless
> `--allow-missing-reference` is passed (run_annotation.sh opts in only on
> its deliberate no-reference branch); `build_clinvar_aa_reference.sh`
> dies on a zero-record subset; the QC report gains
> `not_applicable_records` and folds promoterAI into `overall_status`;
> liftover QC reports `renormalized_representation_records` separately
> and `lifted_records: null` (with a warning) when provenance is absent.

### 4A. Downloads & references that poison silently
- [x] **P4-1** (HIGH) `scripts/download_cadd_wgs.sh:46-49` — `die` inside `$(checksum …)` exits only the subshell; malformed MD5 sidecar disables verification entirely (incl. 80+ GB SNV table). Fix: assign then `|| die` [SH-6]
- [x] **P4-2** (HIGH) `scripts/lib.sh:22-30` — wget branch lacks curl's `-f` parity; HTTP error body promoted to canonical reference path and sticky (`-s` short-circuit) [SH-7]
- [x] **P4-3** (MED) `scripts/download_references.sh:160-161` — `fetch && gunzip || warn` conflates failures; LoF is `required: true`, should `die` [SH-13, ST-M4]
- [x] **P4-4** (MED) `scripts/parallel_fetch.py:194-196` — `os.pwrite` return ignored; short writes (routine on OneDrive/FUSE) corrupt multi-GB downloads silently [SVC-29]

### 4B. Empty-but-stamped reference chain (ClinVar aa-match)
- [x] **P4-5** (MED) `scripts/build_clinvar_aa_reference.sh:76-78,110-116` + `run_annotation.sh:246-247` — zero pathogenic-missense records → empty reference written, exit 0, release stamped; never rebuilt for the life of that release [SH-11]
- [x] **P4-6** (MED) `pipeline/reduce_vep_to_aa_reference.py:35-36` — headerless input → silent empty reference, exit 0 (same end state) [CORE-18]
- [x] **P4-7** (MED) `pipeline/clinvar_aa_match.py:218-226` — bare `except Exception` around config resolution degrades to all-zero flag with exit 0 [CORE-16]

### 4C. Version/status checks that can't fail
- [x] **P4-8** (HIGH) `pipeline/check_dbnsfp_version.py:23-24,111-123` — unparseable release page indistinguishable from "up to date"; report a distinct `latest_unknown`/`online_error` state [AUX-H3]
- [x] **P4-9** (HIGH) `local_service/workbench_service.py:3029-3031` — `not paths or all(...)`: unconfigured annotation source reports as installed [SVC-9]
- [x] **P4-10** (HIGH) `pipeline/screen_ccre_dataset.py:271-281` — failed ENCODE lookup cached as empty metadata with `"complete": true`; transient network failure becomes permanent silent exclusion [CORE-11]
- [x] **P4-11** (MED) `pipeline/screen_immune_curation.py:293-302` — metadata cache not invalidated on policy change, then re-stamped with the *new* policy sha, defeating the integrity check [CORE-14]

### 4D. QC report accuracy
- [x] **P4-12** (MED) `pipeline/annotation_qc.py:263-270` — deliberate PTC skips counted as missing coverage → false WARN + bogus remediation list [CORE-12]
- [x] **P4-13** (MED) `pipeline/annotation_qc.py:205-212` — critical dbNSFP field outside `columns` → permanent 0% WARN; validate critical ⊆ configured [CORE-17]
- [x] **P4-14** (LOW) `pipeline/annotation_qc.py:513-521` — promoterAI status excluded from `overall_status` [CORE-20]
- [x] **P4-15** (MED) `pipeline/write_liftover_qc.py:66-67,82` — missing provenance tag silently substitutes allele-record count for `lifted_records`; should be an error [AUX-M3]
- [x] **P4-16** (MED) `pipeline/write_liftover_qc.py:68-76` — post-norm left-alignment conflated with assembly allele change in QC metrics [AUX-M4]
- [x] **P4-17** (LOW) `pipeline/classify_liftover_records.py:190,207-208` — accounting invariant unreachable; exit 2 gives false assurance [AUX-L1]
- [x] **P4-18** (LOW) `pipeline/classify_liftover_records.py:78,138-146` — half-missing source allele passes sentinel check, perturbing the reconciliation gate [AUX-L2]

### 4E. UI failure-state honesty
- [x] **P4-19** (MED) `webui/app/VariantWorkbench.tsx:780-785,727-778` — SCREEN filter silently passes everything while loading and after a failure, with checkbox still checked [UI-14]
- [x] **P4-20** (MED) `webui/app/local-service.ts:919-926` — `request()` parses JSON before checking `response.ok`; every non-JSON failure surfaces as parse noise [UI-16]

### 4F. VEP plugins
- [x] **P4-21** (HIGH) `docker/PromoterAI.pm:109-114` — no chr-stripping (unlike LoGoFunc.pm:83): chr-prefixed VCF → every lookup empty, no warning; insertion swap per D5 [SH-8]
- [x] **P4-22** (MED) `docker/LoGoFunc.pm:82-84,139` — query contig normalised but table contig stored verbatim and never compared; future chr-prefixed table fails silently [SH-15]

---

## Phase 5 — Long tail (mediums/lows, hygiene, cleanup)

> **Status (2026-08-08): complete** on `fix/phase-5-long-tail`, with two
> deliberate remainders. (1) **P5-5 / D6**: write_run_manifest.py's handle
> leak is fixed but the wire-into-driver-or-delete decision stays open.
> (2) **P5-39 / D7**: lint debt reduced 212→210 errors (rules-of-hooks,
> unescaped entity, and stale directives fixed); the remaining 210 are
> exclusively react-hooks/refs (203) and set-state-in-effect (7) — new
> React-compiler-era rules needing architectural refactors, per the D7
> baseline-vs-rewrite decision. Notable choices: update_clingen_erepo.sh
> now always uses the bundled FASTA extractor (host-samtools branch
> removed); the inert config keys liftover.engine and region.source were
> dropped; client list limits raised to the server cap (5000);
> bgzfChunks streams BGZF in bounded slices instead of materializing the
> file. Full sweep green incl. perl -c on both plugins via stub modules.

### 5A. SQLite / resource handling
- [x] **P5-1** (MED) `local_service/screen_context.py:108` + `gene_knowledge.py:456,468` — path interpolated into sqlite `file:` URI unescaped; `?`/`#`/`%` break or redirect the open [SVC-20]
- [x] **P5-2** (MED) `pipeline/clingen_erepo_annotate.py:32-35` — same URI defect; creates stray zero-byte files [AUX-M1]
- [x] **P5-3** (MED) `local_service/gene_knowledge.py:456,468,408` — `with sqlite3.connect(...)` leaks connections (2 fds per gene lookup) → EMFILE in long-lived service [SVC-19]
- [x] **P5-4** (LOW) `pipeline/prepare_promoterai.py:61-67,105-106,249-252` — `open_text` gzip raw handle never closed by callers [AUX-L3]
- [x] **P5-5** (LOW) `pipeline/write_run_manifest.py:81` — bare `open().read()`; module also has no caller [AUX-L5] *(per D6)*

### 5B. Service mediums/lows
- [x] **P5-6** (HIGH, needs domain confirmation) `local_service/screen_context.py:300` — tissue matrix stride from catalog row count, not stored `shape[1]`; misaligned reads if bundle skews [SVC-6]
- [x] **P5-7** (MED) `local_service/workbench_service.py:1817-1818` — directory branch hardcodes ClinVar filename for every resource's disk-space probe [SVC-23]
- [x] **P5-8** (MED) `local_service/workbench_service.py:2229-2232` — WGS review registry pruned to 30 without deleting backing files; links 404 for files still on disk [SVC-24]
- [x] **P5-9** (MED, needs domain confirmation) `scripts/build_workbench_reference_data.py:141` — required-column guard narrower than columns the parser reads [SVC-27]
- [x] **P5-10** (MED) `scripts/build_workbench_reference_data.py:218` — row-count sanity check runs after the output is already written; validate before write or write-to-temp [SVC-28]
- [x] **P5-11** (LOW) `local_service/gene_knowledge.py:257-258` — PMID join leaves empty middle elements (`123||456`) [SVC-30]
- [x] **P5-12** (LOW) `local_service/phenotype_store.py:319` — `header_row=0` treated as absent (`or` on falsy); use `is None` [SVC-31]
- [x] **P5-13** (LOW) `local_service/workbench_service.py:2593-2596` — output extension check case-sensitive while input check isn't [SVC-32]

### 5C. Pipeline aux mediums/lows
- [x] **P5-14** (MED, needs domain confirmation) `pipeline/build_gene_tss.py:59,69-71,97` — version-stripped gene IDs silently overwrite on collision; log collisions or key on (gene_id, chrom) [AUX-M5]
- [x] **P5-15** (MED) `pipeline/cell_ontology.py:25-28,55-57,78-80` — `ontology_ancestors` returns dangling parent IDs (obsolete/UBERON/GO) absent from `terms` [AUX-M6]
- [x] **P5-16** (MED) `pipeline/prepare_clingen_erepo.py:344-346` — ZeroDivisionError when every export row is retracted; neighbouring code already guards with `max(1,…)` [AUX-M7]
- [x] **P5-17** (LOW) `pipeline/extract_fasta_regions.py:22-29,43-55` — duplicate region lines → misleading byte-count abort; dedupe or report [AUX-L4]
- [x] **P5-18** (LOW) `pipeline/validate_vep_output.py:18` (+ `validate_regression_annotations.py:38`) — CSQ header matched by loose prefix; align on `##INFO=<ID=CSQ,` [AUX-L6]

### 5D. Pipeline core mediums/lows
- [x] **P5-19** (MED) `pipeline/screen_ccre_dataset.py:768-770` — rtree stores coords as float32 (exact only to 2^24); use `rtree_i32` or document superset-filter contract [CORE-15]
- [x] **P5-20** (LOW) `pipeline/clinvar_aa_match.py:136` — `csq_present` measures reference-set emptiness, not CSQ presence [CORE-21]
- [x] **P5-21** (LOW, needs domain confirmation) `screen_ccre_dataset.py:857` vs `local_service/ccre_context.py:67` — two live cCRE coordinate conventions (0-based catalog / 1-based context); record convention in metadata [CORE-22]

### 5E. Scripts / config lows
- [x] **P5-22** (MED) `scripts/update_clingen_erepo.sh:70-77` — samtools `-r` vs Python fallback: asymmetric failure modes; diff the two extractors' output on a fixture [SH-14]
- [x] **P5-23** (MED) `scripts/install_recommended_datasets.sh:43-44` — `VEP_RELEASE` missing the `:-113` default every sibling script applies [SH-18]
- [x] **P5-24** (LOW, needs domain confirmation) `scripts/preflight.sh:297` — `bcftools plugin -l | grep -qx liftover` may never match (format check); verify against the container [SH-19]
- [x] **P5-25** (LOW) `scripts/lib.sh:61,68-69` — 5 of 6 caller scripts never set/export `IMAGE`/`RUNTIME`; container fallback ignores configured runtime on standalone invocation [SH-20]
- [x] **P5-26** (LOW) `config/annotation.config.yaml:52` — `liftover.engine` read by nothing; wire or drop [SH-21]
- [x] **P5-27** (LOW) `config/annotation.config.yaml:88` — `region.source` read by nothing; documented switch unimplemented [SH-22]

### 5F. Static-analysis hygiene (from static_findings.md; no behavioral change expected)
- [x] **P5-28** (HIGH-fragility) `local_service/cohort_store.py` — 12 B023 late-binding closure sites (lines 1630-1699); bind via default args [ST-H2]
- [x] **P5-29** `pipeline/clinvar_aa_match.py:152` — same B023 pattern on `fields` [ST-H3]
- [x] **P5-30** `pipeline/screen_immune_curation.py:670,672` — same B023 pattern in `matches()` [ST-M2]
- [x] **P5-31** (MED-verify) `pipeline/loftee_ptc_50bp.py:649` — `chrom` unpacked and unused in PTC re-eval path; verify not a dropped contig lookup, else rename `_chrom` [ST-M1]
- [x] **P5-32** Unused imports/vars: `sample_library.py:12` shutil, `screen_ccre_dataset.py:22,28` os/sys, `build_workbench_reference_data.py:19` defaultdict, `test_clingen_erepo.py:7` annotate_main (placeholder? no test exercises CLI main), `build_coding_bed.sh:64` FAI (dropped feature?), `run_annotation.sh:56` ORIGINAL_INPUT [ST-L1]
- [x] **P5-33** `scripts/start_workbench.sh:88` — SC2155 declare/assign split [ST-L2]
- [x] **P5-34** `raise` without `from` ×3 (B904): `cohort_store.py:199`, `logofunc_dataset.py:289`, `prepare_promoterai.py:376` [ST-L3]
- [x] **P5-35** `build_vep_command.py:294` — unused loop var `label` (B007) [ST-L5]

### 5G. WebUI perf/robustness lows + lint debt
- [x] **P5-36** (LOW) `webui/app/VariantWorkbench.tsx:3578-3584` — bare filename `slice(0,-1)` truncates into a directory name [UI-19]
- [x] **P5-37** (LOW) `webui/app/vcf.ts:737-753,843,897` — every VCF read twice; BGZF fully materialized in memory (whole-genome tab OOM) [UI-20]
- [x] **P5-38** Silent truncation: `getSampleLibrary` limit 1000 / `getCohortSamples` 500 vs server cap 5000, no `truncated` flag (webui report, latent note)
- [x] **P5-39** eslint: 212 errors in `VariantWorkbench.tsx` incl. one `rules-of-hooks` violation (hook in callback, line 2799) [B-5] *(per D7)*

---

## Docker-gated verification — COMPLETE (2026-08-08)

All previously unverifiable items were executed on this host via Docker
Desktop 29.4.1 using `scripts/verify_container_stack.sh` (added for repeatable
release checks; `--quick` runs the container-only stages in seconds):

- [x] `scripts/run_annotation_regression.sh` — **PASS** (8 pass / 0 fail /
  4 skip; skips are the optional LoGoFunc + PromoterAI datasets, not
  installed on this host). Annotation completeness certificate: PASS.
- [x] in-container `perl -c` on both plugins against the real Bio::EnsEMBL
  modules — **both syntax OK** (image rebuilt 2026-08-08 so the P4-21/22
  plugin fixes are baked in; the script's image-drift stage now guards this).
- [x] `local_service/test_workbench_service.py` loopback tests — pass on this
  host (44+1 tests; confirmed during Phase 0).
- [x] SH-19 `bcftools plugin -l` format — **settled: false alarm.** The real
  output lists the bare plugin name `liftover` on its own line, so even the
  original `grep -qx` would have matched; the shipped `grep -qw` is simply
  more format-tolerant.
## Definition of done (from baseline_report.md)

- pytest ≥175/178 green with the screen-cCRE module collectable (the 2 loopback tests pass on an unsandboxed host)
- eslint rc=0 or an agreed suppression baseline (D7)
- `test_logofunc_dataset.py` / `test_prepare_promoterai.py` actually execute their tests
- Both shell-test assertions live (fail when their guarded feature regresses)
- Every phase-1/2 fix carries a regression test derived from the audit's runtime probes
