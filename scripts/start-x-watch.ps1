$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $python -m dualtrade.x_watch status
if ($LASTEXITCODE -notin @(0,2)) { throw 'X watch settings could not be loaded' }
# Inspect only credential presence; never place a token on the command line.
& $python -c "from dualtrade.credentials import load_local_credentials; import os,sys; load_local_credentials(); sys.exit(0 if os.environ.get('X_BEARER_TOKEN') else 2)"
if ($LASTEXITCODE -ne 0) { throw 'First run: .\.venv\Scripts\python.exe -m dualtrade.x_watch setup-key' }
$stamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
$process = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\pythonw.exe') -ArgumentList @('-m','dualtrade.x_watch','run') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot "runtime\x-watch-$stamp.stdout.log") -RedirectStandardError (Join-Path $projectRoot "runtime\x-watch-$stamp.stderr.log") -PassThru
Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\x-watch.pid') -Value $process.Id
Write-Output ('X monitor launched; PID ' + $process.Id + '. Check runtime\reports\x-watch.txt for health and results.')
