"""Essential resource contract shared by startup, installer and service."""
from pathlib import Path

from pipeline.starter_package import installed_paths, load_plan

# Conservative transient allowances, not advertised download sizes. A retry
# of one small snapshot must not demand the space of a whole clean install.
REFERENCE_ALLOWANCE_GIB = {"VEP cache": 35, "Reference genome": 2,
                           "LOFTEE": 20, "Gene models": 3, "ClinVar": 2, "ClinGen": 1}


def reference_allowance(missing):
    return sum(REFERENCE_ALLOWANCE_GIB[name] for name in missing) * 1024 ** 3


def required_files(config, resolve):
    """Keep LOFTEE/PTC and coding-region preparation unchanged."""
    ref, plugins = config["reference"], config["plugins"]
    cache = resolve(ref["vep_cache_dir"])
    fasta = resolve(ref["fasta"]["path"])
    result = {"VEP cache": [cache / ".homo_sapiens_vep_113_GRCh38.complete",
                             cache / "homo_sapiens/113_GRCh38"],
              "Reference genome": [fasta, Path(str(fasta) + ".fai"), Path(str(fasta) + ".gzi")],
              "LOFTEE": [resolve(plugins["LoF"][key]) for key in
                         ("human_ancestor_fa", "conservation_file", "gerp_bigwig")],
              "Gene models": [resolve(config["post_processing"]["loftee_ptc_50bp"]["gtf"])],
              "ClinVar": [resolve(config["custom_tracks"]["ClinVar"]["file"])],
              "ClinGen": [resolve(config["clingen_erepo"][key]) for key in ("database", "vcf", "manifest")]}
    ancestor = result["LOFTEE"][0]
    result["LOFTEE"] += [Path(str(ancestor) + suffix) for suffix in (".fai", ".gzi")]
    clinvar = result["ClinVar"][0]
    result["ClinVar"] += [Path(str(clinvar) + ".tbi"), Path(str(clinvar) + ".provenance.json")]
    return result


def missing_resources(config, resolve):
    return [name for name, paths in required_files(config, resolve).items()
            if any(not p.exists() or (p.is_file() and p.stat().st_size == 0
                   and not p.name.endswith(".complete")) for p in paths)]


def package_status(root, plan_path):
    try:
        plan = load_plan(plan_path)
        paths = installed_paths(root, plan)
        pending = [c for c in plan["components"] if c["id"] not in paths]
        return {"available": len(paths) == 3, "installable": True,
                "missing": [c["id"] for c in pending], "message": "",
                "download_bytes": sum(a["size_bytes"] for c in pending for a in c["files"])}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"available": False, "installable": False, "missing": [],
                "message": str(exc), "download_bytes": 0}
