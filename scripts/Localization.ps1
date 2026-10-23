# Offline UI text only. Protocol names, paths and service identifiers are unchanged.
function Get-UiLanguage {
    $choice=$env:OSS_UI_LANGUAGE
    if (-not $choice) {
        $pref=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'OllamaStatusService\language.txt'
        if ([IO.File]::Exists($pref)) { $choice=[IO.File]::ReadAllText($pref).Trim() }
    }
    if (-not $choice -or $choice -eq 'auto') { $choice=[Globalization.CultureInfo]::CurrentUICulture.Name }
    $choice=$choice.Replace('_','-').ToLowerInvariant()
    if ($choice -like 'zh*') { if ($choice -match 'hant|-(tw|hk|mo)') { return 'zh-TW' }; return 'zh-CN' }
    foreach ($code in @('en','ja','ko','de','fr','es')) { if ($choice -eq $code -or $choice.StartsWith($code+'-')) { return $code } }
    return 'en'
}
$script:UiLanguage=Get-UiLanguage
$script:UiCatalog=$null
$script:UiCatalogPath=Join-Path (Split-Path -Parent $PSScriptRoot) 'locales\messages.json'
function Get-UiText([string]$Text) {
    if ($script:UiLanguage -eq 'zh-CN' -or -not $Text) { return $Text }
    if (-not $script:UiCatalog) {
        if (-not [IO.File]::Exists($script:UiCatalogPath)) { return $Text }
        $script:UiCatalog=[IO.File]::ReadAllText($script:UiCatalogPath) | ConvertFrom-Json
    }
    $entry=$script:UiCatalog.PSObject.Properties[$Text]
    if ($entry) { return [string]$entry.Value.PSObject.Properties[$script:UiLanguage].Value }
    foreach ($entry in $script:UiCatalog.PSObject.Properties) {
        if (-not $entry.Name.Contains('{0}')) { continue }
        $pattern=[regex]::Escape($entry.Name)
        foreach ($hole in [regex]::Matches($entry.Name,'\{(\d+)(?::[^{}]+)?\}')) {
            $pattern=$pattern.Replace([regex]::Escape($hole.Value),('(?<p'+$hole.Groups[1].Value+'>[\s\S]*?)'))
        }
        $found=[regex]::Match($Text,('^'+$pattern+'$'))
        if ($found.Success) {
            $translated=[string]$entry.Value.PSObject.Properties[$script:UiLanguage].Value
            return [regex]::Replace($translated,'\{(\d+)(?::[^{}]+)?\}',{param($m) $found.Groups['p'+$m.Groups[1].Value].Value})
        }
    }
    foreach ($entry in @($script:UiCatalog.PSObject.Properties | Sort-Object {$_.Name.Length} -Descending)) {
        if ($entry.Name.Length -lt 2 -or $entry.Name.Contains('{0}')) { continue }
        $translated=[string]$entry.Value.PSObject.Properties[$script:UiLanguage].Value
        if ($Text.StartsWith($entry.Name,[StringComparison]::Ordinal)) { return $translated+(Get-UiText $Text.Substring($entry.Name.Length)) }
        if ($Text.EndsWith($entry.Name,[StringComparison]::Ordinal)) { return (Get-UiText $Text.Substring(0,$Text.Length-$entry.Name.Length))+$translated }
    }
    return $Text
}

function Show-UiConfirmation([string]$Text,[string]$Title) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $form=New-Object Windows.Forms.Form
    try {
        $form.Text=Get-UiText $Title
        $form.Font=New-Object Drawing.Font('Segoe UI',10)
        $form.ClientSize=New-Object Drawing.Size(560,230)
        $form.StartPosition='CenterScreen'; $form.FormBorderStyle='FixedDialog'
        $form.MaximizeBox=$false; $form.MinimizeBox=$false
        $label=New-Object Windows.Forms.Label
        $label.Text=Get-UiText $Text; $label.SetBounds(22,22,516,130)
        $form.Controls.Add($label)
        $cancel=New-Object Windows.Forms.Button
        $cancel.Text=Get-UiText '取消'; $cancel.SetBounds(365,178,170,34); $cancel.DialogResult='Cancel'
        $confirm=New-Object Windows.Forms.Button
        $confirm.Text=Get-UiText '继续'; $confirm.SetBounds(184,178,170,34); $confirm.DialogResult='OK'
        $form.Controls.Add($confirm); $form.Controls.Add($cancel)
        $form.AcceptButton=$cancel; $form.CancelButton=$cancel
        return $form.ShowDialog() -eq [Windows.Forms.DialogResult]::OK
    } finally { $form.Dispose() }
}
