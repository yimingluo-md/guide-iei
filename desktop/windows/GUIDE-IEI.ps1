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
$script:TranscriptStarted = $false
$script:LogPath = $null

function Start-GuideLog {
    try {
        $base = if ($Env:LOCALAPPDATA) { $Env:LOCALAPPDATA } else { $Env:TEMP }
        $logDir = Join-Path $base "GUIDE-IEI\logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
        $script:LogPath = Join-Path $logDir "launcher-$stamp.log"
        Start-Transcript -Path $script:LogPath -Force | Out-Null
        $script:TranscriptStarted = $true
    } catch {
        $script:TranscriptStarted = $false
        $script:LogPath = $null
    }
}

function Stop-GuideLog {
    if ($script:TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}

function Offer-WslInstall([string]$WslExe) {
    if ($Env:IEI_NONINTERACTIVE -eq "1" -or -not (Test-Path $WslExe)) {
        return
    }
    $answer = Read-Host "Install WSL2 and Ubuntu now? Windows may request administrator approval (y/N)"
    if ($answer -notmatch '^[Yy]') { return }
    try {
        $process = Start-Process -FilePath $WslExe -ArgumentList @("--install", "-d", "Ubuntu") -Verb RunAs -Wait -PassThru
        if ($process.ExitCode -eq 0) {
            Write-Host ""
            Write-Host "WSL installation was started successfully."
            Write-Host "Restart Windows if requested, finish Ubuntu's first-run setup,"
            Write-Host "then double-click GUIDE-IEI.bat again."
        } else {
            Write-Host "WSL installation returned exit code $($process.ExitCode)."
            Write-Host "Run this in an Administrator PowerShell: wsl --install -d Ubuntu"
        }
    } catch {
        Write-Host "Windows did not start the WSL installer: $($_.Exception.Message)"
        Write-Host "Run this in an Administrator PowerShell: wsl --install -d Ubuntu"
    }
}

function Invoke-GuideIei {
    $WinRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    # The override exists only for the non-interactive Windows CI harness;
    # normal launches always use Windows' trusted system copy.
    $WslExe = if ($Env:IEI_NONINTERACTIVE -eq "1" -and $Env:IEI_WSL_EXE) {
        $Env:IEI_WSL_EXE
    } else {
        Join-Path $Env:SystemRoot "System32\wsl.exe"
    }

    Write-Host ""
    Write-Host "GUIDE-IEI is starting. Keep this window open while you work;"
    Write-Host "closing it stops the workbench. Your browser opens automatically."
    if ($script:LogPath) { Write-Host "Diagnostic log: $script:LogPath" }
    Write-Host ""

    if (-not (Test-Path $WslExe)) {
        Write-Host "GUIDE-IEI needs WSL2 (Windows Subsystem for Linux), but"
        Write-Host "wsl.exe is not available on this computer."
        Write-Host ""
        Write-Host "Run this in an Administrator PowerShell:"
        Write-Host "  wsl --install -d Ubuntu"
        Write-Host "Then restart when requested and launch GUIDE-IEI again."
        return 1
    }

    # Docker Desktop, Rancher Desktop, and Podman register internal WSL
    # distributions that cannot host GUIDE-IEI. A candidate must also prove
    # that it has Bash and is genuinely WSL2, not merely appear in `wsl -l`.
    $distros = @(& $WslExe --list --quiet 2>$null) |
        ForEach-Object { ($_ -replace "`0", '').Trim() } |
        Where-Object { $_ -and $_ -notmatch '^(docker-desktop|rancher-desktop|podman-machine)' }
    $compatible = @()
    $probeCommand = 'case "$(uname -r)" in *WSL2*|*wsl2*|*microsoft-standard*) printf "__IEI_WSL2_BASH__\n" ;; esac'
    foreach ($candidate in @($distros)) {
        $probe = @(& $WslExe -d $candidate bash -lc $probeCommand 2>$null)
        if ($LASTEXITCODE -eq 0 -and (($probe -join "`n") -match '__IEI_WSL2_BASH__')) {
            $compatible += $candidate
        }
    }

    if ($compatible.Count -eq 0) {
        if (@($distros).Count -gt 0) {
            Write-Host "GUIDE-IEI found Linux distribution(s), but none provides both"
            Write-Host "WSL2 and Bash: $(@($distros) -join ', ')"
            Write-Host ""
            Write-Host "Convert an existing distribution with:"
            Write-Host "  wsl --set-version <distribution-name> 2"
            Write-Host "Or install the supported Ubuntu environment with:"
            Write-Host "  wsl --install -d Ubuntu"
        } else {
            Write-Host "GUIDE-IEI runs inside WSL2 with Ubuntu, but no usable Linux"
            Write-Host "distribution is installed yet. Docker Desktop's internal"
            Write-Host "distribution does not count."
            Write-Host ""
            Write-Host "Install the supported environment with:"
            Write-Host "  wsl --install -d Ubuntu"
        }
        Write-Host "Restart if requested, finish Ubuntu's first-run setup, then"
        Write-Host "double-click GUIDE-IEI.bat again."
        Write-Host ""
        Offer-WslInstall $WslExe
        return 1
    }

    # Prefer an existing GUIDE-IEI installation when several compatible
    # distributions exist; otherwise use WSL's first listed real distro.
    $distro = $compatible[0]
    if ($compatible.Count -gt 1) {
        foreach ($candidate in $compatible) {
            $installed = @(& $WslExe -d $candidate bash -lc 'test -d ~/guide-iei/scripts && printf "__IEI_INSTALLED__\n"' 2>$null)
            if (($installed -join "`n") -match '__IEI_INSTALLED__') {
                $distro = $candidate
                break
            }
        }
    }
    Write-Host "Using WSL2 distribution: $distro"

    # Convert the source folder and a temporary copy helper to Linux paths.
    # Values are passed as process arguments, never interpolated into shell
    # source, so spaces, apostrophes, and shell metacharacters are safe.
    $rootOutput = @(& $WslExe -d $distro wslpath -a $WinRoot 2>&1)
    if ($LASTEXITCODE -ne 0 -or $rootOutput.Count -eq 0) {
        throw "WSL could not access the GUIDE-IEI folder: $($rootOutput -join ' ')"
    }
    $winRootWsl = $rootOutput[-1].Trim()

    # Docker is optional for reviewing an already annotated VCF. When Docker
    # Desktop exists, verify that THIS selected distribution can reach it.
    $dockerExe = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    $dockerDesktopInstalled = Test-Path $dockerExe
    $dockerProbe = @(& $WslExe -d $distro bash -lc 'command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && printf "__IEI_DOCKER_READY__\n"' 2>$null)
    $dockerReady = (($dockerProbe -join "`n") -match '__IEI_DOCKER_READY__')
    if ($dockerDesktopInstalled -and -not $dockerReady) {
        Write-Host ""
        Write-Host "NOTE: Docker Desktop is installed but is not available inside $distro."
        Write-Host "Reviewing annotated VCFs will work. Before running VEP, start"
        Write-Host "Docker Desktop and enable Settings > Resources > WSL Integration"
        Write-Host "for this distribution."
    } elseif (-not $dockerReady) {
        Write-Host ""
        Write-Host "NOTE: No working container runtime was found in $distro."
        Write-Host "Reviewing annotated VCFs will work. Running VEP requires Docker"
        Write-Host "Desktop with WSL integration or Docker installed inside WSL."
    }
    Write-Host ""

    $wslRepo = "~/guide-iei"
    $hasRepoOutput = @(& $WslExe -d $distro bash -lc 'test -d ~/guide-iei/scripts && test -f ~/guide-iei/VERSION && printf "__IEI_REPO__\n"' 2>$null)
    $hasRepo = (($hasRepoOutput -join "`n") -match '__IEI_REPO__')

    if (-not $hasRepo -or $Update) {
        if ($Update) {
            Write-Host "Refreshing the WSL copy of GUIDE-IEI from this folder..."
        } else {
            Write-Host "First launch: copying GUIDE-IEI into WSL (one time)..."
        }

        # Replace rather than overlay, while retaining the user's edited
        # configuration and reference datasets. Never carry Windows-native
        # node_modules/.next or source-control/build state into Linux.
        $copyScript = @'
set -euo pipefail
source_root="$1"
repo="$HOME/guide-iei"
staging="$HOME/guide-iei.staging"
rm -rf "$staging"
mkdir -p "$staging"
cp -R "$source_root"/. "$staging"/
rm -rf "$staging/.git" "$staging/webui/node_modules" "$staging/webui/.next" "$staging/dist"
rm -f "$staging/webui/.dependencies-updated" "$staging/webui/.build-required"
find "$staging" -type f \( -name '*.sh' -o -name '*.command' \) -exec chmod +x {} +
printf '%s\n' "$source_root" > "$staging/.wsl-origin"
if [ -f "$repo/config/annotation.config.yaml" ]; then
    mkdir -p "$staging/config"
    cp -p "$repo/config/annotation.config.yaml" "$staging/config/"
fi
if [ -f "$repo/config/annotation.config.yaml.new" ]; then
    mkdir -p "$staging/config"
    cp -p "$repo/config/annotation.config.yaml.new" "$staging/config/"
fi
if [ -d "$repo/references" ]; then
    rm -rf "$staging/references"
    mv "$repo/references" "$staging/references"
fi
rm -rf "$repo"
mv "$staging" "$repo"
'@
        $tempScript = Join-Path $Env:TEMP ("guide-iei-copy-" + [Guid]::NewGuid().ToString("N") + ".sh")
        try {
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [IO.File]::WriteAllText($tempScript, $copyScript.Replace("`r`n", "`n"), $utf8NoBom)
            $scriptOutput = @(& $WslExe -d $distro wslpath -a $tempScript 2>&1)
            if ($LASTEXITCODE -ne 0 -or $scriptOutput.Count -eq 0) {
                throw "WSL could not access its temporary copy helper."
            }
            $tempScriptWsl = $scriptOutput[-1].Trim()
            & $WslExe -d $distro bash $tempScriptWsl $winRootWsl
            if ($LASTEXITCODE -ne 0) {
                throw "copying GUIDE-IEI into WSL returned exit code $LASTEXITCODE"
            }
        } finally {
            Remove-Item -Force -ErrorAction SilentlyContinue $tempScript
        }
    }

    # The GUI launcher never installs a second native Docker engine behind
    # the user's back. setup_environment still installs user-space Node and
    # Linux prerequisites; Docker guidance remains visible above and in-app.
    $dockerDesktopFlag = if ($dockerDesktopInstalled) { "1" } else { "0" }
    $startCommand = "cd $wslRepo && IEI_WSL_LAUNCHER=1 IEI_WSL_DOCKER_DESKTOP=$dockerDesktopFlag bash scripts/start_workbench.sh --bootstrap"
    & $WslExe -d $distro bash -lc $startCommand
    return [int]$LASTEXITCODE
}

Start-GuideLog
$exitCode = 1
try {
    $exitCode = Invoke-GuideIei
} catch {
    Write-Host ""
    Write-Host "ERROR: GUIDE-IEI could not start."
    Write-Host $_.Exception.Message
    if ($script:LogPath) { Write-Host "Diagnostic log: $script:LogPath" }
    $exitCode = 1
} finally {
    Stop-GuideLog
}
exit $exitCode
