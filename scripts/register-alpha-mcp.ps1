$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python=Join-Path $projectRoot '.venv\Scripts\python.exe'
& $python -c "from dualtrade.credentials import load_local_credentials; import os; load_local_credentials(); assert os.environ.get('ALPHAVANTAGE_API_KEY'), 'Configure the key in the local encrypted vault first'"
if($LASTEXITCODE -ne 0){throw 'Configure ALPHAVANTAGE_API_KEY locally first.'}
# Uses the official server through a local quota gateway. No key in argv/config.
codex mcp add alphavantage-research -- $python -m dualtrade.alpha_mcp
