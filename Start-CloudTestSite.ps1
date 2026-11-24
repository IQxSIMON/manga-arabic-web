$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$env:PORT = "5001"
$env:MANGA_BIND_HOST = "127.0.0.1"
& "$projectRoot\.venv\Scripts\python.exe" "$projectRoot\app.py"
