$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\MangaArabic\manga-arabic-web'
$envPath = Join-Path $projectRoot '.env'
$keyText = $null

try {
    try {
        $keyText = [string](Get-Clipboard -Raw -ErrorAction Stop)
    }
    catch {
        Write-Host 'Could not read text from the clipboard. Copy the full key first; .env was not changed.'
        return
    }

    $keyText = $keyText.Trim().Trim('"').Trim("'")
    $isStandardKey = $keyText -match '^AIza[A-Za-z0-9_-]{25,55}$'
    $isAuthKey = $keyText -match '^AQ\.[A-Za-z0-9._-]{40,80}$'
    if (-not ($isStandardKey -or $isAuthKey)) {
        $startsWithAq = $keyText.StartsWith('AQ.')
        $startsWithAiza = $keyText.StartsWith('AIza')
        Write-Host ('Clipboard rejected. Characters received=' + $keyText.Length + '; AQ prefix=' + $startsWithAq + '; AIza prefix=' + $startsWithAiza + '.')
        Write-Host 'Copy the full key using AI Studio Copy; .env was not changed.'
        return
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $envPath) {
        foreach ($line in [IO.File]::ReadAllLines($envPath)) {
            $lines.Add($line)
        }
    }

    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^GEMINI_API_KEY=') {
            if (-not $found) {
                $lines[$i] = 'GEMINI_API_KEY=' + $keyText
                $found = $true
            }
            else {
                $lines.RemoveAt($i)
                $i--
            }
        }
    }

    if (-not $found) {
        $lines.Add('GEMINI_API_KEY=' + $keyText)
    }

    [IO.File]::WriteAllLines($envPath, $lines, [Text.UTF8Encoding]::new($false))
    $keyType = if ($isAuthKey) { 'AQ authorization key' } else { 'Standard AIza key' }
    Write-Host ('Saved locally. Key type=' + $keyType + '; length=' + $keyText.Length + '. Key value was not displayed.')
}
finally {
    Remove-Variable keyText -ErrorAction SilentlyContinue
    $keyText = $null
}
