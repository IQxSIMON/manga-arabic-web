[CmdletBinding()]
param([switch]$InWindow, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$worker = Join-Path $projectRoot '.ai_bridge\bridge_worker.py'
if (-not (Test-Path -LiteralPath $python)) { throw "Missing Python: $python" }
if (-not $InWindow) {
    $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}" -InWindow' -f $PSCommandPath
    if ($CheckOnly) { $arguments += ' -CheckOnly' }
    $window = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Normal -PassThru
    Write-Host "Requested visible PowerShell PID $($window.Id). Desktop visibility must be verified separately."
    exit
}
Set-Location -LiteralPath $projectRoot
$Host.UI.RawUI.WindowTitle = 'Manga Arabic - Visible Codex Worker'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
Write-Host "Visible worker | $projectRoot"
Write-Host 'Stop safely from another terminal: .venv\Scripts\python.exe .ai_bridge\bridge_worker.py stop'
try {
    if ($CheckOnly) { & $python -u $worker check }
    else { & $python -u $worker run }
    Write-Host "Worker exited with code $LASTEXITCODE."
} catch { Write-Host $_ -ForegroundColor Red }
Read-Host 'Press Enter to close this window'
