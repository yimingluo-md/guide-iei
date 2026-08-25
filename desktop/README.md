# One-click launchers

Double-click entry points for clinicians and wet-lab scientists — no
terminal commands. Both routes call `scripts/start_workbench.sh
--bootstrap`, which runs the environment setup on first use, starts the
loopback service and the browser interface, opens the browser, and — when
the workbench is already running — simply opens the browser tab.

- `macos/GUIDE-IEI.app` — source-tree/development app template. The release
  builder copies it, embeds a checksum-verified source payload, compiles the
  `GUIDE-IEI-launcher.c` shim as a universal arm64+x86_64 executable, and
  ad-hoc seals the bundle. On first launch the payload is installed under
  `~/Library/Application Support/GUIDE-IEI/application`, so Gatekeeper App
  Translocation cannot break paths and software updates remain writable.
  The app is intentionally unsigned by Developer ID for now: on current
  macOS, first open requires System Settings → Privacy & Security → Open
  Anyway. `scripts/build_macos_release.sh` produces the standalone ZIP.
- `windows/GUIDE-IEI.bat` → `GUIDE-IEI.ps1` — checks for WSL2 (directing
  the user to Docker Desktop's installer when absent, which enables WSL2),
  copies the repository into the WSL home directory on first launch
  (`~/guide-iei`; rerun with `-Update` to refresh it), and starts the
  workbench inside WSL. The browser opens on the Windows side.

Line endings are pinned by `.gitattributes` (`.sh`/`.command` LF even on
Windows checkouts; `.bat`/`.ps1` CRLF). The Windows launcher awaits
validation on a real Windows machine.
