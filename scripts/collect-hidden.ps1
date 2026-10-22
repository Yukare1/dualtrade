$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'runtime') | Out-Null
$runStamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
$stdoutPath = Join-Path $projectRoot ('runtime\collector-' + $runStamp + '.stdout.log')
$stderrPath = Join-Path $projectRoot ('runtime\collector-' + $runStamp + '.stderr.log')
$process = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\pythonw.exe') -ArgumentList @('-m','dualtrade.cli','collect') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Set-Content -LiteralPath (Join-Path $projectRoot 'runtime\collector.pid') -Value $process.Id
Write-Output ('Collector started; PID '+$process.Id+'. Entries follow the observation/demo gates.')
