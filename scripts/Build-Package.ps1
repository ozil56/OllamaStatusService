param(
    [Parameter(Mandatory=$true)][string]$DependencyBundle,
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist')
)
# Build without installing services or changing production configuration.
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$bundleRoot = [IO.Path]::GetFullPath($DependencyBundle)
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$packageRoot = Join-Path $outputRoot 'OllamaStatusService'
if (Test-Path -LiteralPath $packageRoot) { throw 'Output package already exists. Choose a new output directory.' }
foreach ($required in @('runtime\python.exe','vendor','bin\WinSW.exe','requirements.lock.txt','manifest.sha256.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot $required))) { throw ('Missing dependency bundle file: '+$required) }
}
. (Join-Path $PSScriptRoot 'Common.ps1')
$verified = Test-PackageIntegrity $bundleRoot
if ($verified -lt 1) { throw 'Dependency bundle integrity validation failed.' }
$wanted = [IO.File]::ReadAllText((Join-Path $sourceRoot 'requirements.lock.txt')).Replace("`r`n","`n").Trim()
$actual = [IO.File]::ReadAllText((Join-Path $bundleRoot 'requirements.lock.txt')).Replace("`r`n","`n").Trim()
if ($wanted -ne $actual) { throw 'Dependency versions differ from the source lock file. Use the matching 1.2.3 release bundle.' }
$wrapperHash = (Get-FileHash -LiteralPath (Join-Path $bundleRoot 'bin\WinSW.exe') -Algorithm SHA256).Hash
if ($wrapperHash -ne 'B5066B7BBDFBA1293E5D15CDA3CAAEA88FBEAB35BD5B38C41C913D492AADFC4F') { throw 'Unexpected WinSW dependency.' }
$pythonVersion = & (Join-Path $bundleRoot 'runtime\python.exe') --version
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne 'Python 3.13.12') { throw 'Expected the Python 3.13.12 x64 embedded runtime.' }
& (Join-Path $bundleRoot 'runtime\python.exe') (Join-Path $sourceRoot 'scripts\Build-Readmes.py') --check
if ($LASTEXITCODE -ne 0) { throw 'README translations are incomplete or stale.' }
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
foreach ($name in @('app','locales','src','scripts','tests','examples','licenses','docs','README.md','CHANGELOG.md','config.example.json','requirements.lock.txt','Deployment-Guide.txt','Integration-Guide.txt','Verification-Report.txt')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination $packageRoot -Recurse
}
if (Test-Path -LiteralPath (Join-Path $sourceRoot 'LICENSE')) { Copy-Item -LiteralPath (Join-Path $sourceRoot 'LICENSE') -Destination $packageRoot }
foreach ($name in @('runtime','vendor','bin')) {
    Copy-Item -LiteralPath (Join-Path $bundleRoot $name) -Destination $packageRoot -Recurse
}
& (Join-Path $packageRoot 'scripts\Build-Setup.ps1') -Root $packageRoot
& (Join-Path $packageRoot 'scripts\Build-Tray.ps1') -Root $packageRoot
& (Join-Path $packageRoot 'runtime\python.exe') (Join-Path $packageRoot 'scripts\Finalize-Package.py')
if ($LASTEXITCODE -ne 0) { throw 'Package finalization failed.' }
& (Join-Path $packageRoot 'scripts\Package.ps1') -Root $packageRoot -ZipPath (Join-Path $outputRoot 'OllamaStatusService-Windows-x64-v1.6.1.zip')
Write-Output ('Built source at '+$sourceRoot+' with verified dependencies. No services were installed.')
