$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$projectRoot=Split-Path $PSScriptRoot -Parent
$vaultPath=Join-Path $projectRoot 'runtime\credentials.dpapi.json'
$configPath=Join-Path $projectRoot 'config.local.json'
$values=@{}
if(Test-Path -LiteralPath $vaultPath){$saved=Get-Content -LiteralPath $vaultPath -Raw | ConvertFrom-Json;foreach($p in $saved.PSObject.Properties){$values[$p.Name]=$p.Value}}
$cfg=Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$form=New-Object Windows.Forms.Form
$form.Text='DualTrade - local credential setup'
$form.Size=New-Object Drawing.Size(800,660)
$form.StartPosition='CenterScreen';$form.FormBorderStyle='FixedDialog';$form.MaximizeBox=$false
$intro=New-Object Windows.Forms.Label
$intro.Text='Enter keys only in this local window. Saved values are encrypted for your Windows user. Blank fields preserve saved values. Saving does not enable orders.'
$intro.Location=New-Object Drawing.Point(20,15);$intro.Size=New-Object Drawing.Size(740,45);$form.Controls.Add($intro)
$fields=@{};$row=70
foreach($key in @('ALPHAVANTAGE_API_KEY','MARKETAUX_API_TOKEN','SEC_USER_AGENT')){
    $label=New-Object Windows.Forms.Label;$label.Text=$key;$label.Location=New-Object Drawing.Point(20,$row);$label.Size=New-Object Drawing.Size(270,22);$form.Controls.Add($label)
    $box=New-Object Windows.Forms.TextBox;$box.Location=New-Object Drawing.Point(300,$row);$box.Size=New-Object Drawing.Size(450,24);$box.UseSystemPasswordChar=($key -ne 'SEC_USER_AGENT');$form.Controls.Add($box);$fields[$key]=$box
    $hint=New-Object Windows.Forms.Label;$hint.Text=$(if($values.ContainsKey($key)){'Saved locally (leave blank to keep)'}elseif($key -eq 'SEC_USER_AGENT'){'Your real name and email, e.g. Name contact@example.com'}else{'Not configured'});$hint.Location=New-Object Drawing.Point(300,($row+25));$hint.Size=New-Object Drawing.Size(450,18);$form.Controls.Add($hint);$row+=52
}
$note=New-Object Windows.Forms.Label;$note.Text='Configure provider keys and your SEC contact. Context comes from the existing official document agent. No social-media password is needed.';$note.Location=New-Object Drawing.Point(20,475);$note.Size=New-Object Drawing.Size(730,55);$form.Controls.Add($note)
$status=New-Object Windows.Forms.Label;$status.Location=New-Object Drawing.Point(20,540);$status.Size=New-Object Drawing.Size(730,45);$form.Controls.Add($status)
$save=New-Object Windows.Forms.Button;$save.Text='Save locally';$save.Location=New-Object Drawing.Point(500,585);$save.Size=New-Object Drawing.Size(120,30);$form.Controls.Add($save)
$close=New-Object Windows.Forms.Button;$close.Text='Close';$close.Location=New-Object Drawing.Point(635,585);$close.Size=New-Object Drawing.Size(115,30);$close.Add_Click({$form.Close()});$form.Controls.Add($close)
$save.Add_Click({
    try{
        foreach($key in $fields.Keys){$text=$fields[$key].Text.Trim();if($text){$secure=ConvertTo-SecureString -String $text -AsPlainText -Force;$values[$key]=ConvertFrom-SecureString -SecureString $secure;$fields[$key].Clear()}}
        New-Item -ItemType Directory -Force (Split-Path $vaultPath -Parent) | Out-Null
        $temporary=$vaultPath+'.tmp';$values|ConvertTo-Json|Set-Content -LiteralPath $temporary -Encoding utf8;Move-Item -LiteralPath $temporary -Destination $vaultPath -Force
        $temporary=$configPath+'.tmp';$cfg|ConvertTo-Json -Depth 20|Set-Content -LiteralPath $temporary -Encoding utf8;Move-Item -LiteralPath $temporary -Destination $configPath -Force
        $status.Text='Saved encrypted values. Run credentials --verify, then restart this collector. Orders remain paused.'
    }catch{$status.Text='Setup failed. Check field values and local file access; no secrets are shown.'}
})
[void]$form.ShowDialog()
