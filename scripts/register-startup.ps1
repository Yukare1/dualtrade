# Optional: invoke explicitly after successful preflight; never starts the terminal.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$action = New-ScheduledTaskAction -Execute $python -Argument '-m dualtrade.cli collect' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'DualTradeResearchCollector' -Action $action -Trigger $trigger -Settings $settings -Description 'Demo trading research collector. Does not launch MT5 or enable orders.' | Out-Null
