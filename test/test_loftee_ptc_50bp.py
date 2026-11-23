#!/usr/bin/env python3
"""Tests for frameshift PTC-based replacement of LOFTEE's 50-bp rule."""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline.loftee_ptc_50bp import (
    APPENDED_FIELDS,
    SELENOPROTEIN_SKIP_STATUS,
    Transcript,
    allele_index,
    calculate,
    collect_transcripts,
    load_transcripts,
    prepare_transcript,
    process_vcf,
    revcomp,
)


class StubFasta:
    """1-based inclusive fetch over an in-memory contig, like IndexedFasta."""

    def __init__(self, sequence: str):
        self.sequence = sequence

    def fetch(self, chrom: str, start: int, end: int) -> str:
        return self.sequence[start - 1 : end]


def model() -> Transcript:
    return Transcript(
        transcript_id="ENST00000000001",
        chrom="1",
        version=1,
        strand=1,
        biotype="protein_coding",
        blocks=[(100, 117, 0)],
        cds="ATGAAACCCGGGTTTTAA",
        utr3="ATGATAATAA",
        last_coding_exon_cds=1,
        last_exon_junction_cds=100,
    )


def test_boundary_insertions_use_the_transcript_side_of_the_cut():
    tx = model()
    tx.blocks = [(100, 102, 0), (200, 214, 3)]
    tx.last_exon_junction_cds = 4
    # Both edits insert at the transcript cut ATG|AAA... . Moving a
    # repeat-equivalent insertion into its exon preserves the junction side.
    plus = calculate(tx, 199, "G", "GAT", "frameshift_variant", "", 50)
    assert (plus["ptc_cds"], plus["dist"]) == (22, 4 - 22)
    tx.strand = -1
    tx.blocks = [(201, 203, 0), (100, 114, 3)]
    minus = calculate(tx, 200, "G", "GCA", "frameshift_variant", "", 50)
    assert (minus["ptc_cds"], minus["dist"]) == (4, 6 - 4)
    # A non-repeat insertion at the splice boundary has no equivalent
    # within-CDS placement. Keep the missing calculation visible.
    ambiguous = calculate(tx, 200, "G", "GAA", "frameshift_variant", "", 50)
    assert ambiguous["status"] == "outside_cds" and not ambiguous["rule"]


def test_padding_outside_cds_does_not_change_a_coding_edit():
    tx = model()
    for ref, alt in (("AT", "A"), ("AT", "G")):
        unpadded = calculate(tx, 100, ref, alt, "frameshift_variant", "", 50)
        padded = calculate(tx, 99, "C" + ref + "G", "C" + alt + "G", "frameshift_variant", "", 50)
        assert padded == unpadded
        assert padded["status"] == "ok"


def test_right_padded_repeat_deletion_keeps_the_left_vcf_anchor():
    tx = model()
    tx.blocks = [(100, 102, 0), (200, 214, 3)]
    tx.last_exon_junction_cds = 4
    # The A at 199 is the intronic anchor. The deleted A at 200 is coding.
    for ref, alt in [("AA", "A"), ("AAAA", "AAA")]:
        result = calculate(tx, 199, ref, alt, "frameshift_variant", "", 50)
        assert (result["status"], result["ptc_cds"], result["dist"]) == ("ok", 19, -15)


def test_record_wide_csq_indel_anchor():
    alts = ["A", "ATT"]
    assert allele_index({"Allele": "-"}, "AT", alts) == 0
    assert allele_index({"Allele": "TT"}, "AT", alts) == 1
    assert allele_index({"Allele": "TT", "ALLELE_NUM": "1"}, "AT", alts) == 0
    assert allele_index({"Allele": "TT"}, "AT", ["ATT", "ATTT"]) is None
    # AT is the stripped form of AAT and the suffix-trimmed form of ATA.
    assert allele_index({"Allele": "AT"}, "A", ["AAT", "ATA"]) is None
    # Biallelic PTC inputs use prefix-first spelling even in a repeat.
    assert allele_index({"Allele": "TA"}, "A", ["ATA"]) == 0
    assert allele_index({"Allele": "AT"}, "A", ["ATA"]) is None


def test_frameshift_is_scored_at_downstream_ptc():
    result = calculate(
        model(),
        102,
        "GA",
        "G",
        "frameshift_variant",
        "ENST00000000001.1:c.4del",
        50,
    )
    assert result["status"] == "ok"
    assert result["ptc_cds"] == 19
    assert result["ptc_aa"] == 7
    assert result["dist"] == 80
    assert result["rule"] == "PASS"


def test_successful_recomputation_replaces_lof_info_and_preserves_original(tmp_path):
    fields = [
        "Allele",
        "ALLELE_NUM",
        "Consequence",
        "Feature",
        "HGVSc",
        "HGVSp",
        "LoF",
        "LoF_info",
    ]
    csq = "|".join(
        [
            "G",
            "1",
            "frameshift_variant",
            "ENST00000000001",
            "ENST00000000001.1:c.4del",
            "ENSP1:p.Ala2GlyfsTer6",
            "HC",
            "DIST_FROM_LAST_EXON:10&50_BP_RULE:FAIL",
        ]
    )
    source = tmp_path / "input.vcf"
    output = tmp_path / "output.vcf"
    source.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"1\t102\t.\tGA\tG\t.\tPASS\tCSQ={csq}\n"
    )
    report = process_vcf(
        source,
        output,
        {"ENST00000000001": model()},
        fields,
        50,
        True,
        {"assembly": "GRCh38", "ensembl_release": "test"},
    )
    text = output.read_text()
    header = next(line for line in text.splitlines() if line.startswith("##INFO=<ID=CSQ"))
    output_fields = re.search(r"Format: ([^\">]+)", header).group(1).split("|")
    record = next(line for line in text.splitlines() if not line.startswith("#"))
    raw_csq = dict(item.split("=", 1) for item in record.split("\t")[7].split(";"))["CSQ"]
    annotation = dict(zip(output_fields, raw_csq.split("|")))

    assert "50_BP_RULE:PASS" in annotation["LoF_info"]
    assert annotation["LoF_50_BP_RULE_original"] == "FAIL"
    assert annotation["LoF_50_BP_RULE_PTC"] == "PASS"
    assert annotation["LoF_50_BP_RULE_changed"] == "1"
    assert annotation["PTC_dist_from_last_exon"] == "80"
    assert annotation["PTC_calc_status"] == "ok"
    assert report["changed"] == 1
    assert report["hgvsp_fsTer_validation"] == {"checked": 1, "matched": 1}

    second = tmp_path / "second.vcf"
    _, second_fields = collect_transcripts(output)
    process_vcf(
        output,
        second,
        {"ENST00000000001": model()},
        second_fields,
        50,
        True,
        {"assembly": "GRCh38", "ensembl_release": "test"},
    )
    second_header = next(
        line for line in second.read_text().splitlines()
        if line.startswith("##INFO=<ID=CSQ")
    )
    fields_again = re.search(r"Format: ([^\">]+)", second_header).group(1).split("|")
    second_record = next(
        line for line in second.read_text().splitlines() if not line.startswith("#")
    )
    second_csq = dict(
        item.split("=", 1) for item in second_record.split("\t")[7].split(";")
    )["CSQ"]
    annotation_again = dict(zip(fields_again, second_csq.split("|")))
    assert annotation_again["LoF_50_BP_RULE_original"] == "FAIL"
    assert annotation_again["LoF_50_BP_RULE_PTC"] == "PASS"


def test_gz_output_name_produces_real_gzip(tmp_path):
    # Audit repro (CORE-4): --output foo.vcf.gz used to receive plain text
    # under a .gz name, breaking every downstream gzip/tabix reader.
    import gzip
    fields = ["Allele", "ALLELE_NUM", "Consequence", "Feature", "HGVSc", "HGVSp", "LoF", "LoF_info"]
    source = tmp_path / "input.vcf"
    output = tmp_path / "output.vcf.gz"
    source.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t102\t.\tGA\tG\t.\tPASS\tCSQ=G|1|missense_variant|ENST00000000001|||\n"
    )
    process_vcf(
        source,
        output,
        {"ENST00000000001": model()},
        fields,
        50,
        True,
        {"assembly": "GRCh38", "ensembl_release": "test"},
    )
    with output.open("rb") as handle:
        assert handle.read(2) == b"\x1f\x8b"
    with gzip.open(output, "rt") as handle:
        assert handle.readline().startswith("##fileformat")


def test_version_mismatch_is_refused():
    result = calculate(
        model(),
        102,
        "GA",
        "G",
        "frameshift_variant",
        "ENST00000000001.2:c.4del",
        50,
    )
    assert result["status"] == "transcript_version_mismatch"
    assert result["rule"] == ""


def test_reference_disrupted_transcript_biotype_is_refused_before_cds_scoring():
    transcript = model()
    transcript.biotype = "protein_coding_LoF"
    transcript.problems = ["cds_not_multiple_of_3", "cds_internal_stop"]
    result = calculate(
        transcript,
        102,
        "GA",
        "G",
        "frameshift_variant",
        "ENST00000000001.1:c.4del",
        50,
    )
    assert result["status"] == "unsupported_transcript_biotype:protein_coding_LoF"
    assert result["rule"] == ""


def test_unscored_transcript_does_not_get_fabricated_lof_info(tmp_path):
    # Audit repro (CORE-7): a frameshift CSQ whose LoF/LoF_info are empty
    # (LOFTEE declined to score the transcript) must not have 50_BP_RULE
    # written into LoF_info; the recomputed rule belongs only in this
    # module's own LoF_50_BP_RULE_PTC field.
    fields = [
        "Allele",
        "ALLELE_NUM",
        "Consequence",
        "Feature",
        "HGVSc",
        "HGVSp",
        "LoF",
        "LoF_info",
    ]
    csq = "|".join(
        [
            "G",
            "1",
            "frameshift_variant",
            "ENST00000000001",
            "ENST00000000001.1:c.4del",
            "ENSP1:p.Ala2GlyfsTer6",
            "",
            "",
        ]
    )
    source = tmp_path / "input.vcf"
    output = tmp_path / "output.vcf"
    source.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: '
        + "|".join(fields)
        + '">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"1\t102\t.\tGA\tG\t.\tPASS\tCSQ={csq}\n"
    )
    process_vcf(
        source,
        output,
        {"ENST00000000001": model()},
        fields,
        50,
        True,
        {"assembly": "GRCh38", "ensembl_release": "test"},
    )
    text = output.read_text()
    header = next(line for line in text.splitlines() if line.startswith("##INFO=<ID=CSQ"))
    output_fields = re.search(r"Format: ([^\">]+)", header).group(1).split("|")
    record = next(line for line in text.splitlines() if not line.startswith("#"))
    raw_csq = dict(item.split("=", 1) for item in record.split("\t")[7].split(";"))["CSQ"]
    annotation = dict(zip(output_fields, raw_csq.split("|")))

    assert annotation["LoF_info"] == ""
    assert annotation["LoF"] == ""
    assert annotation["LoF_50_BP_RULE_PTC"] == "PASS"
    assert annotation["LoF_50_BP_RULE_original"] == ""
    assert annotation["LoF_50_BP_RULE_changed"] == ""


def test_single_coding_block_has_no_coding_anchor(tmp_path):
    # Audit repro (CORE-10): a transcript whose ORF sits in one merged CDS
    # block previously got last_coding_exon_cds=1, making every coding
    # distance negative and rule_coding an unconditional FAIL.
    genome = "N" * 99 + "ATGAAACCCGGGTTTTAA" + "N" * 182 + "ATGATAATAA" + "N" * 91
    single = Transcript("ENST00000000001")
    single.chrom = "1"
    single.strand = 1
    single.biotype = "protein_coding"
    single.exons = [(100, 117), (300, 309)]
    single.coding_features = [(100, 117)]
    prepare_transcript(single, StubFasta(genome))
    assert single.last_coding_exon_cds is None

    two_blocks = Transcript("ENST00000000002")
    two_blocks.chrom = "1"
    two_blocks.strand = 1
    two_blocks.biotype = "protein_coding"
    two_blocks.exons = [(100, 108), (300, 308)]
    two_blocks.coding_features = [(100, 108), (300, 308)]
    prepare_transcript(two_blocks, StubFasta(genome))
    assert two_blocks.last_coding_exon_cds == 10


def test_missing_coding_anchor_suppresses_rule_coding():
    transcript = model()
    transcript.last_coding_exon_cds = None
    result = calculate(
        transcript,
        102,
        "GA",
        "G",
        "frameshift_variant",
        "ENST00000000001.1:c.4del",
        50,
    )
    assert result["status"] == "ok"
    assert result["dist_coding"] is None
    assert result["rule_coding"] == ""
    assert result["rule"] == "PASS"


def test_single_exon_transcript_keeps_ptc_but_does_not_apply_junction_rule():
    transcript = model()
    transcript.exons = [(100, 127)]
    result = calculate(
        transcript,
        102,
        "GA",
        "G",
        "frameshift_variant",
        "ENST00000000001.1:c.4del",
        50,
    )
    assert result["status"] == "not_applicable_single_exon_transcript"
    assert result["ptc_cds"] == 19
    assert result["ptc_aa"] == 7
    assert result["dist"] is None
    assert result["rule"] == ""


SELENO_CDS = "ATGAAATGACCCGGGTTT"  # in-frame TGA at codon 3 (CDS 7-9)


def _gtf(lines: list[str], tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "models.gtf"
    path.write_text("#!genome-build test\n" + "".join(lines), encoding="utf-8")
    return path


def _feature(kind, start, end, strand, transcript="ENST00000000009"):
    return (
        f"1\ttest\t{kind}\t{start}\t{end}\t.\t{strand}\t.\t"
        f'gene_id "G1"; transcript_id "{transcript}"; transcript_version "1"; '
        'transcript_biotype "protein_coding";\n'
    )


def test_right_padded_boundary_edits_through_postprocessor(tmp_path):
    source = pathlib.Path(__file__).parent / "contracts" / "loftee-ptc-boundary.vcf"
    wanted, fields = collect_transcripts(source)
    # Both genomes have AG at 115-116 in the CDS, then GGT in the intron.
    # On + the edit shortens the exon before the junction. On - it shortens
    # the exon after the junction, so the junction stays at CDS position 16.
    cases = [
        ("+", 100, "ATG" + "AAA" * 4 + "AG", "AATAAAATAA", (19, -2), (16, 1)),
        ("-", 105, revcomp("CTAATAAAATAA"), revcomp("ATG" + "AAA" * 4), (16, 0), (19, -3)),
    ]
    for strand, start, low_exon, high_exon, deletion, delins in cases:
        genome = "N" * (start - 1) + low_exon + "GGT" + "N" * 80 + high_exon
        features = [
            _feature(kind, left, right, strand)
            for left, right in [(start, 116), (200, 199 + len(high_exon))]
            for kind in ["exon", "CDS"]
        ]
        models = load_transcripts(_gtf(features, tmp_path), wanted, StubFasta(genome))
        output = tmp_path / "scored.vcf"
        report = process_vcf(source, output, models, fields, 50, True, {})
        _, output_fields = collect_transcripts(output)
        entries = {}
        for line in output.read_text().splitlines():
            if not line.startswith("#"):
                record = line.split("\t")
                entries[record[2]] = [
                    dict(zip(output_fields, csq.split("|")))
                    for csq in record[7].removeprefix("CSQ=").split(",")
                ]

        for name, (ptc, distance) in [("deletion", deletion), ("delins", delins)]:
            compact = entries[name][0]
            padded = entries["padded_" + name][0]
            for field in APPENDED_FIELDS:
                assert padded[field] == compact[field], (strand, name, field)
            assert padded["PTC_calc_status"] == "ok"
            assert padded["PTC_cds_pos"] == str(ptc)
            assert padded["PTC_dist_from_last_exon"] == str(distance)
            assert padded["PTC_dist_from_last_coding_exon"] == str(distance)
            assert padded["LoF_info"] == compact["LoF_info"] == "50_BP_RULE:FAIL"
            assert padded["LoF_50_BP_RULE_original"] == "PASS"

        for entry, status in [
            (entries["padded_deletion"][1], "outside_cds"),
            (entries["padded_deletion"][2], "allele_mapping_failed"),
            (entries["bad_padding"][0], "ref_mismatch"),
        ]:
            assert entry["PTC_calc_status"] == status
            assert entry["LoF_50_BP_RULE_PTC"] == ""
            assert entry["LoF_info"] == "50_BP_RULE:PASS"
        assert report["recomputed"] == report["changed"] == 4


def test_multiallelic_padded_insertion_preserves_coding_cut(tmp_path):
    tx = "ENST00000000009"
    source = tmp_path / "insertions.vcf"
    source.write_text(
        '##fileformat=VCFv4.2\n'
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|ALLELE_NUM|Consequence|Feature|LoF_info">\n'
        '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n'
        f'1\t115\tcompact\tA\tAG\t.\tPASS\tCSQ=G|1|frameshift_variant|{tx}|50_BP_RULE:PASS\n'
        f'1\t115\tpadded\tAGGGT\tA,AGGGGT\t.\tPASS\tCSQ=GGGGT|2|frameshift_variant|{tx}|50_BP_RULE:PASS\n'
    )
    wanted, fields = collect_transcripts(source)
    for strand, start, left, right, expected_ptc in [
        ("+", 100, "ATG" + "AAA" * 4 + "AG", "TAAAAAATAA", 19),
        ("-", 105, revcomp("CTAAATAAATAA"), revcomp("ATG" + "AAA" * 4), 22),
    ]:
        genome = "N" * (start - 1) + left + "GGT" + "N" * 80 + right
        features = [_feature(kind, x, y, strand)
                    for x, y in [(start, 116), (200, 199 + len(right))] for kind in ("exon", "CDS")]
        models = load_transcripts(_gtf(features, tmp_path), wanted, StubFasta(genome))
        output = tmp_path / "scored.vcf"
        report = process_vcf(source, output, models, fields, 50, True, {})
        assert report["recomputed"] == report["changed"] == 2
        _, names = collect_transcripts(output)
        compact, padded = [dict(zip(names, line.split("\t")[7][4:].split("|")))
                           for line in output.read_text().splitlines() if not line.startswith("#")]
        assert all(compact[k] == padded[k] for k in APPENDED_FIELDS)
        assert padded["PTC_calc_status"] == "ok"
        assert padded["PTC_cds_pos"] == str(expected_ptc)
        assert padded["LoF_info"] == "50_BP_RULE:FAIL"


def test_selenoprotein_transcript_is_a_deliberate_skip(tmp_path):
    # Review M4: an in-frame UGA at an annotated selenocysteine position is
    # sense, so the model is intact — but the recomputation is refused with
    # its own documented status instead of bad_transcript_model.
    genome = "N" * 99 + SELENO_CDS + "TAA" + "N" * 179 + "ATGATAATAA" + "N" * 91
    plus = [
        _feature("transcript", 100, 309, "+"),
        _feature("exon", 100, 120, "+"),
        _feature("CDS", 100, 117, "+"),
        _feature("stop_codon", 118, 120, "+"),
        _feature("Selenocysteine", 106, 108, "+"),
        _feature("exon", 300, 309, "+"),
    ]
    models = load_transcripts(
        _gtf(plus, tmp_path), {"ENST00000000009"}, StubFasta(genome)
    )
    seleno = models["ENST00000000009"]
    assert seleno.ok, seleno.problems
    assert seleno.is_selenoprotein
    assert seleno.selenocysteine_cds_codons == [7]
    result = calculate(
        seleno, 102, "GA", "G", "frameshift_variant",
        "ENST00000000009.1:c.4del", 50,
    )
    assert result["status"] == SELENOPROTEIN_SKIP_STATUS
    assert result["ptc_cds"] is None
    assert result["rule"] == ""

    # Without the annotation the same CDS is still a broken model: no TGA is
    # ever reinterpreted on its own.
    unannotated = [line for line in plus if "\tSelenocysteine\t" not in line]
    models = load_transcripts(
        _gtf(unannotated, tmp_path), {"ENST00000000009"}, StubFasta(genome)
    )
    plain = models["ENST00000000009"]
    assert plain.problems == ["cds_internal_stop"]
    assert not plain.is_selenoprotein
    result = calculate(
        plain, 102, "GA", "G", "frameshift_variant",
        "ENST00000000009.1:c.4del", 50,
    )
    assert result["status"] == "bad_transcript_model:cds_internal_stop"

    # A Selenocysteine feature that does not sit on an in-frame UGA is an
    # annotation/model mismatch, not a licence to ignore a real stop.
    misplaced = [
        _feature("Selenocysteine", 103, 105, "+") if "\tSelenocysteine\t" in line
        else line
        for line in plus
    ]
    models = load_transcripts(
        _gtf(misplaced, tmp_path), {"ENST00000000009"}, StubFasta(genome)
    )
    wrong = models["ENST00000000009"]
    assert "selenocysteine_site_not_uga" in wrong.problems
    assert "cds_internal_stop" in wrong.problems


def test_selenoprotein_site_maps_on_the_minus_strand(tmp_path):
    def revcomp(value):
        return value.translate(str.maketrans("ACGT", "TGCA"))[::-1]

    # Minus-strand copy of the same ORF: CDS 203-220, stop 200-202, and the
    # UGA-Sec codon (CDS 7-9) at genomic 212-214.
    orf = SELENO_CDS + "TAA"
    genome = "N" * 49 + "ATGATAATAA" + "N" * 140 + revcomp(orf) + "N" * 100
    assert len(genome) >= 220
    minus = [
        _feature("transcript", 50, 220, "-"),
        _feature("exon", 200, 220, "-"),
        _feature("CDS", 203, 220, "-"),
        _feature("stop_codon", 200, 202, "-"),
        _feature("Selenocysteine", 212, 214, "-"),
        _feature("exon", 50, 59, "-"),
    ]
    models = load_transcripts(
        _gtf(minus, tmp_path), {"ENST00000000009"}, StubFasta(genome)
    )
    seleno = models["ENST00000000009"]
    assert seleno.ok, seleno.problems
    assert seleno.cds == orf
    assert seleno.selenocysteine_cds_codons == [7]
    result = calculate(
        seleno, 218, "TC", "T", "frameshift_variant",
        "ENST00000000009.1:c.4del", 50,
    )
    assert result["status"] == SELENOPROTEIN_SKIP_STATUS


if __name__ == "__main__":
    tests = [
        test_frameshift_is_scored_at_downstream_ptc,
        test_boundary_insertions_use_the_transcript_side_of_the_cut,
        test_padding_outside_cds_does_not_change_a_coding_edit,
        test_right_padded_repeat_deletion_keeps_the_left_vcf_anchor,
        test_right_padded_boundary_edits_through_postprocessor,
        test_multiallelic_padded_insertion_preserves_coding_cut,
        test_record_wide_csq_indel_anchor,
        test_successful_recomputation_replaces_lof_info_and_preserves_original,
        test_unscored_transcript_does_not_get_fabricated_lof_info,
        test_single_coding_block_has_no_coding_anchor,
        test_missing_coding_anchor_suppresses_rule_coding,
        test_gz_output_name_produces_real_gzip,
        test_version_mismatch_is_refused,
        test_reference_disrupted_transcript_biotype_is_refused_before_cds_scoring,
        test_single_exon_transcript_keeps_ptc_but_does_not_apply_junction_rule,
        test_selenoprotein_transcript_is_a_deliberate_skip,
        test_selenoprotein_site_maps_on_the_minus_strand,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as directory:
            if "tmp_path" in test.__code__.co_varnames:
                test(pathlib.Path(directory))
            else:
                test()
        print(f"PASS  {test.__name__}")
