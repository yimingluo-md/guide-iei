---
title: "Installation & requirements"
parent: Reference
nav_order: 1
---

# Installation & requirements

[Manual home](index.md)

## The setup check

Run the setup check after cloning — it checks everything below, prints
an exact fix for anything missing, and `--install` fixes the user-space items
itself (no admin rights, no Homebrew, nothing outside one managed folder):

```bash
# report what is present / missing (changes nothing)
bash scripts/setup_environment.sh

# fix what can be fixed without admin rights
bash scripts/setup_environment.sh --install
```

What it needs to find (or install):

- **A container runtime** — Docker, Podman, Singularity, or Apptainer.
  - *macOS:* `--install` sets up a no-admin, Homebrew-free stack (Lima +
    Colima + the Docker CLI, all version-pinned and SHA-256-verified) in
    `~/.iei-variant-review/tools/`. An existing Docker Desktop / Podman
    install is detected and used instead.
  - *Linux / WSL2:* a container runtime is a system component, so the script
    prints the exact install commands and runs them only after an explicit
    yes (existing docker/podman/singularity installs are always preferred —
    Docker Desktop is **not** required on WSL2).
- **Python 3.8+** with **PyYAML** (`requirements.txt`; `--install` handles it)
  — for the config parser.
- **Node.js 22.13+** with npm — for the local review UI. `--install` places
  the official nodejs.org build in the managed tools folder when no suitable
  Node is found; `scripts/start_workbench.sh` finds it there automatically.
- Disk for references: the VEP cache alone is ~25 GB; dbNSFP is a ~50 GB
  download (academic registration required — see
  [Annotation dataset setup](DATASET_SETUP.md)); SpliceAI (if enabled) adds
  tens of GB more.

No VEP, LOFTEE, bgtools, or Perl installation on the host — everything runs in
the container. The setup script never edits your shell profile; remove
`~/.iei-variant-review/tools/` to uninstall everything it added.

Optional but recommended: **native `bcftools`/`tabix`/`bgzip`** on the host.
Without them every htslib operation runs through the container, which works
but is typically 5–20× slower over macOS bind mounts. `--install` adds them
automatically via conda, Homebrew, or apt when one is available; the pinned
in-container BCFtools/liftover used for GRCh37 intake is unaffected.

## Supported platforms

| Platform | Status | Notes |
|----------|--------|-------|
| **Linux** | ✅ native | Primary target. Run directly. |
| **macOS** (Intel or Apple Silicon) | ✅ native | Scripts are Bash-3.2-compatible (macOS ships Bash 3.2); run the Linux container with Docker Desktop, Podman, or Colima. |
| **Windows** | ✅ via **WSL2** only | Not supported from native Windows shells or Git Bash. See below. |

The runner is a set of **Bash** scripts that call standard Unix tools
(`awk`, `sed`, `sort`, `gzip`, `curl`/`wget`, `python3`, `rsync`) plus a Linux
**container** (`ensemblorg/ensembl-vep`). Anything that needs `bcftools` /
`tabix` / `bgzip` runs them natively if present, or falls back to the container.

### Windows: use WSL2

Native Windows (`cmd`, PowerShell, or bare Git Bash) **cannot** run this — the
scripts need a real Unix shell + coreutils, and the VEP image is Linux-only.
The supported route is **WSL2** (Windows Subsystem for Linux 2), which is a real
Linux kernel:

1. Install WSL2 with a Linux distro (e.g. Ubuntu): `wsl --install` in an
   elevated PowerShell, then reboot (~1–2 GB download).
2. Install **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**
   and enable its **WSL2 backend** (Settings → Resources → WSL integration).
3. Open the WSL2 (Ubuntu) shell and clone + run the pipeline there exactly as a
   Linux user would.

> **Performance caveat.** Keep the clone **and** the large reference files on
> the **WSL2 filesystem** (`~/...` inside the distro), *not* on a Windows drive
> under `/mnt/c/...`. Cross-filesystem I/O to `/mnt/c` is very slow, which badly
> hurts the multi-GB tabix reference reads (dbNSFP, SpliceAI, CADD).

> **Disk-space caveat.** The WSL2 filesystem is a virtual-disk file on `C:` by
> default, growing with use — it must accommodate the reference datasets
> (~90 GB exome / 150–250 GB full WGS). If `C:` is small, relocate the distro
> to a larger internal drive or an NTFS external SSD with
> `wsl --export` / `wsl --unregister` / `wsl --import` **before** downloading
> datasets; the User Guide's
> [installation chapter](guide/02-install.md) walks through the commands.
> Datasets on a `/mnt/*` drive via the Storage page work but pay the
> cross-filesystem penalty above.

## Where to keep the clone (cloud-sync note)

Keep the git clone in a **non-synced** location (e.g. `~/repos/`) and do all
`git pull` / `git push` there. **Do not run git directly inside a OneDrive /
Dropbox / Google Drive folder** — those providers block or corrupt the `.git`
directory (OneDrive returns "Operation not permitted" on `.git`, and syncing
git's internal object files mid-write can corrupt the repo).

If you want a copy inside a synced folder to edit/run from, use the helper,
which copies the working tree **without** `.git`:

```bash
# refresh the OneDrive snapshot after a git pull (edit DEST or set ONEDRIVE_DEST)
scripts/sync_to_onedrive.sh [DEST]
```

## Verify the installation (no container, no downloads)

A self-contained smoke test exercises the whole wiring — config parsing, the
VEP command builder, the container-command assembly (dry run), and the ClinVar
amino-acid-match on a simulated VEP output — without needing Docker or any
reference data:

```bash
bash test/test_dry_run.sh
```

Expected tail:

```
[1/3] builder --json
  argv tokens: 40  mounts: 7
[2/3] run_annotation.sh --dry-run
  dry-run assembled OK
[3/3] clinvar_aa_match.py on simulated VEP output
  [aa_match] ref_residues=2 records=3 missense=2 matched=1 csq_usable=True
  match flag present
ALL DRY-RUN CHECKS PASSED
```

Unit tests for the two Python components:

```bash
python test/test_build_command.py   # config -> VEP command (7 tests)
python test/test_aa_match.py         # ClinVar aa-match + reducer (8 tests)
```

Once annotation datasets are installed, the small public regression panel
exercises the installed data — not only the command wiring:

```bash
bash scripts/run_annotation_regression.sh
```

It annotates eight public GRCh38 controls (three known LoGoFunc OR4F5 missense
alleles, NCSTN frameshift, STAT3 missense, IL2RG splice donor, IL2RG
stop-gained, and a TERT promoter variant), then asserts the PTC-based LOFTEE
50-bp correction, LOFTEE, AlphaMissense, CADD, SpliceAI, ClinVar, and ClinVar
amino-acid matching. LoGoFunc and promoterAI are tested when their optional
local tracks are installed and reported as explicit `SKIP`s otherwise. The
input contains one synthetic sample named `REGRESSION`; it contains no patient
data.

Next: [Annotation dataset setup](DATASET_SETUP.md)
