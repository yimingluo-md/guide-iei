from contextlib import closing
import csv
import gzip
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from local_service import element_gene_links as links
from local_service.cohort_store import CohortStore
from local_service.test_cohort_store import write_vcf
from scripts.install_encode_re2g import install, records


class ElementGeneTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        source = self.root / "source.gz"
        with gzip.open(source, "wt") as stream:
            stream.write("#chr\tstart\tend\tname\tTargetGeneEnsemblID\tTargetGene\tTargetGeneTSS\tdistanceToTSS.Feature\tScore\tCellType\n")
            for target in ("DISTANT", "SECOND"):
                for start, end in [(99, 102), (102, 104)]:
                    stream.write(f"chr1\t{start}\t{end}\tinterval\tENSG_{target}\t{target}\t900000\t899900\t0.75\tT cell\n")
            for chrom, start, end in [("cHrM", 99, 102), ("mt", 102, 104), ("ChrX", 99, 102)]:
                stream.write(f"{chrom}\t{start}\t{end}\tinterval\tENSG_MIXED\tMIXED\t900000\t899900\t0.75\tT cell\n")
        entry = dict(accession="ENCFFfixture", annotation="ENCSRfixture", ontology_id="CL:test",
                     systems="immune system", model="dhs_only", context="T cell", url=source.as_uri(),
                     bytes=source.stat().st_size, md5=hashlib.md5(source.read_bytes()).hexdigest())
        self.catalog = self.root / "catalog.tsv"
        with self.catalog.open("w") as stream:
            writer = csv.DictWriter(stream, entry.keys(), delimiter="\t")
            writer.writeheader()
            writer.writerow(entry)
        install(self.root, self.catalog)
        self.path = self.root / links.DATABASE

    def test_coordinate_boundaries_match_in_all_three_views(self):
        predicate = links.region_matcher(links.target_regions(self.path, "distant"))
        for pos, ref, alt, hit in [(99,"A","T",False), (100,"A","T",True),
                                    (104,"A","T",True), (105,"A","T",False),
                                    (99,"AC","A",True), (104,"AC","A",False),
                                    (99,"A","AT",False), (100,"A","AT",True),
                                    (102,"A","AT",False), (101,"A","<DEL>",False)]:
            with self.subTest(pos=pos, ref=ref, alt=alt):
                v = dict(chrom="1", pos=pos, ref=ref, alt=alt, key="v")
                self.assertEqual(predicate("1", pos, ref, alt), hit)
                result = links.lookup(self.path, v)
                self.assertEqual({r["symbol"] for r in result["links"]}, {"DISTANT","SECOND"} if hit else set())
                batch = links.filter_targets(self.path, dict(variants=[v],genes=["distant"]))
                self.assertEqual(batch["matches"], ["v"] if hit else [])

    def test_filter_returns_distinct_matching_target_symbols(self):
        variants = [dict(chrom="1", pos=101, ref="A", alt="G", key="v")]
        for genes in (["distant", "SECOND"], ["distant", "SECOND"] + ["OTHER" + str(i) for i in range(1000)]):
            result = links.filter_targets(self.path, dict(variants=variants, genes=genes))
            self.assertEqual(result, {"matches": ["v"], "targets": {"v": ["DISTANT", "SECOND"]}})
        self.assertEqual(links.filter_targets(self.path, dict(variants=[], genes=[]))["targets"], {})
        with self.assertRaisesRegex(ValueError, "Install ENCODE-rE2G"):
            links.filter_targets(self.root / "absent", dict(variants=[], genes=[]))

    def test_contig_aliases_in_new_and_legacy_indexes(self):
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(set(db.execute("SELECT DISTINCT chrom FROM links")), {("1",), ("X",), ("MT",)})
        for legacy in (False, True):
            if legacy:
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("UPDATE links SET chrom=CASE WHEN start=99 THEN 'cHrMT' ELSE 'M' END WHERE chrom='MT'")
                    db.execute("UPDATE links SET chrom='x' WHERE chrom='X'")
            variants = [dict(key=f"{c}/{p}", chrom=c, pos=p, ref="A", alt="T")
                        for c in (" M ", "Mt", "CHRmt", "ChRx") for p in (100, 104, 105)]
            expected = {v["key"] for v in variants if v["pos"] == 100 or (v["pos"] == 104 and v["chrom"] != "ChRx")}
            with self.subTest(legacy=legacy):
                self.assertEqual({v["key"] for v in variants if links.lookup(self.path, v)["links"]}, expected)
                for extra in ([], [f"other{i}" for i in range(40)]):
                    batch = links.filter_targets(self.path, dict(variants=variants, genes=["mixed", *extra]))
                    self.assertEqual(set(batch["matches"]), expected)
                regions = links.target_regions(self.path, "mixed")
                self.assertEqual(regions["MT"], ([99, 102], [102, 104]))
                predicate = links.region_matcher(regions)
                self.assertEqual({v["key"] for v in variants if predicate(v["chrom"], v["pos"], "A", "T")}, expected)

    def test_search_status_checks_exact_symbol_in_active_index(self):
        result = links.target_search_status(self.path, "distant")
        self.assertTrue(result["known"])
        self.assertEqual(result["index"], [links.PROVIDER, links.RELEASE, hashlib.sha256(self.catalog.read_bytes()).hexdigest()])
        for term in ("", "dist", "banana"):
            self.assertFalse(links.target_search_status(self.path, term)["known"])
        with self.assertRaisesRegex(ValueError, "Install ENCODE-rE2G"):
            links.target_search_status(self.root / "absent", "DISTANT")

    def test_batch_shortlist_respects_multiple_chromosomes_and_insertion_boundaries(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO links SELECT '2',start,end,element,gene,symbol,tss,distance,score,source,context_label "
                       "FROM links WHERE symbol='DISTANT'")
        variants = [dict(chrom=c, pos=p, ref="A", alt=a, key=f"{c}/{p}/{a}")
                    for c in ("1","2","3") for p in range(95,110) for a in ("T","AT","<DEL>")]
        genes = ["distant", "second"] + [f"unknown{i}" for i in range(50)]
        batch = links.filter_targets(self.path, dict(variants=variants, genes=genes))
        expected = {}
        for v in variants:
            symbols = sorted({r["symbol"] for r in links.lookup(self.path, v)["links"]})
            if symbols:
                expected[v["key"]] = symbols
        self.assertEqual(batch["targets"], expected)

    def test_encode_schema_error_identifies_accession_and_bad_line(self):
        path = self.root / "ENCFFexample"
        with gzip.open(path, "wt") as out:
            out.write("#chr\tstart\n")
        with self.assertRaisesRegex(ValueError, "ENCFFexample: missing ENCODE columns: .*Score"):
            list(records(path, 1))
        with gzip.open(self.root / "source.gz", "rt") as src, gzip.open(path, "wt") as out:
            out.write(src.readline())
            out.write(src.readline())
            out.write("chr1\tbad\n")
        with self.assertRaisesRegex(ValueError, "ENCFFexample, line 3"):
            list(records(path, 1))

    def test_cohort_keeps_vep_gene_when_matching_a_distant_target(self):
        source = self.root / "sample.vcf.gz"
        write_vcf(source)
        store = CohortStore(self.root / "cohort.sqlite")
        store.import_vcf(source)
        rows = store.query(dict(mode="target_gene", gene="DISTANT", impacts=[]), links_path=self.path)["rows"]
        self.assertEqual({(r["pos"], r["gene"]) for r in rows}, {(100, "NFKB1")})

    def test_clinical_transcripts_in_region_and_target_queries(self):
        source = self.root / "regulatory.vcf"
        annotations = [
            "intergenic_variant|MODIFIER||||||",
            "regulatory_region_variant&TF_binding_site_variant|MODIFIER|||RegulatoryFeature|element||",
            "regulatory_region_variant|MODIFIER|||Transcript|element||",
            "intergenic_variant|MODIFIER||ENSG1||||",
            "regulatory_region_variant|MODIFIER|NEARBY||RegulatoryFeature|element||",
            "intergenic_variant&intron_variant|MODIFIER||||||",
            "intron_variant|MODIFIER||||||",
            "regulatory_region_variant|MODIFIER||||ENST00000001||",
            "regulatory_region_variant|MODIFIER||||ENSR00000001||",
            "missense_variant|MODERATE|CODING|ENSG2|Transcript|picked||1,"
            "G|missense_variant|MODERATE|CODING|ENSG2|Transcript|mane|NM_TEST|",
            "missense_variant|MODERATE|CODING|ENSG2|Transcript|fallback||1",
        ]
        source.write_text("\n".join([
            "##fileformat=VCFv4.2", "##reference=GRCh38", "##contig=<ID=1,length=248956422>",
            '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|'
            'SYMBOL|Gene|Feature_type|Feature|MANE_SELECT|PICK">',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC",
            *(f"1\t{pos}\t.\tA\tG\t99\tPASS\tCSQ=G|{annotation}\tGT\t0/1"
              for pos, annotation in enumerate(annotations, 100)), "",
        ]))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE links SET end=120 WHERE symbol='DISTANT'")
        store = CohortStore(self.root / "cohort.sqlite")
        store.import_vcf(source)
        for query in [dict(mode="region", region="1:100-120"), dict(mode="target_gene", gene="DISTANT")]:
            with self.subTest(query=query):
                self.assertEqual(store.query(query, links_path=self.path)["variants"], len(annotations))
                clinical = dict(query, mane_only=True)
                rows = store.query(clinical, links_path=self.path)["rows"]
                self.assertEqual({r["pos"]: r["transcript"] for r in rows},
                                 {100: "", 101: "element", 108: "ENSR00000001", 109: "mane", 110: "fallback"})
                self.assertEqual(next(r for r in rows if r["pos"] == 101)["feature_type"], "RegulatoryFeature")
                coding = store.query(dict(clinical, impacts=["MODERATE"]), links_path=self.path)
                self.assertEqual({r["pos"] for r in coding["rows"]}, {109, 110})

    def test_target_gene_bounds_and_single_overlap_pass(self):
        source = self.root / "bounded.vcf"
        write_vcf(source)
        rows = source.read_text().splitlines()
        record = next(line for line in rows if line.startswith("1\t100\t"))
        # The deletion starts before the linked interval. The prefix SNV does not overlap.
        deletion = record.replace("1\t100\trsExact\tA\tG", "1\t80\t.\t" + "A" * 25 + "\tA")
        prefix = record.replace("1\t100\t", "1\t5\t")
        source.write_text("\n".join([*(r for r in rows if r.startswith("#")), prefix, deletion, record]) + "\n")
        store = CohortStore(self.root / "bounds.sqlite")
        store.import_vcf(source)
        query = dict(mode="target_gene", gene="DISTANT", impacts=[])
        seen = []
        def counted(regions):
            predicate = links.region_matcher(regions)
            def match(chrom, pos, ref, alt):
                seen.append(pos)
                return predicate(chrom, pos, ref, alt)
            return match
        with patch("local_service.cohort_store.region_matcher", counted):
            result = store.query(query, links_path=self.path)
        self.assertEqual(result["variants"], 2)
        self.assertCountEqual(seen, [80, 100])
        plan = store.query(query, links_path=self.path, explain_plan=True)["plan"]
        self.assertTrue(any("pos>?" in step and "pos<?" in step for step in plan), plan)

    def test_direct_target_query_includes_modifier_with_large_profile_filter(self):
        source = self.root / "mitochondrial.vcf"
        write_vcf(source)
        source.write_text(source.read_text().replace("##contig=", "##contig=<ID=chrM,length=16569>\n##contig=")
                          .replace("\n1\t", "\nchrM\t").replace("stop_gained|HIGH", "intergenic_variant|MODIFIER"))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE links SET chrom='M' WHERE chrom='MT'")
        store = CohortStore(self.root / "profiles.sqlite")
        store.import_vcf(source)
        query = dict(mode="target_gene", gene="mixed")
        unfiltered = store.query(query, links_path=self.path)
        self.assertEqual([(r["chrom"], r["pos"], r["impact"]) for r in unfiltered["rows"]], [("MT", 100, "MODIFIER")])
        self.assertEqual(store.query(dict(query, impacts=["HIGH"]), links_path=self.path)["total"], 0)
        profiles = [f"absent{i}" for i in range(33000)]
        query.update(profile_hashes=profiles, analysis_scopes=["exome"] * 2000)
        self.assertEqual(store.query(query, links_path=self.path)["total"], 0)
        profiles.append("full:exome")
        filtered = store.query(query, links_path=self.path)
        self.assertEqual(filtered["rows"], unfiltered["rows"])
        self.assertEqual(filtered["total"], 1)
        small = dict(query, profile_hashes=["full:exome"], analysis_scopes=["exome"])
        self.assertEqual(store.query(query, links_path=self.path, explain_plan=True)["sql"],
                         store.query(small, links_path=self.path, explain_plan=True)["sql"])

    def test_invalid_partial_index_is_rebuilt(self):
        root = self.root / "restart"
        final = root / links.DATABASE
        final.parent.mkdir(parents=True)
        digest = hashlib.sha256(self.catalog.read_bytes()).hexdigest()
        stage = final.with_name(digest + ".partial.sqlite3")
        for invalid in ("corrupt", "wrong_schema"):
            with self.subTest(invalid=invalid):
                if invalid == "corrupt":
                    stage.write_bytes(b"not a SQLite database")
                else:
                    with closing(sqlite3.connect(stage)) as db:
                        db.execute("CREATE TABLE sources(accession TEXT)")
                install(root, self.catalog)
                self.assertTrue(links.lookup(final, dict(chrom="1", pos=100, ref="A", alt="T"))["links"])
                self.assertFalse(stage.exists())
                final.unlink()

    def test_interrupted_install_reuses_verified_sources(self):
        root = self.root / "interrupted"
        with self.catalog.open() as handle:
            first = next(csv.DictReader(handle, delimiter="\t"))
        second_path = self.root / "second.gz"
        second = dict(first, accession="ENCFFsecond", url=second_path.as_uri())
        with self.catalog.open("w") as handle:
            writer = csv.DictWriter(handle, first.keys(), delimiter="\t")
            writer.writeheader()
            writer.writerows([first, second])
        second_path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Checksum"):
            install(root, self.catalog)
        self.assertFalse(links.installed(root / links.DATABASE))
        stage, = (root / links.DATABASE).parent.glob("*.partial.sqlite3")
        with closing(sqlite3.connect(stage)) as db, db:
            db.execute("UPDATE links SET chrom='M' WHERE chrom='MT'")
        (self.root / "source.gz").replace(second_path)
        with patch("scripts.install_encode_re2g.sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                install(root, self.catalog)
        install(root, self.catalog)
        rows = links.lookup(root / links.DATABASE, dict(chrom="1", pos=100, ref="A", alt="T"))["links"]
        self.assertEqual({r["accession"] for r in rows}, {"ENCFFfixture", "ENCFFsecond"})
        self.assertEqual(len(rows), 4)
        mitochondrial = links.lookup(root / links.DATABASE, dict(chrom="chrM", pos=100, ref="A", alt="T"))
        self.assertCountEqual([r["accession"] for r in mitochondrial["links"]], ["ENCFFfixture", "ENCFFsecond"])

    def test_another_provider_preserves_its_own_provenance(self):
        other = dict(label="Second model", score_label="Source-specific score", source_base="",
                     database="second/v2/links.sqlite3", release="v2")
        with patch.dict(links.PROVIDERS, second=other):
            path = links.provider_index(self.root, {"provider": "second"})
            path.parent.mkdir(parents=True)
            with closing(links.open_index(self.path)) as original, closing(sqlite3.connect(path)) as db:
                original.backup(db)
                with db:
                    db.executemany("UPDATE metadata SET value=? WHERE key=?", [("second","provider"),("v2","release")])
            result = links.lookup(path, dict(chrom="1",pos=100,ref="A",alt="T"))
            self.assertEqual((result["label"],result["release"],result["score_label"]), ("Second model","v2","Source-specific score"))
            self.assertEqual(result["links"][0]["score"], 0.75)
            with self.assertRaises(ValueError):
                links.provider_index(self.root, {"provider": "/tmp/unknown"})

    def test_invalid_download_is_never_published(self):
        other = self.root / "other"
        (self.root / "source.gz").write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "Checksum"):
            install(other, self.catalog)
        self.assertFalse(links.installed(other / links.DATABASE))
        self.assertTrue(links.installed(self.path))
