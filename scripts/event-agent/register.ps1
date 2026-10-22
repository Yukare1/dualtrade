param([switch]$StartNow)
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$service = Join-Path $PSScriptRoot 'service.py'
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $service + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
Register-ScheduledTask -TaskName 'DualTradeEventAgent' -Action $action -Trigger $trigger -Settings $settings -Description 'Local candidate dispatch and token-free observation; does not control MT5 orders.' -Force | Out-Null
if ($StartNow) { Start-ScheduledTask -TaskName 'DualTradeEventAgent' }
Write-Output 'DualTradeEventAgent registered.'
