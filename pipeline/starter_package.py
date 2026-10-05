"""Pinned component installation: no giant archive, no installer payload data.

The application ships the release manifest, not a mutable remote manifest.
Each component is verified in staging and published atomically. Failed jobs
keep their resumable downloads; existing installed versions are never removed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "config/essential-annotations.json"
KINDS = {"alphamissense", "cadd", "spliceai"}


def load_plan(path=PLAN):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(plan, dict) or plan.get("schema") != "guide-iei.essential-annotations/v1"
            or plan.get("assembly") != "GRCh38" or plan.get("vep_release") != 113):
        raise ValueError("Unsupported essential-annotation release manifest")
    if plan.get("release_status") != "ready":
        raise ValueError(plan.get("message") or "Essential annotation package is not released yet")
    components = plan.get("components", [])
    if (not isinstance(components, list) or len(components) != 3
            or any(not isinstance(c, dict) for c in components)
            or {c.get("id") for c in components} != KINDS):
        raise ValueError("Essential package must contain all three validated starter components")
    for component in components:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", component.get("version", "")):
            raise ValueError("Invalid starter component version")
        names = set()
        files = component.get("files", [])
        if not isinstance(files, list) or any(not isinstance(a, dict) for a in files):
            raise ValueError("Invalid starter asset list")
        for asset in files:
            name = asset.get("name", "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", name) or name in names:
                raise ValueError("Invalid or duplicate starter component filename")
            names.add(name)
            if not re.fullmatch(r"[a-f0-9]{64}", asset.get("sha256", "")):
                raise ValueError("Every starter asset requires a pinned SHA-256")
            if type(asset.get("size_bytes")) is not int or asset["size_bytes"] <= 0:
                raise ValueError("Every starter asset requires a positive byte size")
            url = urlparse(asset.get("url", ""))
            if url.scheme != "https" or url.hostname != "huggingface.co" or url.username or url.fragment:
                raise ValueError("Starter assets must use public HTTPS Hugging Face URLs")
            if (url.query or url.port is not None or not re.fullmatch(
                    r"/datasets/[^/]+/[^/]+/resolve/[0-9a-f]{40}/.+", url.path)):
                raise ValueError("Starter assets require an immutable Hugging Face commit revision")
        score = component["id"] + (".vcf.gz" if component["id"] == "spliceai" else ".tsv.gz")
        required = {score, score + ".tbi", "preparation.json", "LICENSE-NOTICES.json"}
        if component["id"] != "spliceai":
            required.add("indexed.manifest.json")
        if not required <= names:
            raise ValueError("Starter component is missing data, index, provenance or license notices")
    return plan


def component_path(root, component):
    return Path(root) / "starter" / component["id"] / component["version"]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def component_valid(directory, component, *, strict=False):
    try:
        receipt = json.loads((directory / "installation.json").read_text())
        if receipt.get("component") != component:
            return False
        for asset in component["files"]:
            path = directory / asset["name"]
            stat = path.stat()
            if path.is_symlink() or not path.is_file() or stat.st_size != asset["size_bytes"]:
                return False
            if strict or receipt.get("mtimes", {}).get(asset["name"]) != stat.st_mtime_ns:
                if sha256(path) != asset["sha256"]:
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def install(root, plan, run=subprocess.run):
    root = Path(root)
    for component in plan["components"]:
        destination = component_path(root, component)
        if component_valid(destination, component, strict=True):
            print(f"=== {component['id']} starter already verified ===", flush=True)
            continue
        stage = destination.with_name(destination.name + ".preparing")
        if stage.is_symlink() or destination.is_symlink():
            raise ValueError("Starter installation directories must not be symbolic links")
        stage.mkdir(parents=True, exist_ok=True)
        needed = sum(a["size_bytes"] for a in component["files"]) + 512 * 1024 ** 2
        if shutil.disk_usage(stage).free < needed:
            raise ValueError(f"Not enough free space to prepare {component['id']}: need {needed:,} bytes")
        print(f"=== Preparing {component['id']} starter annotations ===", flush=True)
        for asset in component["files"]:
            target = stage / asset["name"]
            if target.is_symlink():
                raise ValueError("Starter staging files must not be symbolic links")
            if target.exists() and (target.stat().st_size != asset["size_bytes"] or sha256(target) != asset["sha256"]):
                import uuid
                target.rename(target.with_name(target.name + ".invalid-" + uuid.uuid4().hex[:12]))
            run([sys.executable, str(ROOT / "scripts/parallel_fetch.py"), asset["url"], str(target),
                 "--connections", "4"], check=True)
            if target.stat().st_size != asset["size_bytes"] or sha256(target) != asset["sha256"]:
                raise ValueError(f"Starter asset verification failed: {asset['name']}")
        receipt = {"schema": "guide-iei.starter-installation/v1", "component": component,
                   "mtimes": {a["name"]: (stage / a["name"]).stat().st_mtime_ns for a in component["files"]}}
        (stage / "installation.json").write_text(json.dumps(receipt, indent=2) + "\n")
        # Preserve a damaged old copy for recovery, never overwrite it in place.
        if destination.exists():
            import uuid
            destination.rename(destination.with_name(destination.name + ".replaced-" + uuid.uuid4().hex[:12]))
        os.replace(stage, destination)
    return {c["id"]: str(component_path(root, c)) for c in plan["components"]}


def installed_paths(root, plan):
    return {c["id"]: component_path(root, c) for c in plan["components"]
            if component_valid(component_path(root, c), c)}


def apply_starters(config, paths, resolve):
    """Activate only a complete verified package; preserve installed full tables.

    Called before per-job user overrides. This never edits the user's config.
    """
    from .spliceai_dataset import FORMAT, prefer_legacy
    splice = (config.get("plugins") or {}).get("SpliceAI", {})
    prefer_legacy(splice, resolve)
    if set(paths) != KINDS:
        return config
    plugins = config.setdefault("plugins", {})
    def indexed(path):
        return path and path.is_file() and any(Path(str(path) + suffix).is_file() for suffix in (".tbi", ".csi"))
    db = plugins.setdefault("dbNSFP", {})
    db["required"] = False
    full_db = resolve(db.get("path"))
    if not indexed(full_db):
        db["enabled"] = False
    for kind, name in (("alphamissense", "AlphaMissenseStarter"), ("cadd", "CADDStarter")):
        block = plugins.setdefault(name, {})
        if block.get("enabled") is False:
            continue
        # A user-configured provider path is an explicit choice, not our default.
        if not block.get("file"):
            block.update(enabled=True, required=True, file=str(paths[kind] / (kind + ".tsv.gz")),
                         manifest=str(paths[kind] / "indexed.manifest.json"))
    splice = plugins.setdefault("SpliceAI", {})
    full_splice = resolve(splice.get("snv"))
    sharded = splice.get("format") == FORMAT and full_splice and full_splice.exists()
    if not sharded and not indexed(full_splice):
        splice.pop("format", None)
        splice.update(snv=str(paths["spliceai"] / "spliceai.vcf.gz"),
                      coverage="MANE v1.5 essential donor/acceptor SNVs only; D=500 M=1",
                      coverage_scope="essential_splice_sites", version="MANE v1.5 D=500 M=1",
                      source_manifest=str(paths["spliceai"] / "preparation.json"))
    return config
