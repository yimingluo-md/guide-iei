# GUIDE-IEI — Medium genomics fixes (M1–M9, plus M36), 2026-09-07

Follow-up to the comprehensive review after a second reviewer verified the
nine "genomics semantics" Medium findings. Their verdicts held (all confirmed,
with four qualifications that corrected the review's own proposals); the
fixes below follow the corrected reasoning. Every code fix carries a
regression test that was confirmed to **fail on the pre-fix tree** (the
reviewer's own reproductions were re-run against the old code first) and pass
after.

Applied as **uncommitted** changes on top of your working tree (which still
carries the uncommitted H1–H11 batch on `ac7c010`). 28 files modified, 3 new
(`pipeline/promoterai_evidence.py`, `test/test_promoterai_evidence.py`,
`test/contracts/promoterai_evidence_cases.json`). Nothing staged or committed
by me.

## Reviewer verdicts on M1–M9 (recorded here; the review document itself was left unchanged)
A second reviewer re-checked M1–M9 against the current tree (including the uncommitted H-fixes) with synthetic reproductions. Outcome: **M2, M3, M5, M7 confirmed as bugs; M4, M6, M8 confirmed as documented limitations/gaps; M1 confirmed as a documentation gap with the wording corrected; M9 split into two separate issues.** Four qualifications changed what was proposed above, and the fixes follow them:
- **M1:** "every indel" was too broad — the optional CADD v1.7 dataset supplies indel scores; the gap is that the prediction controls and the Variant Review guide do not say what a threshold does with a missing score.
- **M2:** the code comment that a bare `|` "says nothing about phase between records" is itself too strong: VCF 4.2 §1.6.2 declares an implicit, contig-wide phase set for phased genotypes without PS. Parsing that declaration and trusting statistically phased blocks are separate questions; the module keeps the conservative POSSIBLE verdict for two hets without PS but now decides the hom-alt-partner case (cis by construction) before the phase-set gate.
- **M3:** the proposed arithmetic guard (`sum(Δlen) % 3 == 0` with a non-zero member) is insufficient — an in-frame −3 deletion plus a substitution (−3 + 0) passes it. The correct check is per contributor: each must itself be `frameshift_variant` on the haplotype's transcript (allele-attributed via `ALLELE_NUM`, since `norm -m -any` admits sibling ALTs), and only then is the combined consequence evaluated.
- **M4:** an explicit, explained skip first; full support must respect the GTF's annotated selenocysteine sites, never reinterpret every in-frame TGA. (Also: the failure was not silent — `PTC_calc_status` recorded it — but it was reported as a coverage gap rather than a deliberate skip.)
- **M9:** "no ploidy awareness" was overstated — haploid `1` is recognised; the gap is a diploid-encoded `1/1` on non-PAR X/Y. Genotypes must not be rewritten from `sex_at_birth` alone. The frequency mixing is real and is a labelling *and* filtering issue because VEP's `MAX_AF` spans 1000 Genomes and ESP as well as gnomAD.

All nine were then fixed as described above.

## What changed, by finding

**M2 — phased het + hom-alt was graded below the unphased het.**
`pipeline/haplotype_consequences.py`: `classify_phase` now decides "a single
non-homozygous variant next to homozygous-alt partners is in cis" *before* the
phase-set gate, so `0|1` without PS + `1/1` is `PARTIAL_CONFIRMED` like `0/1`
+ `1/1`. Two or more hets still need a shared, non-empty PS/PID. The comment
was corrected per the reviewer: VCF 4.2 §1.6.2 *does* declare an implicit
phase set for phased genotypes without PS; the module deliberately treats
that implicit block as unverified (switch errors, unknown caller) and reports
POSSIBLE rather than confirming cis or trans from it. Tests: two new
`classify_phase` cases (all het encodings × hom encodings, three-variant
mixes).

**M3 — frame restoration never checked that contributors were frameshifting.**
The reviewer was right that my proposed guard (`sum % 3 == 0` with a non-zero
member) was insufficient: −3 + 0 passes it. Now `restoring_events` takes a
per-allele frameshift index built from the annotated VCF's CSQ
(`parse_frameshift_transcripts`: `frameshift_variant` per allele via
`ALLELE_NUM` → `Allele` fallback, keyed raw + minimal, never broadcast across
sibling ALTs). A haplotype is a candidate only when ≥2 contributors are
themselves frameshifting **on the haplotype's transcript**; as a second,
independent guard the length changes of those contributors must sum to 0 mod
3 (only decisive when every such allele has a frame-breaking length change;
otherwise Haplosaurus stays the arbiter). `main` always supplies the index;
without a CSQ header nothing is confirmed (warned). New audit counters:
`insufficient_frameshift_contributors`,
`frame_arithmetic_mismatch_haplotypes`,
`frame_arithmetic_unverifiable_haplotypes`; header provenance gains
`ContributorCheck=per_allele_transcript_frameshift`. The reviewer's synthetic
case (in-frame deletion + substitution, both homozygous, sharing a record with
a frameshift sibling) now yields no status. Six new tests.

**M4 — selenoproteins.** As the reviewer recommended: an explicit, explained
skip, and no reinterpretation of every TGA. `loftee_ptc_50bp.py` reads the
Ensembl GTF `Selenocysteine` features (verified on your
`Homo_sapiens.GRCh38.113.gtf.gz`: 130 features, 88 transcripts); an in-frame
UGA is accepted as sense only at an annotated site (a site that is not
in-frame UGA is a model problem, `selenocysteine_site_not_uga` /
`_outside_frame`). Such transcripts get
`PTC_calc_status=selenoprotein_transcript_unsupported`, which `annotation_qc`
counts as a deliberate skip, not a coverage gap. Validated against the real
GTF + FASTA regions: 78 of the 88 selenoprotein transcripts (incl. the MANE
SELENON, GPX4, TXNRD1/2 models) now validate as intact and skip explicitly;
the other 10 are 3'/5'-incomplete models that fail for unrelated reasons.
Tests on plus and minus strand.

**M5 — three PromoterAI implementations.** One rule, one contract:
`pipeline/promoterai_evidence.py` (used by `cohort_store.annotation_from`,
`wgs_review.evaluate_record`, and `annotation_qc`'s field list) and
`promoterAiObservation` in `webui/app/vcf.ts`, both pinned to
`test/contracts/promoterai_evidence_cases.json` (14 cases run by
`test/test_promoterai_evidence.py` and `webui/tests/vcf.test.mjs`). A score
is usable only with the provenance the plugin writes beside it
(`PromoterAI_match` ∈ exact_version/stable_id/…, source transcript,
unambiguous TSS, strand); the score is the first present field in priority
order, never a max across aliases (it is signed). The reviewer's repro
(score-only 0.9) is now withheld by the cohort column, the browser **and**
the WGS prefilter route. Two existing fixtures that encoded the old
behaviour (a bare INFO `promoterAI=` score stored in the cohort column; the
prefilter retaining on a bare alias) were updated to carry provenance.

**M6 — dead `pathogenic_terms` knob.** `prepare_clinical_protein_catalog.py`
gains `load_pathogenic_labels(config)` (reads
`post_processing.clinical_protein_match.pathogenic_terms`, then the legacy
`clinvar_aa_match.pathogenic_terms`, else the built-in default — which is
byte-identical to today's hard-coded set), `--config` on `export`/`reduce`,
and a `labels` subcommand whose fingerprint is part of every catalog stamp so
a policy edit rebuilds the catalogs. Matching is whole-label after
normalisation, never substring; patterns are rejected; compound CLNSIG values
are excluded unless listed verbatim (documented in the config and
ANNOTATIONS.md). The manifest records the active labels. Six new tests.
**Side effect:** the stamp format changed, so the three protein-match
catalogs rebuild once on the next run.

**M7 — QC read a header phrase the matcher no longer writes.**
`annotation_qc` now accepts both "ClinVar snapshot …" and the legacy
"ClinVar release …". The new test generates the header with
`clinvar_aa_match.annotate` itself so the two cannot drift again.

**M8 — integrity check skipped SHA-256 when mtime matched.** Two explicit
tiers in `indexed_scores.validate_manifest_files`: the fast status check
(unchanged; dataset screen/preflight) and `strict=True` (always hashes both
files, uncached). `build_vep_command --verify-integrity` runs strict at job
start (`run_annotation.sh` passes it). Test: same-size overwrite with mtime
restored passes fast, fails strict, plugin dropped from the plan with a
checksum warning.

**M9 — two issues, as the reviewer split them.** (a) Frequency:
`gnomad_popmax` is now the first non-empty of {gnomAD popmax fields} →
{`MAX_AF`} → {gnomAD global AF}, never a maximum across them, with a new
`gnomad_popmax_source` column (`ALTER`-migrated; old rows NULL until
re-import) and the same preference in the WGS prefilter and the browser
(`populationFrequency`, `gnomadPopmaxSource`). The variant page labels the
value by source ("gnomAD popmax" / "Max observed AF (MAX_AF)" / "gnomAD global
AF"), the frequency help text explains the order, and the guide now says
plainly that the standard annotation's headline value **is** VEP's `MAX_AF`
(1000G/ESP/gnomAD). (b) Ploidy: no genotype rewriting from `sex_at_birth`. At
non-PAR X/Y loci only, the Cohort Search **Homozygous** and **Hemizygous**
filters each include the other class (query-time `SINGLE_COPY_LOCUS_SQL`;
rows keep their own zygosity); the select labels and guide say so. Query plan
test confirms no table scan. Four new tests.

**M1 — missing-score semantics documented** (wording corrected per the
reviewer: the CADD dataset does score indels). The Prediction-scores panel now
states that a threshold excludes unscored variants (unlike the frequency
filter) and lists coverage per predictor; `04-first-exome.md` and
`09-reading-a-variant.md` say the same.

**M36 (bonus, taken because M9 touched the same component).** The
`CcreContextPanel` props are now `refAllele`/`altAllele`; the prop literally
named `ref` was the single false positive behind 254 `react-hooks/refs`
warnings. That rule runs at full strength again (`eslint.config.mjs`
narrowed to `set-state-in-effect` only); lint is 0 errors / **23** warnings
(was 276).

## Verification (isolated copy, bcftools 1.19 installed)

- Python: 49/49 suites exit 0 (48 + the new `test_promoterai_evidence.py`),
  ≈516 tests including 34 new (plus 3 new browser tests).
- Shell: `test_dry_run` (exercises `--verify-integrity` and the catalog
  `labels` fingerprint path), `test_hts_helper`, `test_dataset_installer`,
  `test_download_dbnsfp`, `test_fetch_clinvar`, `test_macos_launcher`,
  `test_setup_privileged_consent` — all pass. `bash -n` clean; `shellcheck
  -S warning` shows only the same three pre-existing benign notes.
- Web UI: `npm test` 113 pass / 1 skip / 0 fail (the bundled reference data
  was included this time), `tsc --noEmit` clean, `eslint` 0 errors / 23
  warnings.
- `check_documentation.py` and `export-glossary.mjs --check` pass.
- Pre-fix reproductions (old tree): M2 → POSSIBLE_UNPHASED; M3 → CONFIRMED
  for in-frame del + SNV; M4 → `cds_internal_stop` for all 88 selenoprotein
  transcripts; M5 → cohort column 0.9 and prefilter retained; M7 → null
  release; M9 → 0.04 for popmax 0.001 + MAX_AF 0.04.

## Things to know

- Existing cohort databases: `gnomad_popmax_source` is NULL for rows indexed
  before this change (the UI then shows the generic "gnomAD popmax" label);
  the stored value itself was already the pooled maximum for those rows and
  is refreshed on re-import.
- Existing protein-match catalogs rebuild once (stamp format now includes the
  label fingerprint).
- `test/contracts/` is a new tracked directory; `test/fixtures/` is
  gitignored scratch, which is why the contract does not live there.
- Suggested commit grouping: M2+M3 (haplotype), M4 (PTC), M5 (promoterAI
  contract), M6 (catalog policy), M7+M8 (QC/integrity), M9+M1+M36 (frequency,
  ploidy, UI).
