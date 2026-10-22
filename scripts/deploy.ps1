param([Parameter(Mandatory=$true)][string]$TerminalDataDirectory)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$dataRoot = (Resolve-Path -LiteralPath $TerminalDataDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $dataRoot 'MQL5'))) { throw 'Select an MT5 data directory containing MQL5' }
$binary = Join-Path $projectRoot 'MQL5\Experts\DualTrade.ex5'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Build the EA first' }
$target = Join-Path $dataRoot 'MQL5\Experts\DualTrade'
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item -LiteralPath $binary -Destination (Join-Path $target 'DualTrade.ex5')
# Compile in the terminal's own tree so MT5 receives MetaEditor's reload notice.
# Namespaced support files avoid modifying the user's existing Include directory.
$includeTarget = Join-Path $target 'Source\Include'
New-Item -ItemType Directory -Force $includeTarget | Out-Null
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'MQL5\Include') | Copy-Item -Destination $includeTarget -Recurse -Force
$source = (Get-Content -LiteralPath (Join-Path $projectRoot 'MQL5\Experts\DualTrade.mq5') -Raw).Replace('../Include/','Source/Include/')
$destination = Join-Path $target 'DualTrade.mq5'
Set-Content -LiteralPath $destination -Value $source -Encoding utf8
$cfg = Get-Content -LiteralPath (Join-Path $projectRoot 'config.local.json') -Raw | ConvertFrom-Json
$editor = Join-Path (Split-Path $cfg.terminal_path -Parent) 'MetaEditor64.exe'
$log = Join-Path $projectRoot 'build\deploy-compile.log'
Start-Process -FilePath $editor -ArgumentList @('/compile:"'+$destination+'"','/inc:"'+(Join-Path $dataRoot 'MQL5')+'"','/log:"'+$log+'"') -WindowStyle Hidden -Wait
if ((Get-Content -LiteralPath $log -Raw) -notmatch '0 errors, 0 warnings') { throw 'Terminal compilation failed; inspect build/deploy-compile.log' }
Write-Output 'EA installed and compiled. Confirm the reported version and retain observation/order-permission gates.'
