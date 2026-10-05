"""Starter subset preparation tests use synthetic data, never patient VCFs."""
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys
import subprocess
import textwrap

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.starter_annotations import (
    Intervals, alphamissense_rows, annotation_regions, cadd_rows,
    indexed_manifest, mane_select, prepare, spliceai_rows, write_sorted,
    write_license_notices,
)


def text(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return path


def summary(tmp_path):
    return text(tmp_path, "mane.tsv", "#NCBI_GeneID\tEnsembl_nuc\tMANE_status\tsymbol\tGRCh38_chr\n"
                "1\tENST000001.2\tMANE Select\tPLUS\tNC_000001.11\n"
                "2\tENST000002.3\tMANE Select\tMINUS\tNC_000001.11\n"
                "3\tENST000003.1\tMANE Plus Clinical\tOTHER\tNC_000001.11\n"
                "4\tENST000004.1\tMANE Select\tPATCH\tNW_123.1\n")


def gtf(tmp_path):
    rows = []
    for tx, strand, exons in (("ENST000001.2", "+", [(10, 20), (31, 40)]),
                              ("ENST000002.3", "-", [(100, 110), (121, 130)])):
        for start, end in exons:
            attrs = f'transcript_id "{tx}"; gene_type "protein_coding";'
            rows.append(f"chr1\ttest\texon\t{start}\t{end}\t.\t{strand}\t.\t{attrs}\n")
            rows.append(f"chr1\ttest\tCDS\t{start + 2}\t{end - 2}\t.\t{strand}\t0\t{attrs}\n")
    return text(tmp_path, "mane.gtf", "".join(rows))


def am_file(tmp_path, name="am.tsv", rows=None):
    header = "#CHROM\tPOS\tREF\tALT\tgenome\ttranscript_id\tprotein_variant\tam_pathogenicity\tam_class\n"
    return text(tmp_path, name, header + (rows or
                "chr1\t12\tA\tG\thg38\tENST000001.1\tK2R\t0.8765\tlikely_pathogenic\n"))


def splice_file(tmp_path, rows):
    return text(tmp_path, "scores.vcf", "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" + rows)


def test_mane_select_excludes_plus_clinical_and_alternate_loci(tmp_path):
    assert set(mane_select(summary(tmp_path))) == {"ENST000001", "ENST000002"}


def test_splice_boundaries_are_intronic_on_both_strands(tmp_path):
    intervals, sites = annotation_regions(gtf(tmp_path), mane_select(summary(tmp_path)))
    assert {pos for chrom, pos in sites} == {21, 22, 29, 30, 111, 112, 119, 120}
    assert sites[("1", 21)] == {"PLUS"}
    assert sites[("1", 119)] == {"MINUS"}
    assert not intervals.contains("1", 20)
    assert not intervals.contains("1", 31)
    assert intervals.bases() == 8


def test_wrong_mane_version_cannot_supply_splice_boundaries(tmp_path):
    path = gtf(tmp_path)
    path.write_text(path.read_text().replace("ENST000001.2", "ENST000001.1"))
    with pytest.raises(ValueError, match="missing"):
        annotation_regions(path, mane_select(summary(tmp_path)))


def test_cds_union_has_no_splice_padding(tmp_path):
    intervals, _ = annotation_regions(gtf(tmp_path))
    assert intervals.contains("1", 12) and intervals.contains("1", 18)
    assert not intervals.contains("1", 11) and not intervals.contains("1", 19)
    union = Intervals({"1": [(0, 3), (2, 7), (7, 9), (15, 20)]})
    assert union.bases() == 14
    assert union.contains("1", 9) and not union.contains("1", 10)


def test_coding_scope_includes_separate_stop_codons_not_noncoding_genes(tmp_path):
    path = text(tmp_path, "coding.gtf",
                '1\ttest\tCDS\t10\t20\t.\t+\t0\tgene_biotype "protein_coding";\n'
                '1\ttest\tstop_codon\t21\t23\t.\t+\t0\tgene_biotype "protein_coding";\n'
                '1\ttest\tCDS\t30\t40\t.\t+\t0\tgene_biotype "pseudogene";\n')
    intervals, _ = annotation_regions(path)
    assert intervals.bases() == 14
    assert intervals.contains("1", 23)
    assert not intervals.contains("1", 24)
    assert not intervals.contains("1", 30)


def test_am_preserves_source_version_score_and_label(tmp_path):
    stats = Counter()
    rows = list(alphamissense_rows([am_file(tmp_path)], mane_select(summary(tmp_path)), stats))
    assert rows == [["1", "12", "A", "G", "ENST000001.1", "K2R", "0.8765", "likely_pathogenic", "ENST000001.2", "isoforms"]]
    assert stats["source_transcript_version_differs_from_mane"] == 1
    assert stats["mane_transcripts_without_source_scores"] == 1
    manifest = indexed_manifest("alphamissense", "test")
    assert manifest["match"]["required"] == ["allele", "ensembl_transcript_id", "protein_change"]


def test_am_wrong_assembly_fails(tmp_path):
    path = am_file(tmp_path)
    path.write_text(path.read_text().replace("hg38", "hg19"))
    with pytest.raises(ValueError, match="hg38"):
        list(alphamissense_rows([path], mane_select(summary(tmp_path)), Counter()))


@pytest.mark.parametrize("score", ["nan", "inf", "1.1", "-0.1"])
def test_invalid_am_scores_fail(tmp_path, score):
    path = am_file(tmp_path)
    path.write_text(path.read_text().replace("0.8765", score))
    with pytest.raises(ValueError, match="score outside"):
        list(alphamissense_rows([path], mane_select(summary(tmp_path)), Counter()))


def test_cadd_keeps_phred_precision_and_discards_raw_and_indels(tmp_path):
    path = text(tmp_path, "cadd.tsv", "## CADD v1.7\n#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED\n"
                "1\t12\tA\tG\t0.1\t23.456\n1\t12\tA\tAG\t0.1\t21\n1\t19\tA\tG\t0.1\t20\n")
    rows = list(cadd_rows(path, annotation_regions(gtf(tmp_path))[0], Counter()))
    assert rows == [["1", "12", "A", "G", "23.456"]]
    assert indexed_manifest("cadd", "1.7")["match"]["required"] == ["allele"]


def test_spliceai_preserves_zero_and_filters_overlapping_other_gene(tmp_path):
    path = splice_file(tmp_path, "1\t21\t.\tA\tG\t.\t.\tSpliceAI=G|OTHER|0.90|0.20|0.20|0.20|0|0|0|0,G|PLUS|0.00|0.00|0.00|0.00|-500|0|0|500;EXTRA=private\n")
    sites = annotation_regions(gtf(tmp_path), mane_select(summary(tmp_path)))[1]
    rows = list(spliceai_rows([path], sites, Counter()))
    assert rows[0][-1] == "SpliceAI=G|PLUS|0.00|0.00|0.00|0.00|-500|0|0|500"


def test_spliceai_rejects_wrong_allele_and_sample_columns(tmp_path):
    path = splice_file(tmp_path, "1\t21\t.\tA\tG\t.\t.\tSpliceAI=T|PLUS|0|0|0|0|0|0|0|0\n")
    with pytest.raises(ValueError, match="wrong-allele"):
        list(spliceai_rows([path], {("1", 21): {"PLUS"}}, Counter()))
    path.write_text(path.read_text().rstrip() + "\tGT\t0/1\n")
    with pytest.raises(ValueError, match="sites-only"):
        list(spliceai_rows([path], {("1", 21): {"PLUS"}}, Counter()))


def test_spliceai_accepts_pinned_gtf_name_for_same_exact_mane_transcript(tmp_path):
    annotation = gtf(tmp_path)
    annotation.write_text(annotation.read_text().replace('transcript_id "ENST000001.2";',
                          'transcript_id "ENST000001.2"; gene_name "ENSEMBL_OLD_NAME";'))
    sites = annotation_regions(annotation, mane_select(summary(tmp_path)))[1]
    assert sites[("1", 21)] == {"PLUS", "ENSEMBL_OLD_NAME"}
    source = splice_file(tmp_path, "1\t21\t.\tA\tG\t.\t.\tSpliceAI=G|ENSEMBL_OLD_NAME|0.2|0|0|0|1|0|0|0,G|NEIGHBOR|0.9|0|0|0|1|0|0|0\n")
    rows = list(spliceai_rows([source], sites, Counter()))
    assert rows[0][-1] == "SpliceAI=G|ENSEMBL_OLD_NAME|0.2|0|0|0|1|0|0|0"
    # Names are not transferred from a different transcript version.
    annotation.write_text(annotation.read_text().replace('ENST000001.2', 'ENST000001.1'))
    with pytest.raises(ValueError, match="missing"):
        annotation_regions(annotation, mane_select(summary(tmp_path)))


def test_preparation_atomically_publishes_indexed_resource(tmp_path):
    pysam = pytest.importorskip("pysam")
    source = am_file(tmp_path)
    output = tmp_path / "bundle"
    manifest = prepare("alphamissense", [source, source], output, release="test", mane_summary=summary(tmp_path))
    assert manifest["statistics"]["output_rows"] == 1
    assert manifest["statistics"]["identical_duplicates_removed"] == 1
    assert manifest["release_status"] == "development-not-for-distribution"
    assert str(tmp_path) not in json.dumps(manifest)
    notice = output / "LICENSE-NOTICES.json"
    identity = next(item for item in manifest["files"] if item["name"] == notice.name)
    assert identity["sha256"] == hashlib.sha256(notice.read_bytes()).hexdigest()
    assert identity["size_bytes"] == notice.stat().st_size
    with pysam.TabixFile(str(output / "alphamissense.tsv.gz")) as index:
        assert len(list(index.fetch("1", 11, 12))) == 1
    before = (output / "preparation.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        prepare("alphamissense", [source], output, release="test", mane_summary=summary(tmp_path))
    assert (output / "preparation.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["alphamissense", "cadd", "spliceai"])
def test_offline_notices_include_terms_attribution_and_modifications(tmp_path, kind):
    destination = tmp_path / "LICENSE-NOTICES.json"
    write_license_notices(kind, destination)
    notice = json.loads(destination.read_text())
    assert notice["resource"]["id"] == "starter_" + kind
    for field in ("attribution", "changes", "source_url", "source_notice"):
        assert notice["resource"][field]
    assert len(notice["license"]["text"]) > 1000
    assert notice["license"]["url"].startswith("https://")
    assert "acknowledged" not in notice


@pytest.mark.parametrize("damage", ["missing_file", "missing_text", "missing_attribution"])
def test_missing_notices_prevent_publication(tmp_path, monkeypatch, damage):
    pytest.importorskip("pysam")
    from pipeline import starter_annotations as module
    catalog = json.loads(module.LICENSE_NOTICES.read_text())
    path = tmp_path / "licenses.json"
    if damage == "missing_text":
        catalog["license_texts"]["CC-BY-4.0"]["text"] = ""
    elif damage == "missing_attribution":
        catalog["resources"][0]["attribution"] = ""
    if damage != "missing_file":
        path.write_text(json.dumps(catalog))
    monkeypatch.setattr(module, "LICENSE_NOTICES", path)
    output = tmp_path / "bundle"
    with pytest.raises((ValueError, FileNotFoundError)):
        prepare("alphamissense", [am_file(tmp_path)], output,
                release="test", mane_summary=summary(tmp_path))
    assert not output.exists()
    assert not list(tmp_path.glob(".starter-*"))


def test_macos_payload_keeps_offline_notices(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_macos_app
    monkeypatch.setattr(build_macos_app.subprocess, "check_output", lambda *a, **kw: b"")
    build_macos_app.copy_source(tmp_path)
    for name in ("THIRD_PARTY_NOTICES.md", "config/starter-licenses.json"):
        assert (tmp_path / name).read_bytes() == (build_macos_app.ROOT / name).read_bytes()


def test_conflicting_duplicate_fails_without_publishing(tmp_path):
    pytest.importorskip("pysam")
    first = am_file(tmp_path)
    second = text(tmp_path, "other.tsv", first.read_text().replace("0.8765", "0.9"))
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="conflicting scores"):
        prepare("alphamissense", [first, second], output, release="test", mane_summary=summary(tmp_path))
    assert not output.exists()
    assert not list(tmp_path.glob(".starter-*"))


def test_empty_subset_never_published(tmp_path):
    pytest.importorskip("pysam")
    path = am_file(tmp_path)
    path.write_text(path.read_text().replace("ENST000001", "ENST999999"))
    with pytest.raises(ValueError, match="empty"):
        prepare("alphamissense", [path], tmp_path / "bundle", release="test", mane_summary=summary(tmp_path))
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("isoform_version", [".1", ".2"])
def test_canonical_am_wins_even_when_lower_and_input_order_reversed(tmp_path, isoform_version):
    pysam = pytest.importorskip("pysam")
    row = ["1", "12", "A", "G", "ENST000001.1", "K2R", "0.1", "likely_benign", "ENST000001.2", "canonical"]
    isoform = row[:]
    isoform[4] = "ENST000001" + isoform_version
    isoform[6:8] = ["0.9", "likely_pathogenic"]
    isoform[-1] = "isoforms"
    stats = Counter()
    path = tmp_path / "am.tsv.gz"
    write_sorted([isoform, row], path, "alphamissense", stats)
    with gzip.open(path, "rt") as handle:
        assert handle.readlines()[1].split("\t")[6] == "0.1"
    assert stats["canonical_isoform_disagreements"] == 1
    assert stats["output_rows"] == 1


def test_am_normalized_sort_groups_interleaved_proteins_and_preserves_versions(tmp_path):
    pytest.importorskip("pysam")
    from pipeline.starter_annotations import validate_am_runtime_keys
    base = ["1", "12", "A", "G", "ENST000001.2", "K2R", "0.1", "likely_benign", "ENST000001.3", "canonical"]
    second = base[:]; second[5] = "K3R"
    iso = base[:]; iso[4] = "ENST000001.3"; iso[6] = "0.9"; iso[-1] = "isoforms"
    iso_second = iso[:]; iso_second[5] = "K3R"
    rows = [iso_second, iso, second, base]
    stats = Counter()
    path = tmp_path / "am.tsv.gz"
    write_sorted(rows, path, "alphamissense", stats)
    with gzip.open(path, "rt") as f:
        output = [line for line in f if not line.startswith("#")]
    assert validate_am_runtime_keys(output) == 2
    assert all(line.split("\t")[4] == "ENST000001.2" for line in output)
    assert stats["canonical_isoform_overlaps"] == 2
    # Old version-first ordering, including non-adjacent duplicate keys.
    with pytest.raises(ValueError, match="duplicate AlphaMissense runtime key"):
        validate_am_runtime_keys("\t".join(r) for r in [base, second, iso, iso_second])


@pytest.mark.parametrize("source", ["canonical", "isoforms"])
def test_am_cross_version_same_source_conflicts_fail_or_withhold(tmp_path, source):
    pytest.importorskip("pysam")
    base = ["1", "12", "A", "G", "ENST000001.2", "K2R", "0.1", "likely_benign", "ENST000001.4", source]
    conflict = base[:]; conflict[4] = "ENST000001.3"; conflict[6] = "0.2"
    fallback = base[:]; fallback[-1] = "isoforms"
    other = base[:]; other[1] = "13"
    rows = [conflict, other, fallback, base]
    with pytest.raises(ValueError, match="conflicting scores"):
        write_sorted(rows, tmp_path / "fail.gz", "alphamissense", Counter())
    stats, examples = Counter(), []
    path = tmp_path / "withheld.gz"
    write_sorted(rows, path, "alphamissense", stats, "withhold", examples)
    assert stats["output_rows"] == 1
    assert stats["ambiguous_keys_withheld"] == 1
    assert examples[0]["key"][4] == "ENST000001"
    with gzip.open(path, "rt") as f:
        assert f.readlines()[1].split("\t")[1] == "13"


def test_am_equivalent_isoform_versions_collapse_deterministically(tmp_path):
    pytest.importorskip("pysam")
    base = ["1", "12", "A", "G", "ENST000001.2", "K2R", "0.1", "likely_benign", "ENST000001.3", "isoforms"]
    equivalent = base[:]; equivalent[4] = "ENST000001.3"
    outputs = []
    for i, rows in enumerate(([base, equivalent], [equivalent, base])):
        stats = Counter(); path = tmp_path / f"order{i}.gz"
        write_sorted(rows, path, "alphamissense", stats)
        with gzip.open(path, "rt") as f:
            outputs.append(f.read())
        assert stats["output_rows"] == stats["equivalent_version_duplicates_removed"] == 1
    assert outputs[0] == outputs[1]
    assert "ENST000001.3" in outputs[0].splitlines()[1].split("\t")[4]


@pytest.mark.parametrize("source", ["canonical", "isoforms"])
@pytest.mark.parametrize("score", ["0.05", "0.9"])
def test_exact_mane_version_resolves_conflicts_independent_of_order_and_score(source, score):
    from itertools import permutations
    from pipeline.starter_annotations import resolve_am_duplicates
    old = "1\t12\tA\tG\tENST000001.1\tK2R\t0.1\tlikely_benign\tENST000001.2\t" + source + "\n"
    old_conflict = old.replace("0.1", "0.3")
    exact = old.replace("ENST000001.1", "ENST000001.2").replace("0.1", score)
    for rows in permutations([old, old_conflict, exact]):
        stats = Counter()
        assert list(resolve_am_duplicates(rows, stats, "error", [])) == [exact]
        assert stats["mane_version_conflicts_resolved"] == 1
        assert stats["ambiguous_keys_withheld"] == 0


def test_conflicting_exact_mane_predictions_still_withheld_without_fallback():
    from itertools import permutations
    from pipeline.starter_annotations import resolve_am_duplicates
    exact = "1\t12\tA\tG\tENST000001.2\tK2R\t0.1\tlikely_benign\tENST000001.2\tcanonical\n"
    conflict = exact.replace("0.1", "0.9")
    older = exact.replace("ENST000001.2\tK2R", "ENST000001.1\tK2R")
    supplemental = exact.replace("canonical", "isoforms")
    for rows in permutations([exact, conflict, older, supplemental]):
        stats = Counter()
        assert list(resolve_am_duplicates(rows, stats, "withhold", [])) == []
        assert stats["ambiguous_keys_withheld"] == 1
        assert stats["mane_version_conflicts_resolved"] == 0


def test_inconsistent_mane_versions_in_one_key_fail():
    from pipeline.starter_annotations import resolve_am_duplicates
    row = "1\t12\tA\tG\tENST000001.1\tK2R\t0.1\tlikely_benign\tENST000001.2\tisoforms\n"
    with pytest.raises(ValueError, match="inconsistent pinned MANE"):
        list(resolve_am_duplicates([row, row.replace("ENST000001.2", "ENST000001.3")], Counter(), "withhold", []))


def test_ambiguous_preferred_source_withholds_entire_key_not_just_one_row():
    from pipeline.starter_annotations import resolve_am_duplicates
    first = "1\t12\tA\tG\tENST000001.1\tK2R\t0.1\tlikely_benign\tENST000001.2\tcanonical\n"
    conflict = first.replace("0.1", "0.2")
    fallback = first.replace("canonical", "isoforms")
    other = first.replace("\t12\t", "\t13\t")
    stats, examples = Counter(), []
    result = list(resolve_am_duplicates([first, conflict, fallback, other], stats, "withhold", examples))
    assert result == [other]
    assert stats["ambiguous_keys_withheld"] == 1
    assert examples[0]["key"][1] == "12"


def test_ambiguous_supplement_does_not_override_valid_canonical_score():
    from pipeline.starter_annotations import resolve_am_duplicates
    canonical = "1\t12\tA\tG\tENST000001.1\tK2R\t0.1\tlikely_benign\tENST000001.2\tcanonical\n"
    first = canonical.replace("canonical", "isoforms")
    second = first.replace("0.1", "0.2")
    assert list(resolve_am_duplicates([canonical, first, second], Counter(), "error", [])) == [canonical]


def test_cadd_indexed_and_streaming_subsets_are_identical(tmp_path):
    pysam = pytest.importorskip("pysam")
    path = text(tmp_path, "cadd.tsv", "#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED\n"
                "1\t12\tA\tG\t0.1\t23.456\n1\t19\tA\tG\t0.1\t20\n")
    cds = annotation_regions(gtf(tmp_path))[0]
    expected = list(cadd_rows(path, cds, Counter()))
    pysam.tabix_compress(str(path), str(path) + ".gz")
    compressed = Path(str(path) + ".gz")
    pysam.tabix_index(str(compressed), seq_col=0, start_col=1, end_col=1)
    stats = Counter()
    assert list(cadd_rows(compressed, cds, stats)) == expected
    assert stats["source_rows"] == 1  # only the queried coding row was parsed


def test_generated_indexed_contracts_agree_with_registry():
    from pipeline.indexed_scores import validate_manifest_registry_contract
    from pipeline.predictor_registry import load_registry
    registry = load_registry()
    for kind in ("alphamissense", "cadd"):
        resource_id = "starter_" + kind
        manifest = indexed_manifest(kind, "test")
        assert registry.resource(resource_id).license_ack_required is False
        assert "acknowledged_by_user" not in manifest.get("license", {})
        validate_manifest_registry_contract(manifest, [registry.predictor(resource_id)],
            resource=registry.resource(resource_id), annotator=registry.annotator(resource_id))


def test_prepared_am_can_build_vep_command_without_dbnsfp(tmp_path):
    pytest.importorskip("pysam")
    from pipeline.build_vep_command import build_vep_command
    output = tmp_path / "bundle"
    prepare("alphamissense", [am_file(tmp_path)], output, release="test", mane_summary=summary(tmp_path))
    cfg = {"reference": {"assembly": "GRCh38"}, "plugins": {"AlphaMissenseStarter": {
        "enabled": True, "required": True, "file": str(output / "alphamissense.tsv.gz"),
        "manifest": str(output / "indexed.manifest.json"),
    }}}
    plan = build_vep_command(cfg, "input.vcf", "output.vcf", container=False, verify_integrity=True)
    assert not plan.errors, plan.errors
    assert any(arg.startswith("IndexedScores,") and "resource=starter_alphamissense" in arg for arg in plan.argv)
    assert not any(arg.startswith("dbNSFP,") for arg in plan.argv)


def test_spliceai_manifest_checks_parameters_gtf_and_hashes(tmp_path):
    from pipeline.starter_annotations import file_identity
    pytest.importorskip("pysam")
    source = splice_file(tmp_path, "1\t21\t.\tA\tG\t.\t.\tSpliceAI=G|PLUS|0|0|0|0|0|0|0|0\n")
    annotation, mane = gtf(tmp_path), summary(tmp_path)
    identity = file_identity(source)
    document = {"status": "passed", "genome_assembly": "GRCh38/hg38",
                "scientific_configuration": {"distance": 500, "mask": 1, "spliceai_version": "1.3.1",
                    "annotation_release": "MANE.GRCh38.v1.5.Select", "annotation_source_sha256": file_identity(annotation)["sha256"]},
                "files": [{"vcf": source.name, "vcf_sha256": identity["sha256"], "vcf_bytes": identity["size_bytes"]}]}
    manifest = text(tmp_path, "release.json", json.dumps(document))
    output = tmp_path / "bundle"
    def run():
        return prepare("spliceai", [source], output, release="test", mane_summary=mane, gtf=annotation, source_manifest=manifest)
    document["scientific_configuration"]["distance"] = 50
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="D=500"):
        run()
    document["scientific_configuration"]["distance"] = 500
    document["scientific_configuration"]["annotation_source_sha256"] = "0" * 64
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="boundary GTF"):
        run()
    document["scientific_configuration"]["annotation_source_sha256"] = file_identity(annotation)["sha256"]
    manifest.write_text(json.dumps(document))
    payload = run()
    assert payload["statistics"]["output_rows"] == 1
    assert payload["all_source_chromosomes_supplied"] is True


def test_am_perl_adapter_requires_allele_transcript_and_protein(tmp_path):
    stub = tmp_path / "stub/Bio/EnsEMBL/Variation/Utils"
    stub.mkdir(parents=True)
    (stub / "BaseVepTabixPlugin.pm").write_text(textwrap.dedent(r'''
        package Bio::EnsEMBL::Variation::Utils::BaseVepTabixPlugin;
        sub new { my ($class,$config,@args)=@_; bless {config=>$config,args=>\@args},$class; }
        sub get_user_params { }
        sub params_to_hash { my %p; for (@{$_[0]{args}}) { $p{$1}=$2 if /^([^=]+)=(.*)$/; } return \%p; }
        sub expand_left { } sub expand_right { } sub cache_size { } sub add_file { }
        sub get_data { return $_[0]{test_data}; }
        1;
    '''))
    (stub / "Sequence.pm").write_text(textwrap.dedent(r'''
        package Bio::EnsEMBL::Variation::Utils::Sequence;
        use Exporter 'import'; our @EXPORT_OK=qw(get_matched_variant_alleles);
        sub get_matched_variant_alleles {
          my ($q,$s)=@_;
          return [] unless $q->{pos}==$s->{pos} && $q->{ref} eq $s->{ref} && $q->{alts}[0] eq $s->{alts}[0];
          return [[$q->{alts}[0],$s->{alts}[0]]];
        }
        1;
    '''))
    manifest = text(tmp_path, "manifest.json", json.dumps(indexed_manifest("alphamissense", "test")))
    data = text(tmp_path, "scores.tsv.gz", "")
    harness = text(tmp_path, "harness.pl", textwrap.dedent(r'''
        use strict; use warnings; use JSON::PP qw(encode_json); use IndexedScores;
        { package TX; sub stable_id {$_[0]{id}} sub version {2} }
        { package VF; sub ref_allele_string {'A'} sub strand {1} }
        { package OC; sub SO_term {'missense_variant'} }
        { package OVERLAP; sub translation_start {$_[0]{position}} }
        { package TVA;
          sub new { my ($c,$tx,$peptide,$position,$alt)=@_; bless {tx=>$tx,peptide=>$peptide,position=>$position,alt=>$alt||'G'},$c; }
          sub transcript {bless {id=>$_[0]{tx}},'TX'}
          sub variation_feature {bless {chr=>'1',start=>12,end=>12},'VF'}
          sub variation_feature_seq {$_[0]{alt}}
          sub get_all_OverlapConsequences {[bless {},'OC']}
          sub base_variation_feature_overlap {bless {position=>$_[0]{position}},'OVERLAP'}
          sub pep_allele_string {$_[0]{peptide}}
        }
        my ($file,$manifest)=@ARGV;
        my $p=IndexedScores->new({},"file=$file","manifest=$manifest","resource=starter_alphamissense");
        $p->{test_data}=[$p->parse_data("1\t12\tA\tG\tENST000001.1\tK2R\t0.1234\tlikely_benign\tENST000001.2\tcanonical")];
        print encode_json({
          exact=>$p->run(TVA->new('ENST000001','K/R',2)),
          other_transcript=>$p->run(TVA->new('ENST000002','K/R',2)),
          other_protein=>$p->run(TVA->new('ENST000001','K/T',2)),
          other_position=>$p->run(TVA->new('ENST000001','K/R',3)),
          other_alt=>$p->run(TVA->new('ENST000001','K/R',2,'T'))
        });
    '''))
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["perl", "-I" + str(tmp_path / "stub"), "-I" + str(root / "docker"),
                             str(harness), str(data), str(manifest)], check=True, text=True, capture_output=True)
    observed = json.loads(result.stdout)
    assert observed["exact"]["StarterAM_score"] == "0.1234"
    assert observed["exact"]["StarterAM_match_status"] == "exact"
    for key in ("other_transcript", "other_protein", "other_position", "other_alt"):
        assert "StarterAM_score" not in observed[key]
