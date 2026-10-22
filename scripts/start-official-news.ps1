$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'runtime\official-news') | Out-Null
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $python -m dualtrade.official_news running
if ($LASTEXITCODE -eq 0) { Write-Output 'Official-source collector is already running.'; return }
if ($LASTEXITCODE -ne 2) { throw 'Could not inspect the official-source collector lock' }
$stamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
$process = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\pythonw.exe') -ArgumentList @('-m','dualtrade.official_news','run') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot "runtime\official-news\collector-$stamp.stdout.log") -RedirectStandardError (Join-Path $projectRoot "runtime\official-news\collector-$stamp.stderr.log") -PassThru
Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\official-news\collector.pid') -Value $process.Id
Write-Output ('Official-source collector launched; PID ' + $process.Id + '. Health: runtime\official-news\report.txt')
