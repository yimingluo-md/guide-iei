#!/usr/bin/env python3
"""Unit tests for the ClinVar amino-acid-match post-processing (VCF)."""
import gzip
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import clinvar_aa_match as aam          # noqa: E402
import reduce_vep_to_aa_reference as red  # noqa: E402


CSQ_FORMAT = ("Allele|Consequence|SYMBOL|Gene|Feature|BIOTYPE|"
              "Protein_position|Amino_acids|SIFT|CADD_PHRED")


def _ref(*residues, changes=()):
    ref = aam.Reference()
    for entry in residues:
        sym, pos = entry[0], entry[1]
        ref_aa = entry[2] if len(entry) > 2 else "-"
        ref.add_residue(sym, pos, ref_aa)
    for entry in changes:
        if len(entry) == 4:
            sym, pos, ref_aa, alt_aa = entry
        else:
            sym, pos, alt_aa = entry
            ref_aa = "-"
        ref.add_residue(sym, pos, ref_aa)
        ref.changes.add((sym, pos, ref_aa, alt_aa, "-"))
    return ref


def _csq(cons, symbol, protpos, allele="A"):
    # positional per CSQ_FORMAT
    vals = [allele, cons, symbol, "ENSG0", "ENST0", "protein_coding",
            protpos, "R/H", "deleterious", "25"]
    return "|".join(vals)


def _vcf(records, sample=True):
    """Build a minimal VEP-style VCF text. `records` = list of (info_csq_str,)."""
    h = [
        "##fileformat=VCFv4.2",
        '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence '
        f'annotations from Ensembl VEP. Format: {CSQ_FORMAT}">',
    ]
    cols = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"
    if sample:
        cols += "\tFORMAT\tSAMPLE1"
    lines = h + [cols]
    for i, csq in enumerate(records, 1):
        row = f"1\t{1000+i}\t.\tC\tA\t.\tPASS\tCSQ={csq}"
        if sample:
            row += "\tGT\t0/1"
        lines.append(row)
    return "\n".join(lines) + "\n"


def _write(path, text):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "wt") as fh:
        fh.write(text)


def test_parse_csq_format():
    hdr = ['##INFO=<ID=CSQ,Number=.,Type=String,Description="... Format: '
           + CSQ_FORMAT + '">']
    fields = aam.parse_csq_format(hdr)
    assert fields[0] == "Allele"
    assert "SYMBOL" in fields and "Protein_position" in fields and "Consequence" in fields


def test_match_and_nonmatch(tmp_path):
    # reference: BRCA1 residue 100 is a known pathogenic missense
    ref = _ref(("BRCA1", "100"))
    records = [
        _csq("missense_variant", "BRCA1", "100"),   # -> match
        _csq("missense_variant", "BRCA1", "250"),   # residue not in ref -> 0
        _csq("missense_variant", "TP53", "100"),    # symbol mismatch -> 0
        _csq("synonymous_variant", "BRCA1", "100"), # not missense -> 0
        _csq("missense_variant", "BRCA1", "-"),     # no protein pos -> 0
    ]
    vin = str(tmp_path / "in.vcf")
    vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf(records))
    stats = aam.annotate(vin, vout, ref)
    assert stats["records"] == 5
    assert stats["matched"] == 1, stats
    # verify per-record flags + zygosity preserved
    flags, gts = [], []
    for line in open(vout):
        if line.startswith("#"):
            continue
        cols = line.rstrip("\n").split("\t")
        info = cols[7]
        kv = dict(x.split("=", 1) for x in info.split(";") if "=" in x)
        flags.append(kv["ClinVar_path_aa_match"])
        gts.append(cols[9])  # SAMPLE1 GT
    assert flags == ["1", "0", "0", "0", "0"], flags
    assert gts == ["0/1"] * 5, "zygosity/GT must be preserved untouched"


def test_header_injected(tmp_path):
    ref = _ref(("BRCA1", "100"))
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    aam.annotate(vin, vout, ref, clinvar_release="20260218")
    text = open(vout).read()
    assert '##INFO=<ID=ClinVar_path_aa_match,Number=A,Type=Integer' in text
    assert '##INFO=<ID=ClinVar_path_aa_change_match,Number=A,Type=Integer' in text
    assert "20260218" in text  # release stamped in description


def test_multi_transcript_any_match(tmp_path):
    """A variant with several CSQ transcript entries matches if ANY is a hit."""
    ref = _ref(("BRCA1", "100"))
    multi = ",".join([
        _csq("intron_variant", "BRCA1", "-"),
        _csq("missense_variant", "BRCA1", "100"),  # this one hits
    ])
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([multi]))
    stats = aam.annotate(vin, vout, ref)
    assert stats["matched"] == 1


def test_protein_position_range_form(tmp_path):
    """VEP sometimes emits Protein_position as e.g. '100' — reference must use
    the same string form the sample VCF carries (exact-string match)."""
    ref = _ref(("BRCA1", "100"))
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    stats = aam.annotate(vin, vout, ref)
    assert stats["matched"] == 1


def test_gzip_roundtrip(tmp_path):
    ref = _ref(("BRCA1", "100"))
    vin = str(tmp_path / "in.vcf.gz"); vout = str(tmp_path / "out.vcf.gz")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    aam.annotate(vin, vout, ref)
    with gzip.open(vout, "rt") as fh:
        text = fh.read()
    assert "ClinVar_path_aa_match=1" in text


def test_empty_reference_all_zero(tmp_path):
    """No reference -> every record flagged 0, run still valid."""
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    stats = aam.annotate(vin, vout, aam.Reference())
    assert stats["matched"] == 0
    assert "ClinVar_path_aa_match=0" in open(vout).read()


def test_missing_reference_fails_without_explicit_opt_in(tmp_path):
    """Audit repro (CORE-16): a missing/empty reference used to degrade to an
    all-zero flag with exit 0, indistinguishable from a real negative."""
    vin = str(tmp_path / "in.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    out = str(tmp_path / "out.vcf")
    rc = aam.main(["--input", vin, "--output", out,
                   "--reference", str(tmp_path / "nonexistent.tsv"),
                   "--clinvar-release", "TEST"])
    assert rc == 3

    rc = aam.main(["--input", vin, "--output", out,
                   "--reference", str(tmp_path / "nonexistent.tsv"),
                   "--allow-missing-reference", "--clinvar-release", "TEST"])
    assert rc == 0
    assert "ClinVar_path_aa_match=0" in open(out).read()


def test_headerless_reduce_input_with_data_rows_fails(tmp_path):
    """Audit repro (CORE-18): headerless VEP tab used to yield a silent empty
    reference with exit 0, which the shell then release-stamped."""
    tab = str(tmp_path / "headerless.tsv")
    with open(tab, "w") as fh:
        fh.write("v1\tBRCA1\t100\tmissense_variant\n")
    out = str(tmp_path / "ref.tsv")
    try:
        red.reduce_tab(tab, out)
        raise AssertionError("headerless input with data rows must fail")
    except ValueError as exc:
        assert "no VEP tab header" in str(exc)


def test_short_record_is_padded_not_crashed(tmp_path):
    """Audit repro (CORE-5): a 7-column sites-only record used to raise
    IndexError on the unconditional cols[7] assignment."""
    vcf_in = str(tmp_path / "in.vcf")
    with open(vcf_in, "w") as fh:
        fh.write(
            "##fileformat=VCFv4.2\n"
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="... Format: '
            + CSQ_FORMAT + '">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100\t.\tC\tA\t.\tPASS\n"          # 7 columns, INFO missing
        )
    ref_path = str(tmp_path / "ref.tsv")
    with open(ref_path, "w") as fh:
        fh.write("BRCA1\t100\n")
    out = str(tmp_path / "out.vcf")
    rc = aam.main(["--input", vcf_in, "--output", out,
                   "--reference", ref_path, "--clinvar-release", "TEST"])
    assert rc == 0
    record = [l for l in open(out) if not l.startswith("#")][0].rstrip("\n")
    assert record.split("\t")[7] == "ClinVar_path_aa_match=0;ClinVar_path_aa_change_match=0"


def test_reduce_gz_output_is_real_gzip(tmp_path):
    """Audit repro (CORE-4 twin): a .gz output name used to receive plain text."""
    tab = str(tmp_path / "clinvar.vep.tsv")
    with open(tab, "w") as fh:
        fh.write("#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence\n")
        fh.write("v1\tBRCA1\t100\tmissense_variant\n")
    out = str(tmp_path / "ref.tsv.gz")
    n = red.reduce_tab(tab, out)
    assert n == 1
    with open(out, "rb") as fh:
        assert fh.read(2) == b"\x1f\x8b"
    with gzip.open(out, "rt") as fh:
        assert fh.read() == "BRCA1\t100\t-\t-\t-\n"


def test_reducer(tmp_path):
    """The VEP-tab -> reference reducer keeps missense w/ protein pos, dedups."""
    tab = str(tmp_path / "clinvar.vep.tsv")
    with open(tab, "w") as fh:
        fh.write("#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence\n")
        fh.write("v1\tBRCA1\t100\tmissense_variant\n")
        fh.write("v2\tBRCA1\t100\tmissense_variant\n")   # dup
        fh.write("v3\tTP53\t250\tmissense_variant&NMD\n")  # compound cons, keep
        fh.write("v4\tBRCA1\t-\tmissense_variant\n")       # no pos, drop
        fh.write("v5\tEGFR\t50\tsynonymous_variant\n")     # not missense, drop
    out = str(tmp_path / "ref.tsv")
    n = red.reduce_tab(tab, out)
    lines = [l.strip() for l in open(out)]
    assert n == 2, lines
    assert "BRCA1\t100\t-\t-\t-" in lines
    assert "TP53\t250\t-\t-\t-" in lines
    # round-trips into the matcher's loader
    ref = aam.load_reference(out)
    assert ("BRCA1", "100") in ref.residues and ("TP53", "250") in ref.residues



def test_change_match_is_separate_from_residue_match(tmp_path):
    """Same residue, different substitution -> residue flag only (PM5-style);
    same amino-acid change -> both flags (PS1-style)."""
    ref = _ref(changes=[("BRCA1", "100", "H")])
    # _csq writes Amino_acids R/H, so BRCA1:100 here IS the change R->H.
    same_change = _csq("missense_variant", "BRCA1", "100")
    different_change = same_change.replace("R/H", "R/W")
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([same_change, different_change]))
    stats = aam.annotate(vin, vout, ref)
    assert stats["matched"] == 2
    assert stats["change_matched"] == 1
    flags = []
    for line in open(vout):
        if line.startswith("#"):
            continue
        kv = dict(x.split("=", 1) for x in line.split("\t")[7].split(";") if "=" in x)
        flags.append((kv["ClinVar_path_aa_match"], kv["ClinVar_path_aa_change_match"]))
    assert flags == [("1", "1"), ("1", "0")], flags


def test_multiallelic_match_stays_on_its_own_alt(tmp_path):
    """A match belonging to ALT 2 must not be copied to ALT 1's flags."""
    fmt = "Allele|ALLELE_NUM|Consequence|SYMBOL|Protein_position|Amino_acids"
    csq = ",".join([
        "A|1|synonymous_variant|BRCA1|100|R",
        "G|2|missense_variant|BRCA1|100|R/H",
    ])
    vcf = (
        "##fileformat=VCFv4.2\n"
        f'##INFO=<ID=CSQ,Number=.,Type=String,Description="... Format: {fmt}">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        f"1\t1000\t.\tC\tA,G\t.\tPASS\tCSQ={csq}\n"
    )
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, vcf)
    aam.annotate(vin, vout, _ref(changes=[("BRCA1", "100", "H")]))
    record = [l for l in open(vout) if not l.startswith("#")][0].rstrip("\n")
    kv = dict(x.split("=", 1) for x in record.split("\t")[7].split(";") if "=" in x)
    assert kv["ClinVar_path_aa_match"] == "0,1", kv
    assert kv["ClinVar_path_aa_change_match"] == "0,1", kv


def test_headered_zero_missense_rows_refused(tmp_path):
    """A headered VEP tab with no pathogenic missense rows is a truncated or
    wrong input; writing (and stamping) an empty catalog is refused."""
    tab = str(tmp_path / "empty.tsv")
    with open(tab, "w") as fh:
        fh.write("#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence\n")
        fh.write("v1\tBRCA1\t100\tsynonymous_variant\n")
    try:
        red.reduce_tab(tab, str(tmp_path / "ref.tsv"))
        raise AssertionError("empty catalog must be refused")
    except ValueError as exc:
        assert "empty aa-match reference" in str(exc)


def test_legacy_two_column_reference_loads_residue_only(tmp_path):
    path = str(tmp_path / "legacy.tsv")
    with open(path, "w") as fh:
        fh.write("BRCA1\t100\n")
    ref = aam.load_reference(path)
    assert ("BRCA1", "100") in ref.residues
    assert not ref.changes



def test_second_pass_is_idempotent(tmp_path):
    """Re-annotating already-annotated output must replace the headers and
    INFO keys, never duplicate them."""
    ref = _ref(changes=[("BRCA1", "100", "H")])
    vin = str(tmp_path / "in.vcf")
    first = str(tmp_path / "first.vcf")
    second = str(tmp_path / "second.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    aam.annotate(vin, first, ref)
    aam.annotate(first, second, ref)
    text = open(second).read()
    assert text.count("##INFO=<ID=ClinVar_path_aa_match,") == 1
    assert text.count("##INFO=<ID=ClinVar_path_aa_change_match,") == 1
    record = [l for l in text.splitlines() if not l.startswith("#")][0]
    info = record.split("\t")[7]
    assert info.count("ClinVar_path_aa_match=") == 1
    assert info.count("ClinVar_path_aa_change_match=") == 1
    assert open(first).read() == text


def test_incompatible_reference_residue_matches_nothing(tmp_path):
    """A transcript whose reference amino acid at the position differs is a
    different isoform numbering: neither PS1- nor PM5-style evidence may
    fire on it."""
    # Catalog knows R100H on this gene; _csq writes Amino_acids R/H.
    ref = _ref(changes=[("BRCA1", "100", "R", "H")])
    matching = _csq("missense_variant", "BRCA1", "100")          # R/H
    wrong_ref = matching.replace("R/H", "G/H")                    # G100H
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([matching, wrong_ref]))
    stats = aam.annotate(vin, vout, ref)
    flags = []
    for line in open(vout):
        if line.startswith("#"):
            continue
        kv = dict(x.split("=", 1) for x in line.rstrip("\n").split("\t")[7].split(";") if "=" in x)
        flags.append((kv["ClinVar_path_aa_match"], kv["ClinVar_path_aa_change_match"]))
    assert flags == [("1", "1"), ("0", "0")], flags
    assert stats["matched"] == 1 and stats["change_matched"] == 1


def test_builder_script_requests_the_amino_acids_field():
    """The change-level flag was silently dead in production because the
    catalog builder never asked VEP for Amino_acids."""
    script = os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "build_clinvar_aa_reference.sh")
    text = open(script).read()
    fields_lines = [l for l in text.splitlines() if "--fields" in l]
    assert fields_lines, "builder no longer sets --fields?"
    assert all("Amino_acids" in l for l in fields_lines), fields_lines
    assert all("Feature" in l for l in fields_lines), fields_lines


def test_transcript_gate_blocks_other_isoform_numbering(tmp_path):
    """A patient CSQ entry from a DIFFERENT transcript is a different
    coordinate system: position 100 there is not the catalog's residue 100
    even when the reference amino acid coincides."""
    ref = aam.Reference()
    ref.add_residue("STAT3", "100", "R", "ENST1")
    ref.changes.add(("STAT3", "100", "R", "H", "ENST1"))
    # _csq writes Feature=ENST0 — an isoform the catalog was NOT numbered
    # against, whose residue 100 coincidentally also reads R/H.
    other_isoform = _csq("missense_variant", "STAT3", "100")
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([other_isoform]))
    stats = aam.annotate(vin, vout, ref)
    assert stats["matched"] == 0 and stats["change_matched"] == 0
    # The SAME entry from the catalog's own transcript matches.
    picked = other_isoform.replace("ENST0", "ENST1")
    _write(vin, _vcf([picked]))
    stats = aam.annotate(vin, vout, ref)
    assert stats["matched"] == 1 and stats["change_matched"] == 1


def test_transcript_gate_strips_versions(tmp_path):
    """ENST versions bump with cache releases; identity is the unversioned
    accession on both sides."""
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text("STAT3\t100\tR\tH\tENST0.5\n")
    loaded = aam.load_reference(str(catalog))
    assert set(loaded.residues[("STAT3", "100")]) == {"ENST0"}
    assert loaded.transcript_aware
    versioned = _csq("missense_variant", "STAT3", "100").replace(
        "ENST0", "ENST0.8"
    )
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([versioned]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 1 and stats["change_matched"] == 1


def test_legacy_catalog_without_transcript_keeps_matching(tmp_path):
    """4-column catalogs predate the transcript column: they keep the
    residue-guard behavior instead of matching nothing."""
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text("STAT3\t100\tR\tH\n")
    loaded = aam.load_reference(str(catalog))
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "STAT3", "100")]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 1 and stats["change_matched"] == 1


def test_change_evidence_never_crosses_transcripts_at_shared_keys(tmp_path):
    """Two catalog rows at one (SYMBOL,pos) from different picked
    transcripts must stay separate: a change recorded on ENSTB's numbering
    is not PS1-style evidence for a patient entry on ENSTA."""
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text(
        "GENE\t100\tR\tH\tENSTA\n"
        "GENE\t100\tR\tW\tENSTB\n"
    )
    loaded = aam.load_reference(str(catalog))
    # Patient entry on ENSTA producing R/W — the change known only on ENSTB.
    entry = _csq("missense_variant", "GENE", "100").replace(
        "ENST0", "ENSTA").replace("R/H", "R/W")
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([entry]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 1        # same residue on ENSTA: PM5-style OK
    assert stats["change_matched"] == 0  # R100W belongs to ENSTB's numbering
    # The same change on its own transcript fires.
    entry_b = entry.replace("ENSTA", "ENSTB")
    _write(vin, _vcf([entry_b]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 1 and stats["change_matched"] == 1


def test_stray_legacy_row_does_not_reopen_the_gate(tmp_path):
    """Transcript-awareness is a property of the FILE: one hand-appended
    4-column row must not reopen cross-transcript matching at its key."""
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text(
        "GENE\t100\tR\tH\tENSTA\n"
        "GENE\t100\tR\tH\n"
    )
    loaded = aam.load_reference(str(catalog))
    assert loaded.transcript_aware
    # Patient entry from ENSTC — a transcript the catalog never numbered.
    entry = _csq("missense_variant", "GENE", "100").replace("ENST0", "ENSTC")
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([entry]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 0 and stats["change_matched"] == 0
    assert stats["tx_gate_blocked"] == 1


def test_unknown_patient_transcript_spellings_are_symmetric(tmp_path):
    """Feature '' and Feature '-' both mean unknown: both fall back to the
    residue guard instead of one hard-blocking."""
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text("GENE\t100\tR\tH\tENSTA\n")
    loaded = aam.load_reference(str(catalog))
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    for unknown in ("", "-"):
        entry = _csq("missense_variant", "GENE", "100").replace(
            "|ENST0|", f"|{unknown}|")
        _write(vin, _vcf([entry]))
        stats = aam.annotate(vin, vout, loaded)
        assert stats["matched"] == 1, unknown
        assert stats["change_matched"] == 1, unknown


def test_reducer_emits_the_transcript_column(tmp_path):
    """The catalog must record WHICH transcript numbered each position,
    version-stripped."""
    tab = tmp_path / "vep.tsv"
    tab.write_text(
        "#Uploaded_variation\tSYMBOL\tProtein_position\tConsequence"
        "\tAmino_acids\tFeature\n"
        "v1\tSTAT3\t100\tmissense_variant\tR/H\tENST00000264657.10\n"
    )
    out = tmp_path / "catalog.tsv"
    assert red.reduce_tab(str(tab), str(out)) == 1
    assert out.read_text() == "STAT3\t100\tR\tH\tENST00000264657\n"


def test_provenance_details_and_exact_allele_exclusion(tmp_path):
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text(
        "#gene\tprotein_position\tref_aa\talt_aa\ttranscript\trecord_id"
        "\tsource_allele\tclassification\tdisease\n"
        "BRCA1\t100\tR\tH\tENST0\tRCV 1\t1:999:C:T\tPathogenic\tDisease A\n"
    )
    loaded = aam.load_reference(str(catalog))
    assert loaded.has_provenance
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 1 and stats["change_matched"] == 1
    text = open(vout).read()
    assert "##INFO=<ID=ClinVar_path_aa_details,Number=A,Type=String" in text
    record = [line for line in text.splitlines() if not line.startswith("#")][0]
    info = dict(item.split("=", 1) for item in record.split("\t")[7].split(";") if "=" in item)
    assert info["ClinVar_path_aa_details"].startswith(
        "change|RCV%201|1%3A999%3AC%3AT|Pathogenic|BRCA1|ENST0|100|R|H|Disease%20A"
    )

    # The same catalog record is exact-allele evidence for this query and is
    # therefore not duplicated as PS1/PM5-style protein evidence.
    catalog.write_text(
        "BRCA1\t100\tR\tH\tENST0\tRCV1\t1:1001:C:A\tPathogenic\tDisease A\n"
    )
    loaded = aam.load_reference(str(catalog))
    aam.annotate(vin, vout, loaded)
    record = [line for line in open(vout) if not line.startswith("#")][0]
    info = dict(item.split("=", 1) for item in record.split("\t")[7].split(";") if "=" in item)
    assert info["ClinVar_path_aa_match"] == "0"
    assert info["ClinVar_path_aa_change_match"] == "0"
    assert info["ClinVar_path_aa_details"] == "."


def test_exact_allele_exclusion_survives_padded_and_lowercase_representations(tmp_path):
    """Audit repro (H5): the catalog's source allele is minimal (ClinVar VCF
    form); a padded or lowercase patient representation of the SAME allele
    used to bypass the exclusion and let the record support itself."""
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text(
        "BRCA1\t100\tR\tH\tENST0\tRCV1\t1:1001:C:A\tPathogenic\tDisease A\n"
    )
    loaded = aam.load_reference(str(catalog))
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    text = _vcf([_csq("missense_variant", "BRCA1", "100")])
    # Same allele, padded by one shared trailing base and lowercase:
    # 1:1001 cg>ag  ==  1:1001 C>A.
    padded = text.replace("1\t1001\t.\tC\tA\t", "1\t1001\t.\tcg\tag\t")
    assert padded != text
    _write(vin, padded)
    aam.annotate(vin, vout, loaded)
    record = [line for line in open(vout) if not line.startswith("#")][0]
    info = dict(item.split("=", 1) for item in record.split("\t")[7].split(";") if "=" in item)
    assert info["ClinVar_path_aa_match"] == "0"
    assert info["ClinVar_path_aa_change_match"] == "0"
    # A genuinely different allele at the same residue still counts.
    different = text.replace("1\t1001\t.\tC\tA\t", "1\t1001\t.\tC\tT\t")
    _write(vin, different)
    aam.annotate(vin, vout, loaded)
    record = [line for line in open(vout) if not line.startswith("#")][0]
    info = dict(item.split("=", 1) for item in record.split("\t")[7].split(";") if "=" in item)
    assert info["ClinVar_path_aa_match"] == "1"
    assert aam._canonical_allele_text("chr1:100:AT:ATT") == "1:100:A:AT"
    assert aam._canonical_allele_text("1:298:atg:atc") == "1:300:G:C"
    assert aam._canonical_allele_text("M:8993:T:G") == "MT:8993:T:G"
    assert aam._canonical_allele_text("1:100:A:<DEL>") == "1:100:A:<DEL>"


def test_provenance_residue_kind_is_disjoint_from_change(tmp_path):
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text(
        "BRCA1\t100\tR\tW\tENST0\tRCV2\t1:999:C:G\tLikely pathogenic\t\n"
    )
    loaded = aam.load_reference(str(catalog))
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([_csq("missense_variant", "BRCA1", "100")]))
    aam.annotate(vin, vout, loaded, info_key="GenIA_path_aa_match", source_label="GenIA")
    record = [line for line in open(vout) if not line.startswith("#")][0]
    info = dict(item.split("=", 1) for item in record.split("\t")[7].split(";") if "=" in item)
    assert info["GenIA_path_aa_match"] == "1"
    assert info["GenIA_path_aa_change_match"] == "0"
    assert info["GenIA_path_aa_details"].startswith("residue|")


def test_provenance_catalog_requires_complete_amino_acid_change(tmp_path):
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text(
        "BRCA1\t100\tR\tH\tENST0\tRCV1\t1:999:C:T\tPathogenic\t\n"
    )
    loaded = aam.load_reference(str(catalog))
    incomplete = _csq("missense_variant", "BRCA1", "100").replace("R/H", "R")
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, _vcf([incomplete]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 0 and stats["change_matched"] == 0

    missing_transcript = _csq("missense_variant", "BRCA1", "100").replace(
        "|ENST0|", "||"
    )
    _write(vin, _vcf([missing_transcript]))
    stats = aam.annotate(vin, vout, loaded)
    assert stats["matched"] == 0 and stats["change_matched"] == 0


def test_multiallelic_without_allele_num_is_not_broadcast(tmp_path):
    catalog = tmp_path / "catalog.tsv"
    catalog.write_text(
        "BRCA1\t100\tR\tH\tENST0\tRCV1\t1:999:C:T\tPathogenic\t\n"
    )
    vcf = _vcf([_csq("missense_variant", "BRCA1", "100")]).replace(
        "\tC\tA\t", "\tC\tA,G\t"
    )
    vin = str(tmp_path / "in.vcf"); vout = str(tmp_path / "out.vcf")
    _write(vin, vcf)
    aam.annotate(vin, vout, aam.load_reference(str(catalog)))
    record = [line for line in open(vout) if not line.startswith("#")][0]
    info = dict(item.split("=", 1) for item in record.split("\t")[7].split(";") if "=" in item)
    assert info["ClinVar_path_aa_match"] == "0,0"
    assert info["ClinVar_path_aa_change_match"] == "0,0"
    assert info["ClinVar_path_aa_details"] == ".,."

if __name__ == "__main__":
    import tempfile, pathlib, inspect
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            sig = inspect.signature(fn)
            with tempfile.TemporaryDirectory() as td:
                if sig.parameters:
                    fn(pathlib.Path(td))
                else:
                    fn()
            print(f"PASS  {name}")
            passed += 1
    print(f"\n{passed} tests passed")
