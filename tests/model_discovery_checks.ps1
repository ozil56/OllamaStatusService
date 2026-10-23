param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$testDir=Join-Path ([IO.Path]::GetTempPath()) ('OllamaDiscoveryBuild-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDir | Out-Null
try {
    $compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    $exe=Join-Path $testDir 'checks.exe'
    & $compiler /nologo /target:exe /platform:x64 /reference:System.Web.Extensions.dll ('/out:'+$exe) (Join-Path $Root 'src\ModelDiscovery.cs') (Join-Path $Root 'tests\ModelDiscoveryTests.cs') (Join-Path $Root 'src\Localization.cs') ('/resource:'+(Join-Path $Root 'locales\messages.json')+',OSS.Translations')
    if ($LASTEXITCODE -ne 0) { throw 'Discovery test compilation failed' }
    . (Join-Path $Root 'scripts\Common.ps1')
    Invoke-Native $exe @()
} finally {
    $full=[IO.Path]::GetFullPath($testDir)
    $temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $full.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($full).StartsWith('OllamaDiscoveryBuild-')) { throw 'Invalid cleanup path' }
    Remove-Item -LiteralPath $full -Recurse -Force
}
