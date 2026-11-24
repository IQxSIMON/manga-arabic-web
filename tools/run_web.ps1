$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$appPath = Join-Path $projectRoot 'app.py'
$port = 5000
$bindHost = '127.0.0.1'

if (-not [string]::IsNullOrWhiteSpace($env:MANGA_BIND_HOST)) {
    $bindHost = $env:MANGA_BIND_HOST.Trim()
}

if (-not [string]::IsNullOrWhiteSpace($env:PORT)) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:PORT, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw 'PORT must be an integer from 1 to 65535.'
    }
    $port = $parsedPort
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project virtual-environment Python was not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
    throw "Flask entry point was not found: $appPath"
}

Push-Location -LiteralPath $projectRoot
try {
    Write-Host "Starting Manga Arabic Translator at http://${bindHost}:$port/"
    Write-Host 'Press Ctrl+C in this window to stop Flask and its local model servers.'
    & $pythonPath $appPath
}
finally {
    Pop-Location
}
