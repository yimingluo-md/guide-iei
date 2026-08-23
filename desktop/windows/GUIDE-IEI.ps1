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
$distro = $null
try {
    # Docker Desktop, Rancher Desktop and Podman register internal
    # distributions that are not usable Linux environments; only a real
    # distribution counts.
    $distros = (wsl.exe -l -q) 2>$null |
        ForEach-Object { ($_ -replace "`0", '').Trim() } |
        Where-Object { $_ -and $_ -notmatch '^(docker-desktop|rancher-desktop|podman-machine)' }
    if ($distros) {
        $wslReady = $true
        # Every later wsl.exe call must target THIS distribution
        # explicitly: unqualified calls run in the WSL default, which can
        # be docker-desktop (Docker installed first, or the default lost
        # during a WSL relocation) — an environment the check above just
        # ruled out. `wsl -l` lists the default first, so when the user's
        # default is a real distribution it is the one chosen here. On a
        # machine with several real distributions, prefer the one that
        # already holds a GUIDE-IEI install — a changed default must not
        # silently migrate the app (and its review state) elsewhere.
        $distro = @($distros)[0]
        if (@($distros).Count -gt 1) {
            foreach ($candidate in $distros) {
                $installed = (wsl.exe -d $candidate bash -c "test -d ~/guide-iei/scripts && echo yes") 2>$null
                if ($installed) { $distro = $candidate; break }
            }
        }
    }
} catch { $wslReady = $false }

if (-not $wslReady) {
    Write-Host "GUIDE-IEI runs inside WSL2 (Windows Subsystem for Linux)"
    Write-Host "with a Linux distribution, and no distribution is installed"
    Write-Host "yet. (Docker Desktop's own internal one does not count.)"
    Write-Host ""
    Write-Host "  1. Open PowerShell and run:   wsl --install -d Ubuntu"
    Write-Host "     then restart when asked."
    Write-Host "  2. Install Docker Desktop if you have not (needed for"
    Write-Host "     annotation):"
    Write-Host "     https://www.docker.com/products/docker-desktop/"
    Write-Host "  3. Double-click GUIDE-IEI.bat again."
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
$winRootWsl = (wsl.exe -d $distro wslpath -a "$WinRoot").Trim()
$hasRepo = (wsl.exe -d $distro bash -c "test -d $wslRepo/scripts && echo yes") 2>$null

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
    wsl.exe -d $distro bash -c "rm -rf $wslRepo.staging && mkdir -p $wslRepo.staging && cp -R '$winRootWsl'/. $wslRepo.staging/ && find $wslRepo.staging -name '*.sh' -o -name '*.command' | xargs -r chmod +x && rm -rf $wslRepo && mv $wslRepo.staging $wslRepo"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: copying into WSL failed. Please report this message."
        exit 1
    }
}

# --- Start the workbench ---------------------------------------------------
# start_workbench.sh handles first-time setup (--bootstrap), the browser
# open (via the Windows default browser), and supervised restarts.
wsl.exe -d $distro bash -lc "cd $wslRepo && bash scripts/start_workbench.sh --bootstrap"
exit $LASTEXITCODE
