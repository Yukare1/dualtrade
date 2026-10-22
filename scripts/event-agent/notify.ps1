param([string]$Title, [string]$Message)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
try {
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Visible = $true
    $icon.BalloonTipTitle = $Title
    $icon.BalloonTipText = $Message
    $icon.ShowBalloonTip(5000)
    Start-Sleep -Seconds 6
} finally {
    $icon.Dispose()
}
