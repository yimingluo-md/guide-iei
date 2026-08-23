# One-click launchers

Double-click entry points for clinicians and wet-lab scientists — no
terminal commands. Both routes call `scripts/start_workbench.sh
--bootstrap`, which runs the environment setup on first use, starts the
loopback service and the browser interface, opens the browser, and — when
the workbench is already running — simply opens the browser tab.

- `macos/GUIDE-IEI.app` — double-clickable app bundle. Fast path opens the
  browser directly; otherwise it opens a Terminal window running
  `GUIDE-IEI-Workbench.command`, so first-run progress is visible and
  closing the window stops the workbench. Unsigned: first open is
  right-click → Open.
- `windows/GUIDE-IEI.bat` → `GUIDE-IEI.ps1` — checks for WSL2 (directing
  the user to Docker Desktop's installer when absent, which enables WSL2),
  copies the repository into the WSL home directory on first launch
  (`~/guide-iei`; rerun with `-Update` to refresh it), and starts the
  workbench inside WSL. The browser opens on the Windows side.

Line endings are pinned by `.gitattributes` (`.sh`/`.command` LF even on
Windows checkouts; `.bat`/`.ps1` CRLF). The Windows launcher awaits
validation on a real Windows machine.
