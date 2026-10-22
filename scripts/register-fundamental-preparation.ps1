$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if ((Get-TimeZone).Id -ne 'Turkey Standard Time') {
    throw 'This 15:00 schedule requires the verified Turkey timezone.'
}
$python = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$runner = Join-Path $PSScriptRoot 'prepare-fundamentals.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $runner)) {
    throw 'Project Python runtime and preparation runner are required.'
}
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $runner + '" --scheduled') -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '15:00'
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'DualTradeFundamentalPreparation' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force -Description 'Prepare existing SEC fundamental caches before the US session. Holidays and late runs skip. No order APIs.' | Out-Null
Write-Output 'Fundamental preparation registered for 15:00 Turkey time; exchange holidays and late runs skip.'
