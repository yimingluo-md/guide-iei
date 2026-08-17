---
title: "Install it on your computer"
parent: User Guide
nav_order: 2
---

# Install it on your computer

Three commands take a new machine to a running workbench. This chapter
explains what they do, walks each platform through them, and covers the
common first-run problems.

## What you need

- A reasonably modern computer: **Mac** (Intel or Apple Silicon), **Windows
  10/11** (via WSL2 — explained below), or **Linux**.
- **Disk space**: ~40 GB free for the exome reference datasets; more for
  whole-genome work ([next chapter](03-datasets.md) has the full planning
  table). Datasets can live on an external SSD if your internal drive is
  small.
- **No administrator rights are required on a Mac**, and nothing is
  installed system-wide: everything the installer adds lives in one managed
  folder (`~/.iei-variant-review/tools/`), and deleting that folder
  uninstalls it all. On Linux/WSL2, one component (the container runtime)
  is a system package; the installer prints the exact commands and asks
  before running them.

## The three commands

Open a terminal (macOS: **Terminal** in Applications → Utilities; Windows:
the **Ubuntu/WSL2** window — see below; Linux: any shell) and run:

```bash
git clone https://github.com/yimingluo-md/guide-iei.git && cd guide-iei
```

```bash
bash scripts/setup_environment.sh --install
```

```bash
bash scripts/start_workbench.sh
```

Then open **`http://127.0.0.1:3000`** in your browser — a local address:
the workbench runs on your machine and uses the browser as its screen.

*[Screenshot: terminal after setup_environment.sh --install completes, "Environment ready" summary]*

## What the second command actually does

`setup_environment.sh` is an **environment doctor**. Run without options it
only *reports* — green `[ OK ]` lines for what is present, and an exact fix
for anything missing. With `--install` it also *fixes* what can be fixed
without admin rights:

- **Python and its one dependency** — checked, installed if missing.
- **Node.js** (runs the workbench interface) — if your machine has no
  suitable version, the official build is placed in the managed tools
  folder. Your system is not touched.
- **A container runtime** — the annotation engine (Ensembl VEP and its
  plugins) runs inside a Linux container, so nothing bioinformatic is ever
  installed on your machine directly. If you already have Docker Desktop or
  Podman, it is detected and used. On a Mac without one, the installer sets
  up a small, pinned, checksum-verified container stack in the managed
  folder — no Homebrew, no admin password.
- **Native bcftools/tabix** (optional, recommended) — speeds up file
  operations several-fold; added automatically when a package manager is
  available.
- **A smoke test** — the wiring is exercised end to end (no downloads, no
  patient data) so you know the installation works before you invest in
  dataset downloads.

The doctor is safe to run repeatedly — it changes nothing that is already
correct, and it is the first thing to re-run whenever something seems
broken.

## Windows: the WSL2 track

GUIDE-IEI cannot run from native Windows (Command Prompt, PowerShell) — it
needs a Unix environment. Windows provides one, called **WSL2**, and the
one-time setup is:

1. In PowerShell (run as administrator): `wsl --install`, then reboot.
   This installs Ubuntu Linux inside Windows.
2. Install **Docker Desktop** and, in its settings, enable **WSL
   integration** (Settings → Resources → WSL integration).
3. Open the **Ubuntu** app from the Start menu — that window is your
   terminal — and run the three commands there.

One important habit: keep the GUIDE-IEI folder and its datasets **inside
the Linux filesystem** (your Ubuntu home folder, `~/...`), not on
`/mnt/c/...`. Files accessed across the Windows/Linux boundary are many
times slower, and the large reference files feel it badly.

## Common first-run problems

| Symptom | Cause and fix |
|---|---|
| `[FIX]` line about the container daemon | The container runtime is installed but not running. The doctor prints the exact start command (e.g. `colima start ...`); Docker Desktop users: launch Docker Desktop. |
| Workbench page will not load | The terminal running `start_workbench.sh` must stay open — it *is* the application. Restart it and watch for error lines. |
| Everything is slow | Check the doctor's note about native bcftools/tabix; without them, file operations run through the container at 5–20× cost. |
| Cloned into OneDrive/Dropbox and git errors appear | Cloud-synced folders corrupt git's internals. Keep the clone in a normal folder (e.g. `~/guide-iei`); a sync helper exists if you want a synced copy ([Installation reference](../INSTALLATION.md)). |
| Not sure what state things are in | Re-run `bash scripts/setup_environment.sh` (no options). It changes nothing and tells you exactly what is present and missing. |

## Uninstalling

Delete the cloned `guide-iei` folder and the managed folder
`~/.iei-variant-review/` (which also contains your sample library — export
anything you want to keep first). Nothing else on the system was modified.

Technical reference: [Installation & requirements](../INSTALLATION.md)

Next: [Set up the annotation datasets](03-datasets.md)
