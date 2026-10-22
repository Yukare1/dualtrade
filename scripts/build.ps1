param([string]$Terminal = 'C:\Program Files\MetaTrader 5 IC Markets Global', [string]$IncludeRoot = '', [string]$Expert = 'DualTrade')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $IncludeRoot) {
    $candidates = Get-ChildItem -LiteralPath "$env:APPDATA\MetaQuotes\Terminal" -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'MQL5\Include\Trade\Trade.mqh') }
    if (-not $candidates) { throw 'MT5 standard include directory not found' }
    $IncludeRoot = Join-Path $candidates[0].FullName 'MQL5'
}
New-Item -ItemType Directory -Force (Join-Path $projectRoot 'build') | Out-Null
$source = Join-Path $projectRoot ('MQL5\Experts\' + $Expert + '.mq5')
$log = Join-Path $projectRoot ('build\' + $Expert + '-compile.log')
Start-Process -FilePath (Join-Path $Terminal 'MetaEditor64.exe') -ArgumentList @('/compile:"' + $source + '"', '/inc:"' + $IncludeRoot + '"', '/log:"' + $log + '"') -WindowStyle Hidden -Wait
$content = Get-Content -LiteralPath $log -Raw
$content -split "`r?`n" | Select-Object -Last 3
if ($content -notmatch '0 errors, 0 warnings') { throw 'Compilation did not pass cleanly; see build/compile.log' }
