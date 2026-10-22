param([switch]$NativeIntegration)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$env:TEMP = Join-Path $projectRoot 'build\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $python -m pytest -q --junitxml=build/tests.xml
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& (Join-Path $PSScriptRoot 'build.ps1')
if ($NativeIntegration) {
    & (Join-Path $PSScriptRoot 'build.ps1') -Expert DualTradeReplay
    & (Join-Path $PSScriptRoot 'build.ps1') -Expert DualTradeIntegrationTests
    & $python (Join-Path $PSScriptRoot 'test-three-books-native.py')
    if ($LASTEXITCODE -ne 0) { throw 'Native automatic order-flow checks failed' }
}
& $python (Join-Path $PSScriptRoot 'history-native-check.py')
if ($LASTEXITCODE -ne 0) { throw 'Export broker history before checking acceptance' }
& $python -c "from dualtrade.cli import source_hash; from dualtrade.storage import write_json,read_json; from dualtrade.config import ROOT; import time,hashlib; native=read_json(ROOT/'build/three-books-native.json',{}); valid=native.get('passed') is True and native.get('source_hash')==source_hash() and native.get('binary_sha256')==hashlib.sha256((ROOT/'MQL5/Experts/DualTradeIntegrationTests.ex5').read_bytes()).hexdigest(); write_json(ROOT/'build/acceptance.json',{'passed':valid,'unit_and_compile_passed':True,'historical_logic_passed':True,'source_hash':source_hash(),'time':time.time(),'native_evidence':'build/three-books-native.json','native_checks':native.get('checks',0),'scope':'Functional behavior and controlled simulator faults, not profitability or physical outages','activation_pending':['Healthy observation for each selected section','Required market/source readiness','Explicit pinned-demo EA order permission']})"
Write-Output 'Functional evidence recorded. Per-section observation, source readiness and explicit demo permission remain separate activation gates.'
