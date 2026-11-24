$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\MangaArabic\manga-arabic-web'
$envPath = Join-Path $projectRoot '.env'
$secureKey = $null
$ptr = [IntPtr]::Zero
$plainKey = $null

try {
    $secureKey = Read-Host 'Paste the full Gemini API key (input is hidden)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim()

    $isStandardKey = $plainKey -match '^AIza[A-Za-z0-9_-]{25,55}$'
    $isAuthKey = $plainKey -match '^AQ\.[A-Za-z0-9._-]{40,80}$'
    if (-not ($isStandardKey -or $isAuthKey)) {
        $startsWithAq = $plainKey.StartsWith('AQ.')
        $startsWithAiza = $plainKey.StartsWith('AIza')
        Write-Host ('Input rejected. Characters received=' + $plainKey.Length + '; AQ prefix=' + $startsWithAq + '; AIza prefix=' + $startsWithAiza + '.')
        throw 'Copy the complete key with the AI Studio Copy button; the .env file was not changed.'
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
                $lines[$i] = 'GEMINI_API_KEY=' + $plainKey
                $found = $true
            }
            else {
                $lines.RemoveAt($i)
                $i--
            }
        }
    }
    if (-not $found) {
        $lines.Add('GEMINI_API_KEY=' + $plainKey)
    }
    [IO.File]::WriteAllLines($envPath, $lines, [Text.UTF8Encoding]::new($false))
    $keyType = if ($isAuthKey) { 'AQ authorization key' } else { 'Standard AIza key' }
    Write-Host ('Saved locally. Key type=' + $keyType + '; length=' + $plainKey.Length + '. Key value was not displayed.')
}
finally {
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    Remove-Variable plainKey, secureKey, ptr -ErrorAction SilentlyContinue
    $plainKey = $null
    $secureKey = $null
}
