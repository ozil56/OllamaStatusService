# Safe static checks; NEVER runs installation, UAC, firewall or service mutations.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$allErrors=@()
Get-ChildItem -LiteralPath (Join-Path $Root 'scripts') -Filter '*.ps1' | ForEach-Object {
    $tokens=$null; $errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors) | Out-Null
    $allErrors += $errors
}
if ($allErrors.Count) { $allErrors | Format-List; throw 'PowerShell syntax failure' }
$encoder=[Text.Encoding]::GetEncoding(936)
Get-ChildItem -LiteralPath $Root -Filter '*.cmd' | ForEach-Object {
    $bytes=[IO.File]::ReadAllBytes($_.FullName)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) { throw 'CMD must not contain UTF-8 BOM' }
    $text=$encoder.GetString($bytes)
    if ($text -match '(?<!\r)\n') { throw 'CMD must use CRLF' }
    if ($text -notmatch '@echo off') { throw 'Unexpected launcher content' }
}
Write-Output 'PowerShell syntax and CMD encoding checks passed.'
