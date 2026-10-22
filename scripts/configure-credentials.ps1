param([switch]$Gui)
$ErrorActionPreference = 'Stop'
if($Gui){ & (Join-Path $PSScriptRoot 'credential-form.ps1'); return }
$projectRoot = Split-Path $PSScriptRoot -Parent
$path = Join-Path $projectRoot 'runtime\credentials.dpapi.json'
New-Item -ItemType Directory -Force (Split-Path $path -Parent) | Out-Null
$values = @{}
if (Test-Path -LiteralPath $path) {
    $existing = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    foreach ($property in $existing.PSObject.Properties) { $values[$property.Name] = $property.Value }
}
Write-Output 'Values stay on this PC, encrypted for your Windows user. Blank input preserves an existing value.'
foreach ($key in @('ALPHAVANTAGE_API_KEY','MARKETAUX_API_TOKEN','SEC_USER_AGENT')) {
    $secure = Read-Host -Prompt $key -AsSecureString
    if ($secure.Length -gt 0) { $values[$key] = ConvertFrom-SecureString -SecureString $secure }
}
$values | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding utf8
Write-Output 'Saved encrypted local credentials. Restart the collector to use them. Reddit approval and sentiment validation remain separate requirements.'
