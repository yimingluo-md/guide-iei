# GUIDE-IEI one-click launcher (Windows).
#
# Started by GUIDE-IEI.bat. Runs the workbench inside WSL2 and opens the
# Windows browser at the local address. The first launch copies the
# repository into the WSL home directory (Linux-side files are much faster
# than /mnt/c) and prepares the environment automatically.
#
#   -Update   refresh the WSL copy from this folder before starting

param([switch]$Update)

$ErrorActionPreference = "Stop"
$WinRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Write-Host ""
Write-Host "GUIDE-IEI is starting. Keep this window open while you work;"
Write-Host "closing it stops the workbench. Your browser opens automatically."
Write-Host ""

# --- WSL2 present with a Linux distribution? -------------------------------
$wslReady = $false
try {
    # Docker Desktop registers internal docker-desktop* distributions that
    # are not usable Linux environments; only a real distribution counts.
    $distros = (wsl.exe -l -q) 2>$null |
        ForEach-Object { ($_ -replace "`0", '').Trim() } |
        Where-Object { $_ -and $_ -notmatch '^docker-desktop' }
    if ($distros) { $wslReady = $true }
} catch { $wslReady = $false }

if (-not $wslReady) {
    Write-Host "GUIDE-IEI runs inside WSL2 (Windows Subsystem for Linux),"
    Write-Host "which is not set up yet. The simplest route is to install"
    Write-Host "Docker Desktop - its installer enables WSL2 for you and is"
    Write-Host "also needed for annotation."
    Write-Host ""
    Write-Host "  1. Install Docker Desktop (opens now), restart if asked."
    Write-Host "  2. Double-click GUIDE-IEI.bat again."
    Start-Process "https://www.docker.com/products/docker-desktop/"
    exit 1
}

# --- Docker Desktop? (needed for annotation; review works without it) ------
$dockerExe = "$Env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
if (-not (Test-Path $dockerExe)) {
    Write-Host "NOTE: Docker Desktop was not found. Reviewing annotated VCFs"
    Write-Host "works without it, but running VEP annotation needs it:"
    Write-Host "  https://www.docker.com/products/docker-desktop/"
    Write-Host ""
}

# --- Repository copy inside WSL -------------------------------------------
$wslRepo = "~/guide-iei"
$winRootWsl = (wsl.exe wslpath -a "$WinRoot").Trim()
$hasRepo = (wsl.exe bash -c "test -d $wslRepo/scripts && echo yes") 2>$null

if (-not $hasRepo -or $Update) {
    if ($Update) {
        Write-Host "Refreshing the WSL copy of GUIDE-IEI from this folder..."
    } else {
        Write-Host "First launch: copying GUIDE-IEI into WSL (one time)..."
    }
    # Replace, never overlay: overlay copies kept files that upstream
    # deleted or renamed, leaving stale modules in the installed copy. The
    # repository holds no user state (that lives in ~/.iei-variant-review),
    # so a staged swap is safe; webui dependencies reinstall on next start.
    wsl.exe bash -c "rm -rf $wslRepo.staging && mkdir -p $wslRepo.staging && cp -R '$winRootWsl'/. $wslRepo.staging/ && find $wslRepo.staging -name '*.sh' -o -name '*.command' | xargs -r chmod +x && rm -rf $wslRepo && mv $wslRepo.staging $wslRepo"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: copying into WSL failed. Please report this message."
        exit 1
    }
}

# --- Start the workbench ---------------------------------------------------
# start_workbench.sh handles first-time setup (--bootstrap), the browser
# open (via the Windows default browser), and supervised restarts.
wsl.exe bash -lc "cd $wslRepo && bash scripts/start_workbench.sh --bootstrap"
exit $LASTEXITCODE
