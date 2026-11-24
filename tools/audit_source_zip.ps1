$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$path = Join-Path $env:TEMP 'manga-arabic-web-source-2026-11-25-v2.zip'
$archive = [IO.Compression.ZipFile]::OpenRead($path)
try {
    $archive.Entries | Sort-Object Length -Descending | Select-Object -First 30 FullName,Length | Format-Table -AutoSize
    'ENTRY_COUNT=' + $archive.Entries.Count
    'SECRET_PATHS=' + (($archive.Entries | Where-Object FullName -Match '(^|/)(\.env($|\.)|credentials|secrets?)' | Measure-Object).Count)
} finally { $archive.Dispose() }
