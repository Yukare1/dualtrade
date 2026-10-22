$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$portable = Join-Path $projectRoot 'build\mt5-simulator'
$data = (Get-Content (Join-Path $projectRoot 'build\terminal-data-path.txt') -Raw).Trim()
$cfg = Get-Content (Join-Path $projectRoot 'config.local.json') -Raw | ConvertFrom-Json
$installed = Split-Path $cfg.terminal_path -Parent
if (Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $portable 'terminal64.exe') }) { throw 'Isolated simulator is already running' }
foreach ($folder in @($portable, (Join-Path $portable 'config'), (Join-Path $portable 'MQL5\Experts'), (Join-Path $portable 'bases\ICMarketsSC-Demo\history\AAPL.NAS'))) { New-Item -ItemType Directory -Force $folder | Out-Null }
foreach ($binary in @('terminal64.exe','metatester64.exe')) { Copy-Item -LiteralPath (Join-Path $installed $binary) -Destination (Join-Path $portable $binary) -Force }
# Copy market definitions and history only: no accounts.dat, passwords, or chart EAs.
Copy-Item -LiteralPath (Join-Path $data 'config\servers.dat') -Destination (Join-Path $portable 'config\servers.dat') -Force
if (-not (Test-Path -LiteralPath (Join-Path $portable 'bases\ICMarketsSC-Demo\symbols'))) { Copy-Item -LiteralPath (Join-Path $data 'bases\ICMarketsSC-Demo\symbols') -Destination (Join-Path $portable 'bases\ICMarketsSC-Demo\symbols') -Recurse }
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $data 'bases\ICMarketsSC-Demo\history\AAPL.NAS') -Filter '*.hcc' -File) {
    try { $sourceStream=[IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite) }
    catch [IO.IOException] { Write-Warning ('Locked history skipped: '+$file.Name); continue }
    try {
        $destination=Join-Path $portable ('bases\ICMarketsSC-Demo\history\AAPL.NAS\'+$file.Name)
        $targetStream=[IO.File]::Open($destination,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $sourceStream.CopyTo($targetStream) } finally { $targetStream.Dispose() }
    } finally { $sourceStream.Dispose() }
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'MQL5\Experts\DualTradeIntegrationTests.ex5') -Destination (Join-Path $portable 'MQL5\Experts\DualTradeIntegrationTests.ex5') -Force
$fromDate='2025.10.20'
$toDate='2025.10.22'
$configuration=@"
[Common]
Login=$($cfg.expected_login)
Server=ICMarketsSC-Demo
[Experts]
Enabled=0
AllowLiveTrading=0
AllowDllImport=0
[Tester]
Expert=DualTradeIntegrationTests.ex5
Symbol=AAPL.NAS
Period=M15
Login=$($cfg.expected_login)
Optimization=0
Model=1
FromDate=$fromDate
ToDate=$toDate
Deposit=1000
Currency=USD
ProfitInPips=0
Leverage=1:20
ExecutionMode=0
UseLocal=1
UseRemote=0
UseCloud=0
Visual=0
Report=integration-native
ReplaceReport=1
ShutdownTerminal=1
"@
$path=Join-Path $portable 'integration.ini'
Set-Content -LiteralPath $path -Value $configuration -Encoding ascii
$process=Start-Process -FilePath (Join-Path $portable 'terminal64.exe') -ArgumentList @('/portable','/skipupdate',('/config:"'+$path+'"')) -WorkingDirectory $portable -WindowStyle Hidden -PassThru
Write-Output ('Isolated simulator started: PID '+$process.Id)
