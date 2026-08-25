$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcher = Join-Path $repoRoot "desktop\windows\GUIDE-IEI.ps1"
$batchLauncher = Join-Path $repoRoot "desktop\windows\GUIDE-IEI.bat"
$windowsPowerShell = Join-Path $Env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-TestLauncher([string]$WslExe) {
    $oldWslExe = $Env:IEI_WSL_EXE
    $oldNonInteractive = $Env:IEI_NONINTERACTIVE
    try {
        $Env:IEI_WSL_EXE = $WslExe
        $Env:IEI_NONINTERACTIVE = "1"
        $output = @(& $windowsPowerShell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher 2>&1)
        return [PSCustomObject]@{
            ExitCode = [int]$LASTEXITCODE
            Text = ($output -join "`n")
        }
    } finally {
        $Env:IEI_WSL_EXE = $oldWslExe
        $Env:IEI_NONINTERACTIVE = $oldNonInteractive
    }
}

# Parse with the same Windows PowerShell language service used by the launcher.
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $launcher,
    [ref]$tokens,
    [ref]$parseErrors
)
Assert-True ($parseErrors.Count -eq 0) "PowerShell parser errors: $($parseErrors -join '; ')"

$batchText = [IO.File]::ReadAllText($batchLauncher)
Assert-True ($batchText.Contains("GUIDE-IEI launcher is starting")) "The batch file must show immediate feedback."
Assert-True ($batchText.Contains("WindowsPowerShell\v1.0\powershell.exe")) "The batch file must use the explicit Windows PowerShell path."
Assert-True ($batchText.Contains("pause")) "The batch file must remain visible after a launch failure."

$missingWsl = Join-Path $Env:TEMP ("guide-iei-test-no-wsl-" + [Guid]::NewGuid().ToString("N") + ".exe")
$missingResult = Invoke-TestLauncher $missingWsl
Assert-True ($missingResult.ExitCode -ne 0) "Missing WSL must return a failure status."
Assert-True ($missingResult.Text.Contains("wsl --install -d Ubuntu")) "Missing WSL must show the exact installation command."
Assert-True ($missingResult.Text.Contains("Diagnostic log:")) "The launcher must print its diagnostic-log location."

# Docker Desktop and other applications can register internal or unusable
# distributions. Simulate a listed Ubuntu that cannot prove Bash + WSL2.
$fakeWsl = Join-Path $Env:TEMP "guide-iei-fake-wsl.cmd"
$fakeWslText = @'
@echo off
if "%~1"=="--list" if "%~2"=="--quiet" echo Ubuntu
exit /b 0
'@
[IO.File]::WriteAllText($fakeWsl, $fakeWslText, [Text.Encoding]::ASCII)
try {
    $incompatibleResult = Invoke-TestLauncher $fakeWsl
    Assert-True ($incompatibleResult.ExitCode -ne 0) "An unverified distro must return a failure status."
    Assert-True ($incompatibleResult.Text.Contains("none provides both")) "The launcher must explain the WSL2-and-Bash requirement."
} finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $fakeWsl
}

$launcherText = [IO.File]::ReadAllText($launcher)
Assert-True ($launcherText.Contains('source_root="$1"')) "Windows paths must be passed as arguments to the Linux copy helper."
Assert-True ($launcherText.Contains('"$staging/webui/node_modules"')) "Windows node_modules must be removed before WSL setup."
Assert-True ($launcherText.Contains('"$staging/webui/.next"')) "Windows Next.js build output must be removed before WSL setup."
Assert-True ($launcherText.Contains("IEI_WSL_LAUNCHER=1")) "The WSL setup must know it was started by the Windows launcher."

Write-Host "Windows launcher regression tests passed."
