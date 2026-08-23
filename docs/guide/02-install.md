---
title: "Install it on your computer"
parent: User Guide
nav_order: 2
---

# Install it on your computer

GUIDE-IEI starts from a double-click. The first launch prepares everything
it needs on its own — a few minutes, once — and every later launch opens
the workbench directly. A terminal route exists for those who prefer it
(and for Linux), described in the appendix at the end of this chapter.

## Requirements

- A reasonably modern computer: **Mac** (Intel or Apple Silicon), **Windows
  10/11** (via WSL2, which the launcher arranges), or **Linux**.
- **Disk space**: approximately 110 GB for the exome reference datasets and
  more for whole-genome work ([the next chapter](03-datasets.md) provides
  the planning table). Datasets may reside on an external SSD when the
  internal drive is small.
- **No administrator rights are required on a Mac**, and nothing is
  installed system-wide: everything the first launch adds is contained in
  one managed folder (`~/.iei-variant-review/tools/`), and removing that
  folder uninstalls it completely. On Windows, one third-party installer —
  Docker Desktop — is required once, described below.

## Get the software

On the [GUIDE-IEI GitHub page](https://github.com/yimingluo-md/guide-iei),
use the green **Code** button → **Download ZIP**, then unzip and place the
`guide-iei` folder somewhere ordinary — the home folder or Documents.
Avoid cloud-synced locations (OneDrive, Dropbox, iCloud Drive): sync
services interfere with the workbench's working files.

## Mac: double-click GUIDE-IEI

Inside the folder, open `desktop` → `macos` and double-click
**GUIDE-IEI.app** (drag it to the Dock if you like — it stays connected to
the folder it came from).

- **The first time only**, macOS refuses the app with *"GUIDE-IEI" Not
  Opened — Apple could not verify…*, offering only **Move to Trash** and
  **Done**. This is the standard treatment of any downloaded app outside
  the App Store, and the resolution is one time only:
  1. Click **Done** (not Move to Trash).
  2. Open **System Settings → Privacy & Security** and scroll down to
     the **Security** section, which now says *"GUIDE-IEI" was blocked to
     protect your Mac*.
  3. Click **Open Anyway** (your login password or Touch ID confirms
     it), then double-click the app again and confirm **Open Anyway**
     once more.

  On older macOS versions, right-clicking the app and choosing **Open**
  achieves the same in one step.
- **The first launch prepares the environment**: a Terminal window opens
  and reports progress while Python is checked, Node.js and the interface
  dependencies are installed into the managed folder, and a smoke test
  verifies the wiring — no administrator password, nothing outside the
  managed folder. A few minutes, once.
- The browser then opens the workbench at **`http://127.0.0.1:3000`** — a
  local address; the workbench runs on your machine and uses the browser
  as its display.
- **Keep the Terminal window open while you work** — it *is* the
  application; closing it stops the workbench. Later launches skip the
  preparation and open in seconds.

If the app itself refuses to open even after Open Anyway (a rare unzip
quirk), double-click `GUIDE-IEI-Workbench.command` in the same folder —
it is the same launcher in plainer clothing, and macOS may ask for the
same one-time Open Anyway approval for it.

## Windows: one installer, then double-click

GUIDE-IEI runs inside **WSL2** (a Linux environment Windows provides), and
its annotation engine runs in a container. One installer covers both:

1. Install
   **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**
   and restart if asked. Its installer enables WSL2; afterwards, in Docker
   Desktop's settings, confirm **WSL integration** is on (Settings →
   Resources → WSL integration).
2. Inside the GUIDE-IEI folder, open `desktop` → `windows` and
   double-click **GUIDE-IEI.bat**.

The first launch copies GUIDE-IEI into the Linux filesystem (where large
indexed files are read many times faster than across the Windows/Linux
boundary), prepares the environment, and opens the workbench in your
normal Windows browser. As on the Mac: keep the launcher window open while
you work; closing it stops the workbench.

## What the first launch prepares

The preparation step is a **setup check with an installer attached**. It
reports a green `[ OK ]` line for everything already present and corrects
what can be corrected without administrator rights:

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
  downloads and no patient data, so the installation is verified before
  any time is invested in dataset downloads.

Its summary looks like this:

```text
== IEI pipeline environment check — macOS (arm64)

[ OK ] core tools (git, tar, curl/wget, awk, sed, sort, gzip)
[ OK ] python3 3.9.12 (>= 3.9)
[ OK ] PyYAML importable (config parser dependency)
[ OK ] node v26.7.0 (>= 22.13)
[ OK ] webui/node_modules present
[ OK ] native bcftools/tabix/bgzip found — htslib I/O runs without container overhead
[WARN] disk: only 38 GiB free — the exome reference set alone needs ~40 GiB
       (the Storage page can place datasets on another drive)
[ OK ] pipeline smoke test passed (test/test_dry_run.sh)

== Summary: 7 ok, 1 warning(s), 0 to fix
Environment ready.
```

A `WARN` line, like the disk-space warning above, is advice rather than a
failure — the summary still ends `Environment ready` when nothing needs
fixing. The check may be repeated without consequence — it changes nothing
that is already correct — and re-running it (appendix below) is the first
move whenever the installation appears broken.

## Windows only: WSL2 disk space — plan before downloading datasets

WSL2 keeps its entire Linux filesystem in a single virtual-disk file that
resides on the Windows system drive (`C:`) by default and **grows as data
are added**. Once the annotation datasets are installed, this file reaches
the sizes described in the [next chapter](03-datasets.md) — roughly
**110 GB for exome work, 150–250 GB for the full whole-genome stack**. The
practical rule: the drive hosting WSL must have that much free space.

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
  works, but it crosses the Windows/Linux boundary, and the large indexed
  reference files (dbNSFP, SpliceAI) pay the largest penalty — annotation
  becomes noticeably slower. Prefer one of the relocation options when
  possible.

## Common first-run problems

| Symptom | Cause and remedy |
|---|---|
| macOS: *"GUIDE-IEI" Not Opened* with only Move to Trash / Done | Expected once for a downloaded app: **Done** → System Settings → Privacy & Security → Security section → **Open Anyway** → open the app again. Never needed twice. |
| The workbench page does not load | The launcher window must remain open — it *is* the application. Start it again and watch for error lines. |
| A `[FIX]` line about the container daemon | The container runtime is installed but not running. Docker Desktop users: launch Docker Desktop; the setup check otherwise prints the exact start command. |
| Everything is slow | Check the setup summary's note about native bcftools/tabix; without them, file operations run through the container at 5–20× cost. |
| Windows: the launcher window flashes and closes | Docker Desktop (and with it WSL2) is not installed yet — step 1 above. |
| The folder lives in OneDrive/Dropbox and behaves oddly | Cloud-synced folders corrupt the working files. Move the `guide-iei` folder to an ordinary location and launch again. |
| Uncertain what state the installation is in | Run the setup check from the appendix below (no options). It changes nothing and reports exactly what is present and missing. |

## Uninstalling

Delete the `guide-iei` folder (the launcher app lives inside it) and the
managed folder `~/.iei-variant-review/` — noting that the latter also
contains the sample library, so export anything worth keeping first.
Nothing else on the system was modified.

## Appendix: the terminal route

Everything the launcher does can be typed instead — the route of choice on
Linux, on servers, and for anyone at home in a shell:

```bash
git clone https://github.com/yimingluo-md/guide-iei.git && cd guide-iei
```

```bash
bash scripts/setup_environment.sh --install
```

```bash
bash scripts/start_workbench.sh
```

Then open **`http://127.0.0.1:3000`** in a browser.
`setup_environment.sh` run without options is the pure setup check —
report only, change nothing. On Linux and WSL2, one component — the
container runtime — is a system package; the installer prints the exact
commands and asks for confirmation before running them.

Technical reference: [Installation & requirements](../INSTALLATION.md)

Next: [Set up the annotation datasets](03-datasets.md)
