---
title: "Install it on your computer"
parent: User Guide
nav_order: 2
---

# Install it on your computer

Installation consists of three commands. This chapter explains what each
does, walks each platform through them, and addresses the problems most
often encountered on first use.

## Requirements

- A reasonably modern computer: **Mac** (Intel or Apple Silicon), **Windows
  10/11** (via WSL2, described below), or **Linux**.
- **Disk space**: approximately 110 GB for the exome reference datasets and
  more for whole-genome work ([the next chapter](03-datasets.md) provides
  the planning table). Datasets may reside on an external SSD when the
  internal drive is small.
- **No administrator rights are required on a Mac**, and nothing is
  installed system-wide: everything the installer adds is contained in one
  managed folder (`~/.iei-variant-review/tools/`), and removing that folder
  uninstalls it completely. On Linux and WSL2, one component — the
  container runtime — is a system package; the installer prints the exact
  commands and asks for confirmation before running them.

## The three commands

Open a terminal (macOS: **Terminal**, in Applications → Utilities; Windows:
the **Ubuntu/WSL2** window described below; Linux: any shell) and run:

```bash
git clone https://github.com/yimingluo-md/guide-iei.git && cd guide-iei
```

```bash
bash scripts/setup_environment.sh --install
```

```bash
bash scripts/start_workbench.sh
```

Then open **`http://127.0.0.1:3000`** in a browser — a local address: the
workbench runs on your machine and uses the browser as its display.

```text
== IEI pipeline environment check — macOS (arm64)

[ OK ] core tools (git, tar, curl/wget, awk, sed, sort, gzip)
[ OK ] python3 3.9.12 (>= 3.8)
[ OK ] PyYAML importable (config parser dependency)
[ OK ] node v26.7.0 (>= 22.13)
[ OK ] webui/node_modules present
[ OK ] native bcftools/tabix/bgzip found — htslib I/O runs without container overhead
[WARN] disk: only 38 GiB free — the exome reference set alone needs ~40 GiB
       (the Storage page can place datasets on another drive)
[ OK ] pipeline smoke test passed (test/test_dry_run.sh)

== Summary: 7 ok, 1 warning(s), 0 to fix
Environment ready. Next steps:
  bash scripts/start_workbench.sh            # launch the review workbench
```

A `WARN` line, like the disk-space warning above, is advice rather than a
failure — the summary still ends `Environment ready` when nothing needs
fixing.

## What the second command does

`setup_environment.sh` is a **setup check**. Run without options it only
*reports*: green `[ OK ]` lines for what is present, and the exact remedy
for anything missing. With `--install` it also corrects what can be
corrected without administrator rights:

- **Python and its single dependency** — verified, installed if absent.
- **Node.js** (runs the workbench interface) — when no suitable version is
  found, the official build is placed in the managed tools folder, leaving
  the system installation untouched.
- **A container runtime** — the annotation engine (Ensembl VEP and its
  plugins) runs inside a Linux container, so no bioinformatics software is
  installed on the machine itself. An existing Docker Desktop or Podman
  installation is detected and used; on a Mac without one, a small, pinned,
  checksum-verified container stack is set up in the managed folder — no
  Homebrew, no administrator password.
- **Native bcftools/tabix** (optional, recommended) — accelerates file
  operations severalfold; added automatically when a package manager is
  available.
- **A smoke test** — the complete wiring is exercised end to end, with no
  downloads and no patient data, so the installation is verified before any
  time is invested in dataset downloads.

The setup check may be run repeatedly without consequence — it changes
nothing that is already correct — and it is the first thing to re-run
whenever the installation appears broken.

## Windows: the WSL2 track

GUIDE-IEI cannot run from native Windows (Command Prompt, PowerShell); it
requires a Unix environment, which Windows provides through **WSL2**. The
one-time setup:

1. In PowerShell (run as administrator): `wsl --install`, then reboot.
   This installs Ubuntu Linux inside Windows (a ~1–2 GB download).
2. Install
   **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**
   and, in its settings, enable **WSL integration** (Settings → Resources →
   WSL integration).
3. Open the **Ubuntu** application from the Start menu — that window is the
   terminal — and run the three commands there.

One important practice: keep the GUIDE-IEI folder and its datasets **inside
the Linux filesystem** (the Ubuntu home folder, `~/...`), not under
`/mnt/c/...`. Files accessed across the Windows/Linux boundary are read
many times more slowly, and the large indexed reference files are affected
most.

### WSL2 disk space: plan before downloading datasets

WSL2 keeps its entire Linux filesystem in a single virtual-disk file that
resides on the Windows system drive (`C:`) by default and **grows as data
are added**. The Ubuntu installation itself is small, but once the
annotation datasets are installed, this file reaches the sizes described in
the [next chapter](03-datasets.md) — roughly **110 GB for exome work,
150–250 GB for the full whole-genome stack**. The practical rule: the drive
hosting WSL must have that much free space.

When `C:` is too small, there are two good options and one fallback:

- **Relocate WSL to a larger internal drive** (preferred). In PowerShell:

  ```
  wsl --shutdown
  wsl --export Ubuntu D:\wsl\ubuntu.tar
  wsl --unregister Ubuntu
  wsl --import Ubuntu D:\wsl\Ubuntu D:\wsl\ubuntu.tar
  ```

  This moves the entire Linux filesystem to `D:` (adjust the drive
  letter). Performed **before** datasets are downloaded, there is almost
  nothing to move. Export first; unregister only after the export has
  succeeded.

- **Relocate WSL to an external SSD.** The same export/import commands
  accept an external destination, provided the drive is **NTFS-formatted**
  and remains connected whenever GUIDE-IEI is used. Performance is
  preserved, because the Linux filesystem still resides inside the virtual
  disk.

- **Fallback: datasets on an external drive via `/mnt`.** The **Storage**
  page inside the application can place the annotation datasets at any
  path, including a Windows drive visible in WSL as `/mnt/d/...`. This
  works, but it crosses the Windows/Linux boundary described above, and
  the large indexed reference files (dbNSFP, SpliceAI) pay the largest
  penalty — annotation becomes noticeably slower. Prefer one of the
  relocation options when possible.

## Common first-run problems

| Symptom | Cause and remedy |
|---|---|
| `[FIX]` line about the container daemon | The container runtime is installed but not running. The setup check prints the exact start command (e.g. `colima start ...`); Docker Desktop users: launch Docker Desktop. |
| The workbench page does not load | The terminal running `start_workbench.sh` must remain open — it *is* the application. Restart it and watch for error lines. |
| Everything is slow | Check the setup check's note about native bcftools/tabix; without them, file operations run through the container at 5–20× cost. |
| Git errors after cloning into OneDrive/Dropbox | Cloud-synced folders corrupt git's internal files. Keep the clone in an ordinary folder (e.g. `~/guide-iei`); a sync helper exists for keeping a synced working copy ([Installation reference](../INSTALLATION.md)). |
| Uncertain what state the installation is in | Re-run `bash scripts/setup_environment.sh` (no options). It changes nothing and reports exactly what is present and missing. |

## Uninstalling

Delete the cloned `guide-iei` folder and the managed folder
`~/.iei-variant-review/` — noting that the latter also contains the sample
library, so export anything worth keeping first. Nothing else on the system
was modified.

Technical reference: [Installation & requirements](../INSTALLATION.md)

Next: [Set up the annotation datasets](03-datasets.md)
