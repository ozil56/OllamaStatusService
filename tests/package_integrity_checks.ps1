param([string]$Root=(Split-Path -Parent $PSScriptRoot))
. (Join-Path $Root 'scripts\Common.ps1')
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('Ollama校验 & test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
try {
    $file=Join-Path $fixture '安装 中文.cmd'
    [IO.File]::WriteAllBytes($file,[Text.Encoding]::GetEncoding(936).GetBytes("@echo off`r`n"))
    $entry=@([ordered]@{path='安装 中文.cmd';sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash})
    $manifestFile=Join-Path $fixture 'manifest.sha256.json'
    foreach ($bom in @($false,$true)) {
        [IO.File]::WriteAllText($manifestFile,($entry | ConvertTo-Json),[Text.UTF8Encoding]::new($bom))
        if ((Test-PackageIntegrity $fixture) -ne 1) { throw 'UTF-8 Chinese file validation failed' }
    }
    $second=Join-Path $fixture 'Deployment-Guide.txt'
    [IO.File]::WriteAllText($second,'UTF-8 测试',[Text.UTF8Encoding]::new($false))
    $entry+=@([ordered]@{path='Deployment-Guide.txt';sha256=(Get-FileHash -LiteralPath $second -Algorithm SHA256).Hash})
    foreach ($bom in @($false,$true)) {
        [IO.File]::WriteAllText($manifestFile,($entry | ConvertTo-Json),[Text.UTF8Encoding]::new($bom))
        if ((Test-PackageIntegrity $fixture) -ne 2) { throw 'JSON array unwrapping failed' }
    }
    [IO.File]::AppendAllText($file,'tampered')
    $rejected=$false
    try { Test-PackageIntegrity $fixture | Out-Null } catch { $rejected=$true }
    if (-not $rejected) { throw 'A modified payload was incorrectly accepted' }
    Remove-Item -LiteralPath $file
    $rejected=$false
    try { Test-PackageIntegrity $fixture | Out-Null } catch { $rejected=$true }
    if (-not $rejected) { throw 'A missing payload was incorrectly accepted' }
    Write-Output 'PASS: production validator handles BOM/no-BOM UTF-8 Chinese paths and rejects modified/missing files.'
} finally {
    $resolved=[IO.Path]::GetFullPath($fixture)
    $base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $resolved.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($resolved).StartsWith('Ollama校验 & test-')) { throw 'Unexpected fixture cleanup boundary' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
