#!/usr/bin/env python3
"""Unit tests for build_vep_command.py — run with `python -m pytest` or directly."""
import json
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from build_vep_command import build_vep_command  # noqa: E402


def _touch(d, name):
    p = os.path.join(d, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


def _full_cfg(root):
    """A config with every source enabled and all files present under `root`."""
    j = lambda *p: os.path.join(root, *p)
    for rel in [
        "vep_cache/.ok", "fasta/genome.fa.gz",
        "dbnsfp/dbNSFP5.3.1a_grch38.gz",
        "loftee/human_ancestor.fa.gz", "loftee/loftee.sql",
        "loftee/gerp.bw", "spliceai/snv.vcf.gz", "spliceai/indel.vcf.gz",
        "cadd/whole_genome_SNVs.tsv.gz", "cadd/whole_genome_SNVs.tsv.gz.tbi",
        "cadd/gnomad.genomes.r4.0.indel.tsv.gz", "cadd/gnomad.genomes.r4.0.indel.tsv.gz.tbi",
        "promoterai/promoterai_scores.tsv.gz", "promoterai/promoterai_transcripts.tsv",
        "custom/rm.bed.gz", "custom/segdup.bed.gz",
        "clinvar/clinvar.vcf.gz", "logofunc/LoGoFunc.csv.gz",
        "logofunc/LoGoFunc.csv.gz.tbi",
        "funcvep/funcvep_scores.grch38.tsv.gz",
        "funcvep/funcvep_scores.grch38.tsv.gz.tbi",
        "funcvep/funcvep.manifest.json",
    ]:
        _touch(root, rel)
    with open(j("funcvep/funcvep_scores.grch38.tsv.gz"), "wb") as handle:
        handle.write(b"score")
    with open(j("funcvep/funcvep_scores.grch38.tsv.gz.tbi"), "wb") as handle:
        handle.write(b"index")
    with open(j("funcvep/funcvep.manifest.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "manifest_schema": "guide-iei.indexed-scores/v1",
            "resource": {"id": "funcvep", "name": "FuncVEP", "release": "test"},
            "assembly": "GRCh38",
            "applicability": {"consequences": ["missense_variant"]},
            "table": {
                "columns": [
                    "chrom", "position", "reference", "alternate",
                    "ensembl_gene_id", "FuncVEP_CTI", "FuncVEP_CTE", "FuncVEP_SP",
                ],
            },
            "match": {
                "required": ["allele", "ensembl_gene_id"],
                "dimensions": {
                    "allele": {
                        "chrom": "chrom", "position": "position",
                        "reference": "reference", "alternate": "alternate",
                    },
                    "ensembl_gene_id": {"column": "ensembl_gene_id"},
                },
            },
            "outputs": [
                {
                    "id": field,
                    "column": field,
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "direction": "higher_is_more_functionally_damaging",
                    "description": field,
                }
                for field in ("FuncVEP_CTI", "FuncVEP_CTE", "FuncVEP_SP")
            ],
            "provenance": {
                "match": "FuncVEP_match",
                "match_status": "FuncVEP_match_status",
                "source_target": "FuncVEP_source_gene",
                "allele_available": "FuncVEP_allele_available",
            },
            "license": {"acknowledged_by_user": True},
            "files": {
                "data": {
                    "name": "funcvep_scores.grch38.tsv.gz",
                    "size": len(b"score"),
                    "mtime_ns": os.stat(j("funcvep/funcvep_scores.grch38.tsv.gz")).st_mtime_ns,
                    "sha256": hashlib.sha256(b"score").hexdigest(),
                },
                "index": {
                    "name": "funcvep_scores.grch38.tsv.gz.tbi",
                    "size": len(b"index"),
                    "mtime_ns": os.stat(j("funcvep/funcvep_scores.grch38.tsv.gz.tbi")).st_mtime_ns,
                    "sha256": hashlib.sha256(b"index").hexdigest(),
                },
            },
        }, handle)
    return {
        "reference": {"species": "homo_sapiens", "assembly": "GRCh38",
                      "vep_cache_dir": j("vep_cache"),
                      "fasta": {"enabled": True, "required": True, "path": j("fasta/genome.fa.gz")}},
        "run": {"fork": 8, "buffer_size": 5000, "force_overwrite": True},
        "output": {"format": "vcf", "compress": "bgzip", "vep_stats": True},
        "core": {
                 "pick": True, "pick_flag": "--flag_pick_allele_gene",
                 "pick_order": ["mane_select", "mane_plus_clinical", "canonical",
                                "appris", "tsl", "biotype", "ccds", "rank", "length"],
                 "symbol": True, "hgvs": True, "numbers": True,
                 "canonical": True, "appris": True, "tsl": True, "ccds": True,
                 "mane": True, "allele_number": True,
                 "biotype": True, "sift": "p", "polyphen": "p",
                 "af_gnomade": True, "af_gnomadg": True, "max_af": True},
        "plugins": {
            "dbNSFP": {"enabled": True, "version": "5.3.1a",
                       "path": j("dbnsfp/dbNSFP5.3.1a_grch38.gz"),
                       "columns": ["CADD_phred", "CADD_raw", "REVEL_score", "AlphaMissense_score",
                                   "SIFT_pred", "Polyphen2_HDIV_pred"]},
            "LoF": {"enabled": True, "loftee_path": "auto",
                    "human_ancestor_fa": j("loftee/human_ancestor.fa.gz"),
                    "conservation_file": j("loftee/loftee.sql"),
                    "gerp_bigwig": j("loftee/gerp.bw")},
            "SpliceAI": {"enabled": True, "snv": j("spliceai/snv.vcf.gz"),
                         "indel": j("spliceai/indel.vcf.gz")},
            "CADD_WGS": {
                "enabled": True,
                "snv": j("cadd/whole_genome_SNVs.tsv.gz"),
                "indels": j("cadd/gnomad.genomes.r4.0.indel.tsv.gz"),
            },
            "PromoterAI": {
                "enabled": True,
                "file": j("promoterai/promoterai_scores.tsv.gz"),
                "transcript_map": j("promoterai/promoterai_transcripts.tsv"),
            },
            "LoGoFunc": {
                "enabled": True,
                "file": j("logofunc/LoGoFunc.csv.gz"),
            },
            "FuncVEP": {
                "enabled": True,
                "file": j("funcvep/funcvep_scores.grch38.tsv.gz"),
                "manifest": j("funcvep/funcvep.manifest.json"),
            },
        },
        "custom_tracks": {
            "RepeatMasker": {"enabled": True, "file": j("custom/rm.bed.gz"),
                             "short_name": "RepeatMasker", "format": "bed"},
            "SegDup": {"enabled": True, "file": j("custom/segdup.bed.gz"),
                       "short_name": "SegDup", "format": "bed"},
            "ClinVar": {"enabled": True, "file": j("clinvar/clinvar.vcf.gz"),
                        "short_name": "ClinVar", "format": "vcf", "type": "exact",
                        "coords": 0, "fields": ["CLNSIG", "CLNSIGCONF", "CLNREVSTAT", "CLNDN"]},
        },
    }


def test_avi_custom_track_is_exact_allele_vcf_not_a_transcript_plugin(tmp_path):
    from pathlib import Path
    from avi_dataset import SCHEMA, RELEASE, EXPECTED_ROWS, digest
    cfg = _full_cfg(str(tmp_path))
    source = _touch(str(tmp_path), "avi/avi.grch38.vcf.gz")
    _touch(str(tmp_path), "avi/avi.grch38.vcf.gz.tbi")
    manifest_path = Path(tmp_path) / "avi/manifest.json"
    files = {}
    for path in (Path(source), Path(source + ".tbi")):
        files[path.name] = {"size":path.stat().st_size,"mtime_ns":path.stat().st_mtime_ns,"sha256":digest(path)}
    manifest_path.write_text(json.dumps({"schema":SCHEMA,"release":RELEASE,"assembly":"GRCh38",
                                         "rows":EXPECTED_ROWS,"positions":EXPECTED_ROWS//3,"files":files}))
    cfg["custom_tracks"]["AlphaGenomeAVI"] = {
        "enabled": True, "required": False, "file": source,
        "manifest": str(manifest_path),
        "short_name": "AlphaGenomeAVI", "format": "vcf", "type": "exact",
        "coords": 0, "fields": ["raw", "phred"],
    }
    plan = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False)
    assert not plan.errors, plan.errors
    expected = f"file={os.path.realpath(source)},short_name=AlphaGenomeAVI,format=vcf,type=exact,coords=0,fields=raw%phred"
    assert expected in plan.argv
    assert plan.argv[plan.argv.index(expected) - 1] == "--custom"
    assert not any("IndexedScores" in arg and "avi" in arg.lower() for arg in plan.argv)
    cfg["custom_tracks"]["AlphaGenomeAVI"]["manifest"] = ""
    missing = build_vep_command(cfg,"in.vcf.gz","out.vcf.gz",container=False,check_exists=False)
    assert not any("AlphaGenomeAVI" in arg for arg in missing.argv)
    assert any("manifest" in w for w in missing.warnings)
    cfg["custom_tracks"]["AlphaGenomeAVI"]["manifest"] = str(manifest_path)
    # Same-size data replacement must fail the always-hash job-start check.
    Path(source).write_bytes(b'changed')
    broken = build_vep_command(cfg,"in.vcf.gz","out.vcf.gz",container=False,verify_integrity=True)
    assert not any("AlphaGenomeAVI" in arg for arg in broken.argv)
    cfg["custom_tracks"]["AlphaGenomeAVI"]["enabled"] = False
    disabled = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False)
    assert not any("AlphaGenomeAVI" in arg for arg in disabled.argv)


def test_full_stack_native(tmp_path):
    """Every source present -> every flag emitted, no warnings/errors."""
    cfg = _full_cfg(str(tmp_path))
    plan = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False)
    s = plan.command_string()
    assert not plan.errors, plan.errors
    assert not plan.warnings, plan.warnings
    # output format preserves zygosity
    assert "--vcf" in plan.argv and "--tab" not in plan.argv
    assert "--compress_output" in plan.argv
    # core
    for flag in ["--offline", "--cache", "--flag_pick_allele_gene", "--pick_order",
                 "--symbol", "--hgvs", "--numbers", "--canonical", "--appris",
                 "--tsl", "--ccds", "--mane", "--allele_number",
                 "--af_gnomade", "--af_gnomadg", "--max_af"]:
        assert flag in plan.argv, flag
    pick_order = plan.argv[plan.argv.index("--pick_order") + 1]
    assert pick_order == (
        "mane_select,mane_plus_clinical,canonical,appris,tsl,"
        "biotype,ccds,rank,length"
    )
    assert "--sift" in plan.argv and "p" in plan.argv
    # dbNSFP transcript-specific and allele-level registry fields are emitted
    # by separate plugin instances with disjoint output columns.
    dbnsfp = [a for a in plan.argv if a.startswith("dbNSFP,")]
    assert len(dbnsfp) == 2, dbnsfp
    transcript_dbnsfp = next(
        spec for spec in dbnsfp if "transcript_match=1" in spec
    )
    allele_dbnsfp = next(spec for spec in dbnsfp if "consequence=ALL" in spec)
    assert "pep_match=0" in allele_dbnsfp
    assert "REVEL_score" in transcript_dbnsfp
    assert "AlphaMissense_score" in transcript_dbnsfp
    assert "CADD_phred" not in transcript_dbnsfp
    assert "CADD_phred" in allele_dbnsfp and "CADD_raw" in allele_dbnsfp
    assert any(a.startswith("LoF,loftee_path:$LOFTEE_DIR") for a in plan.argv)
    assert any(a.startswith("SpliceAI,snv=") and "indel=" in a for a in plan.argv)
    assert any(
        a.startswith("CADD,snv=") and "indels=" in a for a in plan.argv
    )
    assert any(a.startswith("PromoterAI,file=") and "transcript_map=" in a for a in plan.argv)
    assert any(a.startswith("LoGoFunc,file=") for a in plan.argv)
    assert any(
        a.startswith("IndexedScores,file=")
        and ",manifest=" in a
        and a.endswith(",resource=funcvep")
        for a in plan.argv
    )
    # custom tracks — RepeatMasker, SegDup and ClinVar
    customs = [plan.argv[i + 1] for i, a in enumerate(plan.argv) if a == "--custom"]
    assert len(customs) == 3, customs
    clinvar = [c for c in customs if "short_name=ClinVar" in c][0]
    assert "fields=CLNSIG%CLNSIGCONF%CLNREVSTAT%CLNDN" in clinvar
    assert "type=exact" in clinvar and "coords=0" in clinvar
    # the shell-quoted rendering starts with the vep executable
    assert s.split()[0] == "vep", s


def test_dbnsfp_registry_scopes_partition_columns_without_duplicates(tmp_path):
    cfg = _full_cfg(str(tmp_path))
    cfg["plugins"]["dbNSFP"]["columns"] += [
        "GERP++_RS",
        "phyloP100way_vertebrate",
        "phastCons100way_vertebrate",
        "MutationTaster_score",
        "MutationTaster_pred",
        "CADD_phred",  # duplicate configuration must not duplicate CSQ output
        "REVEL_score",
    ]
    plan = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False)
    assert not plan.errors
    assert any("duplicates" in warning for warning in plan.warnings)

    specs = [value for value in plan.argv if value.startswith("dbNSFP,")]
    assert len(specs) == 2, specs
    transcript = next(spec for spec in specs if "transcript_match=1" in spec)
    allele = next(spec for spec in specs if "consequence=ALL" in spec)
    assert "pep_match=0" in allele

    expected_allele = {
        "CADD_phred",
        "CADD_raw",
        "GERP++_RS",
        "phyloP100way_vertebrate",
        "phastCons100way_vertebrate",
        "MutationTaster_score",
        "MutationTaster_pred",
    }
    for field in expected_allele:
        assert field in allele
        assert field not in transcript
    assert "REVEL_score" in transcript and "REVEL_score" not in allele

    configured_fields = set(cfg["plugins"]["dbNSFP"]["columns"])
    emitted_fields = [
        token
        for spec in specs
        for token in spec.split(",")[1:]
        if token in configured_fields
    ]
    assert len(emitted_fields) == len(set(emitted_fields)), emitted_fields


def test_optional_cadd_requires_both_data_files_and_indexes(tmp_path):
    cfg = _full_cfg(str(tmp_path))
    (tmp_path / "cadd" / "gnomad.genomes.r4.0.indel.tsv.gz.tbi").unlink()
    plan = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False)
    assert not plan.errors
    assert any("CADD_WGS.indels" in warning and "index" in warning for warning in plan.warnings)
    assert not any(value.startswith("CADD,") for value in plan.argv)


def test_required_spliceai_snv_only(tmp_path):
    """The default public dataset is SNV-only and must not imply an indel file."""
    cfg = _full_cfg(str(tmp_path))
    cfg["plugins"]["SpliceAI"] = {
        "enabled": True,
        "required": True,
        "snv": str(tmp_path / "spliceai" / "snv.vcf.gz"),
    }
    plan = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False)
    spliceai = [a for a in plan.argv if a.startswith("SpliceAI,")]
    assert len(spliceai) == 1
    assert ",snv=" in spliceai[0]
    assert "indel=" not in spliceai[0]
    assert not plan.errors, plan.errors


def test_core_only(tmp_path):
    """All plugins/customs disabled -> only core flags, no plugin/custom args."""
    cfg = _full_cfg(str(tmp_path))
    for p in cfg["plugins"].values():
        p["enabled"] = False
    for c in cfg["custom_tracks"].values():
        c["enabled"] = False
    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert "--plugin" not in plan.argv
    assert "--custom" not in plan.argv
    assert "--flag_pick_allele_gene" in plan.argv and "--vcf" in plan.argv
    assert not plan.errors


def test_missing_file_skipped(tmp_path):
    """A missing optional file is skipped with a warning, run still valid."""
    cfg = _full_cfg(str(tmp_path))
    os.remove(cfg["plugins"]["dbNSFP"]["path"])       # delete dbNSFP
    os.remove(cfg["plugins"]["LoGoFunc"]["file"])  # delete LoGoFunc
    os.remove(cfg["plugins"]["FuncVEP"]["file"] + ".tbi")  # incomplete FuncVEP
    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not plan.errors
    assert any("dbNSFP" in w for w in plan.warnings)
    assert any("LoGoFunc" in w for w in plan.warnings)
    assert any("FuncVEP" in w and "index" in w for w in plan.warnings)
    assert not any(a.startswith("dbNSFP,") for a in plan.argv)
    assert not any(a.startswith("LoGoFunc,") for a in plan.argv)
    assert not any(a.startswith("IndexedScores,") for a in plan.argv)
    customs = [plan.argv[i + 1] for i, a in enumerate(plan.argv) if a == "--custom"]
    assert len(customs) == 3


def test_invalid_funcvep_manifest_is_never_emitted_to_vep(tmp_path):
    cfg = _full_cfg(str(tmp_path))
    manifest = cfg["plugins"]["FuncVEP"]["manifest"]
    with open(manifest, "w", encoding="utf-8") as handle:
        json.dump({"resource": {"id": "funcvep"}}, handle)

    optional = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not optional.errors
    assert any("FuncVEP.manifest" in warning for warning in optional.warnings)
    assert not any(value.startswith("IndexedScores,") for value in optional.argv)

    cfg["plugins"]["FuncVEP"]["required"] = True
    required = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert any("FuncVEP.manifest" in error for error in required.errors)
    assert not any(value.startswith("IndexedScores,") for value in required.argv)


def test_generic_manifest_metric_contract_mismatches_stop_startup(tmp_path):
    cases = (
        ("type", "type", "integer", "type 'integer'"),
        ("range", "maximum", 2, "range [0, 2]"),
        ("direction", "direction", "lower", "direction 'lower'"),
    )
    for label, key, value, expected in cases:
        root = tmp_path / label
        cfg = _full_cfg(str(root))
        manifest_path = cfg["plugins"]["FuncVEP"]["manifest"]
        with open(manifest_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["outputs"][0][key] = value
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
        assert not plan.errors, label
        assert any(
            "FuncVEP_CTI" in warning
            and expected in warning
            and "registry metric 'funcvep.cti'" in warning
            for warning in plan.warnings
        ), (label, plan.warnings)
        assert not any(value.startswith("IndexedScores,") for value in plan.argv)


def test_generic_manifest_rejects_a_declared_stale_binary_threshold(tmp_path):
    cfg = _full_cfg(str(tmp_path))
    manifest_path = cfg["plugins"]["FuncVEP"]["manifest"]
    with open(manifest_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["outputs"][0]["binary_classification"] = {
        "threshold": 0.521,
        "comparison": "greater_than_or_equal",
        "positive_label": "Damaging",
        "negative_label": "Neutral",
        "threshold_set": "obsolete preprint threshold",
        "source_url": "https://example.test/obsolete",
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not plan.errors
    assert any(
        "FuncVEP_CTI" in warning
        and "binary classification does not match registry metric 'funcvep.cti'"
        in warning
        for warning in plan.warnings
    ), plan.warnings
    assert not any(value.startswith("IndexedScores,") for value in plan.argv)


def test_generic_manifest_provenance_role_mismatch_is_startup_error(tmp_path):
    cfg = _full_cfg(str(tmp_path))
    cfg["plugins"]["FuncVEP"]["required"] = True
    manifest_path = cfg["plugins"]["FuncVEP"]["manifest"]
    with open(manifest_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    provenance = payload["provenance"]
    provenance["source_target"], provenance["allele_available"] = (
        provenance["allele_available"], provenance["source_target"]
    )
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert any(
        "provenance.source_target" in error
        and "role 'flag'" in error
        and "role 'provenance'" in error
        for error in plan.errors
    ), plan.errors
    assert not any(value.startswith("IndexedScores,") for value in plan.argv)


def test_generic_manifest_mtime_is_optional_and_checksum_resolves_mismatch(tmp_path):
    cfg = _full_cfg(str(tmp_path))
    manifest_path = cfg["plugins"]["FuncVEP"]["manifest"]
    score_path = cfg["plugins"]["FuncVEP"]["file"]

    with open(manifest_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["files"]["data"].pop("mtime_ns")
    payload["files"]["index"].pop("mtime_ns")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    without_mtime = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not without_mtime.errors
    assert any(value.startswith("IndexedScores,") for value in without_mtime.argv)

    # Restore timestamp metadata, then simulate a metadata-only copy. The
    # manifest hash still proves identity, so the plugin remains enabled.
    score_status = os.stat(score_path)
    payload["files"]["data"]["mtime_ns"] = score_status.st_mtime_ns
    payload["files"]["index"]["mtime_ns"] = os.stat(score_path + ".tbi").st_mtime_ns
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.utime(
        score_path,
        ns=(score_status.st_atime_ns, score_status.st_mtime_ns + 1_000_000),
    )
    copied = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not copied.errors
    assert any(value.startswith("IndexedScores,") for value in copied.argv)

    # A same-size content change cannot hide behind the timestamp fallback.
    with open(score_path, "wb") as handle:
        handle.write(b"scorf")
    damaged = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not damaged.errors
    assert any("sha256" in warning for warning in damaged.warnings)
    assert not any(value.startswith("IndexedScores,") for value in damaged.argv)


def test_strict_integrity_check_catches_a_same_size_same_mtime_change(tmp_path):
    # Review M8: the fast status check trusts name+size when the recorded
    # mtime matches (or is absent), so a content change that preserves both
    # passes it. The strict check run at job start always hashes.
    from indexed_scores import ManifestError, validate_manifest_files
    from pathlib import Path

    cfg = _full_cfg(str(tmp_path))
    manifest_path = cfg["plugins"]["FuncVEP"]["manifest"]
    score_path = cfg["plugins"]["FuncVEP"]["file"]
    with open(manifest_path, encoding="utf-8") as handle:
        payload = json.load(handle)

    # Intact dataset: both checks pass, strict emits the plugin.
    validate_manifest_files(payload, Path(score_path), Path(score_path + ".tbi"))
    validate_manifest_files(
        payload, Path(score_path), Path(score_path + ".tbi"), strict=True
    )
    strict_plan = build_vep_command(
        cfg, "in.vcf", "out.vcf", container=False, verify_integrity=True
    )
    assert not strict_plan.errors
    assert any(value.startswith("IndexedScores,") for value in strict_plan.argv)

    # Same-size overwrite with the original timestamp restored.
    before = os.stat(score_path)
    with open(score_path, "wb") as handle:
        handle.write(b"scorf")
    os.utime(score_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(score_path).st_size == before.st_size
    assert os.stat(score_path).st_mtime_ns == before.st_mtime_ns

    # Fast status check: still passes (by design — it is the cheap poll).
    validate_manifest_files(payload, Path(score_path), Path(score_path + ".tbi"))
    fast_plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert not fast_plan.errors
    assert any(value.startswith("IndexedScores,") for value in fast_plan.argv)

    # Strict verification: refuses the dataset, and the plan drops the plugin
    # with a warning naming the checksum.
    try:
        validate_manifest_files(
            payload, Path(score_path), Path(score_path + ".tbi"), strict=True
        )
    except ManifestError as error:
        assert "sha256" in str(error) and "strict" in str(error)
    else:
        raise AssertionError("strict verification accepted a changed file")
    strict_plan = build_vep_command(
        cfg, "in.vcf", "out.vcf", container=False, verify_integrity=True
    )
    assert not strict_plan.errors  # FuncVEP is optional in this fixture
    assert any("sha256" in warning for warning in strict_plan.warnings)
    assert not any(value.startswith("IndexedScores,") for value in strict_plan.argv)

    # A manifest without mtime_ns is accepted by the fast check on name+size
    # alone, and still refused by the strict check.
    payload["files"]["data"].pop("mtime_ns")
    validate_manifest_files(payload, Path(score_path), Path(score_path + ".tbi"))
    try:
        validate_manifest_files(
            payload, Path(score_path), Path(score_path + ".tbi"), strict=True
        )
    except ManifestError:
        pass
    else:
        raise AssertionError("strict verification accepted a changed file")


def test_required_missing_errors(tmp_path):
    """A missing REQUIRED file becomes an error (would abort the run)."""
    cfg = _full_cfg(str(tmp_path))
    os.remove(cfg["reference"]["fasta"]["path"])  # fasta is required:true
    plan = build_vep_command(cfg, "in.vcf", "out.vcf", container=False)
    assert any("fasta" in e for e in plan.errors)


def test_container_mounts(tmp_path):
    """Container mode maps ref paths under /refs and records bind-mounts."""
    cfg = _full_cfg(str(tmp_path))
    ind = str(tmp_path / "inp"); outd = str(tmp_path / "outp")
    os.makedirs(ind, exist_ok=True); os.makedirs(outd, exist_ok=True)
    plan = build_vep_command(cfg, os.path.join(ind, "in.vcf.gz"),
                             os.path.join(outd, "out.vcf.gz"), container=True)
    assert not plan.errors
    # all absolute args now point under /refs/, /work_in, /work_out
    for a in plan.argv:
        if a.startswith("/"):
            assert a.startswith(("/refs/", "/work_in", "/work_out")), a
    containers = {m.container for m in plan.mounts}
    assert "/work_in" in containers
    assert "/work_out" in containers
    # reference dirs mounted read-only; work_out mounted read-write
    assert any(m.container.startswith("/refs/") and m.mode == "ro" for m in plan.mounts)
    assert any(m.container == "/work_out" and m.mode == "rw" for m in plan.mounts)


def test_shared_io_dir_single_rw_mount(tmp_path):
    """When input and output share a dir, one rw /work_in mount covers both."""
    cfg = _full_cfg(str(tmp_path))
    d = str(tmp_path / "io")
    os.makedirs(d, exist_ok=True)
    plan = build_vep_command(cfg, os.path.join(d, "in.vcf.gz"),
                             os.path.join(d, "out.vcf.gz"), container=True)
    io_mounts = [m for m in plan.mounts if m.container in ("/work_in", "/work_out")]
    assert len(io_mounts) == 1
    assert io_mounts[0].container == "/work_in" and io_mounts[0].mode == "rw"
    # both -i and -o resolve under /work_in
    i_idx = plan.argv.index("-i"); o_idx = plan.argv.index("-o")
    assert plan.argv[i_idx + 1].startswith("/work_in/")
    assert plan.argv[o_idx + 1].startswith("/work_in/")


def test_unconfigured_cadd_path_is_not_emitted_as_cwd(tmp_path):
    # Audit repro (CORE-6): cadd.get(key, "") -> abspath("") == the current
    # working directory, emitted as a real `indels=<cwd>` argument under
    # --no-check. An unset path must count as missing.
    cfg = _full_cfg(str(tmp_path))
    del cfg["plugins"]["CADD_WGS"]["indels"]
    plan = build_vep_command(cfg, "in.vcf.gz", "out.vcf.gz", container=False,
                             check_exists=False)
    assert not plan.errors
    assert not [a for a in plan.argv if a.startswith("CADD,")]  # all-or-nothing kept
    assert not any("indels=" in a and os.getcwd() in a for a in plan.argv)
    assert any(
        "CADD_WGS.indels" in warning and "no path configured" in warning
        for warning in plan.warnings
    )


def test_unconfigured_funcvep_path_is_not_emitted_as_project_root(tmp_path):
    cfg = _full_cfg(str(tmp_path))
    del cfg["plugins"]["FuncVEP"]["file"]
    del cfg["plugins"]["FuncVEP"]["manifest"]
    plan = build_vep_command(
        cfg, "in.vcf.gz", "out.vcf.gz", container=True,
        check_exists=False, base_dir=str(tmp_path),
    )
    assert not plan.errors
    assert not any(value.startswith("IndexedScores,") for value in plan.argv)
    assert not any(mount.host == str(tmp_path.parent) for mount in plan.mounts)
    assert any(
        "FuncVEP.file" in warning and "no path configured" in warning
        for warning in plan.warnings
    )
    assert any(
        "FuncVEP.manifest" in warning and "no path configured" in warning
        for warning in plan.warnings
    )


def test_json_mode_prints_errors_to_stderr(tmp_path):
    # Audit repro (CORE-8): --json returned 2 with errors only in stdout,
    # which run_annotation.sh swallows into a command substitution; the
    # operator saw "see WARN/ERROR above" with nothing printed.
    import json
    import subprocess
    import yaml
    cfg = _full_cfg(str(tmp_path))
    os.remove(cfg["reference"]["fasta"]["path"])
    config_path = os.path.join(str(tmp_path), "config.yaml")
    with open(config_path, "w") as handle:
        yaml.safe_dump(cfg, handle)
    script = os.path.join(os.path.dirname(__file__), "..", "pipeline", "build_vep_command.py")
    result = subprocess.run(
        [sys.executable, script, "--config", config_path,
         "--input", "in.vcf.gz", "--output", "out.vcf.gz", "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "ERROR" in result.stderr, result.stderr
    assert json.loads(result.stdout)["errors"]


if __name__ == "__main__":
    import tempfile
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as td:
                fn(__import__("pathlib").Path(td))
            print(f"PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed")
