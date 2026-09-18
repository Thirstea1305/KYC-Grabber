# Self-restarting launcher for Windows.
# Keeps the always-on service running even if the process dies; logs to .\logs\service-<date>.log
#
#   .\deploy\run-windows.ps1            # run in the foreground, restart on failure
#   .\deploy\run-windows.ps1 -Once      # single run, no restart loop

[CmdletBinding()]
param(
    [switch]$Once,
    [int]$RestartDelaySeconds = 15
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Virtual environment not found at $python. Create it with: python -m venv .venv"
}

$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("service-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

Set-Location $projectRoot
Write-Host "KYC Grabber -> $logFile"

do {
    $started = Get-Date
    Write-Host ("[{0}] starting python -m kyc_grabber run" -f $started.ToString("s"))
    & $python -m kyc_grabber run 2>&1 | Tee-Object -FilePath $logFile -Append
    $exitCode = $LASTEXITCODE

    if ($Once) { exit $exitCode }

    Write-Warning ("process exited with code {0}; restarting in {1}s" -f $exitCode, $RestartDelaySeconds)
    Start-Sleep -Seconds $RestartDelaySeconds
} while ($true)
