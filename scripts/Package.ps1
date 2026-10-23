param([string]$Root=(Split-Path -Parent $PSScriptRoot), [string]$ZipPath='')
$ErrorActionPreference='Stop'
$Root=[IO.Path]::GetFullPath($Root).TrimEnd('\')
if (-not $ZipPath) { $ZipPath=Join-Path (Split-Path -Parent $Root) 'OllamaStatusService-Windows-x64-v1.6.1.zip' }
$manifest=@(Get-ChildItem -LiteralPath $Root -File -Recurse | Where-Object { $_.Name -ne 'manifest.sha256.json' -and $_.FullName -notmatch '\\__pycache__\\' } | Sort-Object FullName | ForEach-Object {
    [ordered]@{path=$_.FullName.Substring($Root.Length+1);sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$manifestJson=$manifest | ConvertTo-Json -Depth 4
# Produce the same BOM-tagged UTF-8 on PowerShell 5.1 and PowerShell 7.
[IO.File]::WriteAllText((Join-Path $Root 'manifest.sha256.json'),$manifestJson,[Text.UTF8Encoding]::new($true))
# Python zipfile preserves Unicode names and avoids Compress-Archive's hidden-file omissions.
$python=Join-Path $Root 'runtime\python.exe'
& $python (Join-Path $PSScriptRoot 'Write-Archive.py') $Root $ZipPath
if ($LASTEXITCODE -ne 0) { throw '打包失败。' }
$hash=(Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
($hash + '  ' + [IO.Path]::GetFileName($ZipPath)) | Set-Content -LiteralPath ($ZipPath + '.sha256.txt') -Encoding ascii
Get-Item -LiteralPath $ZipPath | Select-Object FullName,Length
