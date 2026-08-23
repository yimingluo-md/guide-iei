#!/usr/bin/env python3
"""Build a VEP command line from the annotation config.

Reads the YAML config, validates that each enabled source's reference file
exists, and emits the `vep` argument list. A source whose file is missing is
SKIPPED with a warning (so a partially-downloaded reference set still runs)
unless it is marked ``required: true``.

Two path spaces are in play:
  * HOST paths      -- where files live on the user's machine (from the config)
  * CONTAINER paths -- where those files are mounted inside the container

`build_vep_command()` returns both the argv (using CONTAINER paths) and the
set of bind-mounts the runner must create. When ``container=False`` (e.g. a
native conda VEP like the original vep_hg38.sh), host paths are used directly.

Usage:
    python build_vep_command.py --config config/annotation.config.yaml \\
        --input sample.vcf.gz --output sample.vep.vcf.gz [--no-container] [--print]
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("ERROR: pyyaml is required (pip install pyyaml).")


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class Mount:
    host: str        # absolute host directory
    container: str   # container mountpoint
    mode: str        # "ro" or "rw"


@dataclass
class VepPlan:
    argv: list[str] = field(default_factory=list)       # the `vep ...` argument list
    mounts: list[Mount] = field(default_factory=list)   # bind-mounts (ordered, de-duped)
    warnings: list[str] = field(default_factory=list)     # skipped/missing sources
    errors: list[str] = field(default_factory=list)       # required-but-missing

    def command_string(self) -> str:
        parts = []
        for a in self.argv:
            parts.append(a if _safe(a) else _shquote(a))
        return " ".join(parts)


def _safe(s: str) -> bool:
    return all(c.isalnum() or c in "-_/.=,:%+@" for c in s)


def _shquote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# --------------------------------------------------------------------------- #
# Path handling: map a host path into the container mount space
# --------------------------------------------------------------------------- #
class PathMapper:
    """Maps host paths to container paths and records the dirs to bind-mount.

    Each distinct host parent directory is mounted read-only under
    ``/refs/<n>`` inside the container. When ``container=False`` the mapper is a
    pass-through (returns absolute host paths, records no mounts).
    """

    def __init__(self, container: bool, refs_root: str = "/refs", base_dir: str | None = None):
        self.container = container
        self.base_dir = base_dir
        self.refs_root = refs_root
        self._refs: dict[str, str] = {}    # host_dir -> container_dir (read-only ref mounts)
        self._counter = 0

    def absolutize(self, host_path: str) -> str:
        # Config-declared reference paths are relative to the PROJECT root
        # (the config file's parent directory's parent), never the caller's
        # working directory — invoking the runner from elsewhere previously
        # resolved every relative plugin path against the wrong base.
        if os.path.isabs(host_path):
            return host_path
        return os.path.join(self.base_dir, host_path) if self.base_dir else host_path

    def map(self, host_path: str) -> str:
        # Resolve symlinks before choosing the bind mount. Large optional
        # datasets may remain in a lab-managed data directory while a small,
        # ignored link under references/ supplies the configured path.
        ap = os.path.realpath(os.path.abspath(self.absolutize(host_path)))
        if not self.container:
            return ap
        host_dir = os.path.dirname(ap)
        base = os.path.basename(ap)
        if host_dir not in self._refs:
            self._refs[host_dir] = f"{self.refs_root}/{self._counter}"
            self._counter += 1
        return f"{self._refs[host_dir]}/{base}"

    def ref_mounts(self) -> list["Mount"]:
        return [Mount(host=h, container=c, mode="ro") for h, c in self._refs.items()]


# --------------------------------------------------------------------------- #
# Source-file resolver: existence check + required/skip policy
# --------------------------------------------------------------------------- #
def _resolve(plan: VepPlan, mapper: PathMapper, host_path: str,
             label: str, required: bool, check_exists: bool) -> str | None:
    """Return the container path for host_path, or None if it should be skipped.

    Records a warning (skip) or error (required-but-missing) on `plan`.
    """
    host_path = mapper.absolutize(host_path)
    exists = os.path.exists(host_path)
    if check_exists and not exists:
        msg = f"{label}: file not found -> {host_path}"
        if required:
            plan.errors.append(msg)
        else:
            plan.warnings.append(msg + "  [skipped]")
        return None
    return mapper.map(host_path)


def _resolve_indexed(plan: VepPlan, mapper: PathMapper, host_path: str,
                     label: str, required: bool,
                     check_exists: bool) -> str | None:
    """Resolve a BGZF reference and require a sibling tabix/CSI index."""
    cp = _resolve(plan, mapper, host_path, label, required, check_exists)
    if cp is None or not check_exists:
        return cp
    if not any(os.path.isfile(host_path + suffix) for suffix in (".tbi", ".csi")):
        msg = f"{label}: tabix/CSI index not found -> {host_path}.tbi/.csi"
        if required:
            plan.errors.append(msg)
        else:
            plan.warnings.append(msg + "  [skipped]")
        return None
    return cp


# --------------------------------------------------------------------------- #
# Main builder
# --------------------------------------------------------------------------- #
def build_vep_command(cfg: dict, input_vcf: str, output_file: str,
                      container: bool = True,
                      check_exists: bool = True,
                      base_dir: str | None = None) -> VepPlan:
    """Build the VEP argument list + bind-mounts from a parsed config dict."""
    plan = VepPlan()
    mapper = PathMapper(container=container, base_dir=base_dir)

    ref = cfg.get("reference", {})
    run = cfg.get("run", {})
    out = cfg.get("output", {})
    core = cfg.get("core", {})

    # --- input / output (mounted in a writable work dir by the runner) -------
    # The runner mounts the input dir (ro) and output dir (rw); here we use
    # basenames under fixed container mountpoints /work_in and /work_out.
    in_dir = os.path.abspath(os.path.dirname(input_vcf) or ".")
    out_dir = os.path.abspath(os.path.dirname(output_file) or ".")
    io_mounts: list[Mount] = []
    if container:
        in_c = f"/work_in/{os.path.basename(input_vcf)}"
        out_c = f"/work_out/{os.path.basename(output_file)}"
        io_mounts.append(Mount(host=in_dir, container="/work_in", mode="ro"))
        # If output shares the input dir, one rw mount covers both -> rewrite input.
        if out_dir == in_dir:
            io_mounts = [Mount(host=in_dir, container="/work_in", mode="rw")]
            out_c = f"/work_in/{os.path.basename(output_file)}"
        else:
            io_mounts.append(Mount(host=out_dir, container="/work_out", mode="rw"))
    else:
        in_c, out_c = os.path.abspath(input_vcf), os.path.abspath(output_file)

    argv = ["vep", "-i", in_c, "-o", out_c]

    # --- offline / cache -----------------------------------------------------
    argv += ["--offline", "--cache"]
    cache_dir = ref.get("vep_cache_dir")
    if cache_dir:
        argv += ["--dir_cache", mapper.map(cache_dir) if container else os.path.abspath(cache_dir)]
    argv += ["--species", ref.get("species", "homo_sapiens")]
    argv += ["--assembly", ref.get("assembly", "GRCh38")]

    # --- FASTA (required for HGVS/LOFTEE/SpliceAI) ---------------------------
    fa = ref.get("fasta", {})
    if fa.get("enabled", False):
        fp = _resolve(plan, mapper, fa["path"], "reference.fasta",
                      fa.get("required", False), check_exists)
        if fp:
            argv += ["--fasta", fp]

    # --- output format -------------------------------------------------------
    fmt = out.get("format", "vcf")
    if fmt == "vcf":
        argv += ["--vcf"]          # preserves sample GT / zygosity
    elif fmt == "tab":
        argv += ["--tab"]
    if out.get("compress") == "bgzip":
        argv += ["--compress_output", "bgzip"]
    if not out.get("vep_stats", True):
        argv += ["--no_stats"]

    # --- run / perf ----------------------------------------------------------
    if run.get("fork"):
        argv += ["--fork", str(run["fork"])]
    if run.get("buffer_size"):
        argv += ["--buffer_size", str(run["buffer_size"])]
    if run.get("force_overwrite", True):
        argv += ["--force_overwrite"]

    # --- core flags ----------------------------------------------------------
    if core.get("pick", True):
        argv += [core.get("pick_flag", "--pick")]
        pick_order = core.get("pick_order")
        if pick_order:
            if isinstance(pick_order, str):
                order_value = pick_order
            else:
                order_value = ",".join(str(item) for item in pick_order)
            argv += ["--pick_order", order_value]
    if core.get("symbol", True):
        argv += ["--symbol"]
    if core.get("hgvs", True):
        argv += ["--hgvs"]
    if core.get("numbers"):
        argv += ["--numbers"]
    if core.get("canonical"):
        argv += ["--canonical"]
    if core.get("appris"):
        argv += ["--appris"]
    if core.get("tsl"):
        argv += ["--tsl"]
    if core.get("ccds"):
        argv += ["--ccds"]
    if core.get("mane", True):
        argv += ["--mane"]
    if core.get("allele_number"):
        argv += ["--allele_number"]
    if core.get("biotype", True):
        argv += ["--biotype"]
    if core.get("sift"):
        argv += ["--sift", str(core["sift"])]
    if core.get("polyphen"):
        argv += ["--polyphen", str(core["polyphen"])]
    if core.get("af_gnomade"):
        argv += ["--af_gnomade"]
    if core.get("af_gnomadg"):
        argv += ["--af_gnomadg"]
    if core.get("max_af"):
        argv += ["--max_af"]
    for extra in core.get("extra_flags", []) or []:
        argv += [str(extra)]

    # --- plugins -------------------------------------------------------------
    _add_plugins(cfg.get("plugins", {}), plan, mapper, argv, check_exists)

    # --- custom tracks -------------------------------------------------------
    _add_custom(cfg.get("custom_tracks", {}), plan, mapper, argv, check_exists)

    plan.argv = argv
    # Combine ref mounts (ro) + io mounts, de-duping by (host,container).
    seen = set()
    combined: list[Mount] = []
    for m in mapper.ref_mounts() + io_mounts:
        key = (m.host, m.container)
        if key not in seen:
            seen.add(key)
            combined.append(m)
    plan.mounts = combined
    return plan


def _add_plugins(plugins: dict, plan: VepPlan, mapper: PathMapper,
                 argv: list[str], check_exists: bool) -> None:
    # dbNSFP — consolidates CADD/REVEL/AlphaMissense/SIFT/PolyPhen/PrimateAI/etc.
    # Plugin string: dbNSFP,<file>,<col1>,<col2>,...
    dbnsfp = plugins.get("dbNSFP", {})
    if dbnsfp.get("enabled"):
        cp = _resolve(plan, mapper, dbnsfp["path"], "plugin.dbNSFP",
                      dbnsfp.get("required", False), check_exists)
        if cp:
            cols = dbnsfp.get("columns") or []
            if isinstance(cols, str):          # allow columns: ALL
                cols = [cols]
            spec = ",".join([cp] + [str(c) for c in cols]) if cols else cp
            argv += ["--plugin", "dbNSFP," + spec]

    # LoF (LOFTEE) — multi-key plugin string
    lof = plugins.get("LoF", {})
    if lof.get("enabled"):
        kv = []
        # loftee_path: "auto" -> use $LOFTEE_DIR env set in the image
        lp = lof.get("loftee_path", "auto")
        kv.append(f"loftee_path:{'$LOFTEE_DIR' if lp == 'auto' else (mapper.map(lp) if mapper.container else os.path.abspath(lp))}")
        ok = True
        for key, _label in [("human_ancestor_fa", "human_ancestor_fa"),
                           ("conservation_file", "conservation_file"),
                           ("gerp_bigwig", "gerp_bigwig")]:
            hp = lof.get(key)
            if not hp:
                continue
            cp = _resolve(plan, mapper, hp, f"plugin.LoF.{key}", lof.get("required", False), check_exists)
            if cp is None:
                ok = False
                break
            kv.append(f"{key}:{cp}")
        if ok:
            argv += ["--plugin", "LoF," + ",".join(kv)]

    # SpliceAI (snv= + indel=)
    sai = plugins.get("SpliceAI", {})
    if sai.get("enabled"):
        parts = []
        ok = True
        for key in ("snv", "indel"):
            hp = sai.get(key)
            if not hp:
                continue
            cp = _resolve(plan, mapper, hp, f"plugin.SpliceAI.{key}", sai.get("required", False), check_exists)
            if cp is None:
                ok = False
                break
            parts.append(f"{key}={cp}")
        if ok and parts:
            argv += ["--plugin", "SpliceAI," + ",".join(parts)]

    # CADD v1.7 WGS — the official VEP plugin reads the score-only SNV and
    # indel TSVs directly. Do not convert these files to a duplicate VCF and
    # do not use the much larger inclAnno downloads: the plugin emits only
    # CADD_RAW and CADD_PHRED.
    cadd = plugins.get("CADD_WGS", {})
    if cadd.get("enabled"):
        required = cadd.get("required", False)
        parts = []
        ok = True
        for key in ("snv", "indels"):
            host_path = cadd.get(key, "")
            if not host_path:
                # An unset path must be treated as missing: abspath("") is the
                # current working directory, which --no-check would otherwise
                # emit as a real plugin argument (indels=<cwd>).
                msg = f"plugin.CADD_WGS.{key}: no path configured"
                if required:
                    plan.errors.append(msg)
                else:
                    plan.warnings.append(msg + "  [skipped]")
                ok = False
                continue
            cp = _resolve_indexed(
                plan, mapper, host_path,
                f"plugin.CADD_WGS.{key}", required, check_exists,
            )
            if cp is None:
                ok = False
            else:
                parts.append(f"{key}={cp}")
        if ok:
            argv += ["--plugin", "CADD," + ",".join(parts)]

    # PromoterAI — local licensed scores compacted to an indexed four-allele
    # table plus a transcript/TSS map by scripts/prepare_promoterai.sh.
    promoterai = plugins.get("PromoterAI", {})
    if promoterai.get("enabled"):
        required = promoterai.get("required", False)
        score_path = _resolve(
            plan, mapper, promoterai.get("file", ""),
            "plugin.PromoterAI.file", required, check_exists,
        )
        transcript_map = _resolve(
            plan, mapper, promoterai.get("transcript_map", ""),
            "plugin.PromoterAI.transcript_map", required, check_exists,
        )
        if score_path and transcript_map:
            argv += [
                "--plugin",
                f"PromoterAI,file={score_path},transcript_map={transcript_map}",
            ]

    # LoGoFunc — strict allele + transcript + protein-change matching is
    # implemented in the bundled plugin. The source table is already BGZF and
    # tabix indexed, so it is mounted and queried without conversion.
    logofunc = plugins.get("LoGoFunc", {})
    if logofunc.get("enabled"):
        score_path = _resolve(
            plan, mapper, logofunc.get("file", ""),
            "plugin.LoGoFunc.file", logofunc.get("required", False), check_exists,
        )
        if score_path:
            argv += ["--plugin", f"LoGoFunc,file={score_path}"]


def _add_custom(tracks: dict, plan: VepPlan, mapper: PathMapper,
                argv: list[str], check_exists: bool) -> None:
    for name, t in tracks.items():
        if not t.get("enabled"):
            continue
        cp = _resolve(plan, mapper, t["file"], f"custom.{name}", t.get("required", False), check_exists)
        if cp is None:
            continue
        # Build the --custom string: file=...,short_name=...,format=...,[type=,coords=,fields=]
        seg = [f"file={cp}", f"short_name={t.get('short_name', name)}", f"format={t.get('format', 'vcf')}"]
        if "type" in t:
            seg.append(f"type={t['type']}")
        if "coords" in t:
            seg.append(f"coords={t['coords']}")
        if t.get("fields"):
            seg.append("fields=" + "%".join(t["fields"]))
        argv += ["--custom", ",".join(seg)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def load_config(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a VEP command from the annotation config.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--no-container", action="store_true",
                    help="emit native (host-path) command instead of container paths")
    ap.add_argument("--no-check", action="store_true",
                    help="do not check file existence (emit all enabled sources)")
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="print the command string (default prints argv one-per-line)")
    ap.add_argument("--json", dest="do_json", action="store_true",
                    help="emit {argv, mounts, warnings, errors} as JSON (for the runner)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    # Config paths are project-root-relative: root = the config's parent
    # directory's parent (config/ lives directly under the project).
    config_base = os.path.dirname(os.path.dirname(os.path.abspath(args.config)))
    plan = build_vep_command(cfg, args.input, args.output,
                             base_dir=config_base,
                             container=not args.no_container,
                             check_exists=not args.no_check)

    for w in plan.warnings:
        print(f"WARN  {w}", file=sys.stderr)

    if args.do_json:
        import json
        # Errors must also reach stderr: callers capture stdout into a
        # variable (run_annotation.sh), so a JSON-only error would leave the
        # operator with "see WARN/ERROR above" and nothing printed.
        for e in plan.errors:
            print(f"ERROR {e}", file=sys.stderr)
        json.dump({
            "argv": plan.argv,
            "mounts": [{"host": m.host, "container": m.container, "mode": m.mode}
                       for m in plan.mounts],
            "warnings": plan.warnings,
            "errors": plan.errors,
        }, sys.stdout)
        print()
        return 2 if plan.errors else 0

    if plan.errors:
        for e in plan.errors:
            print(f"ERROR {e}", file=sys.stderr)
        return 2

    if args.do_print:
        print(plan.command_string())
    else:
        for a in plan.argv:
            print(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
