param([string]$Root=(Split-Path -Parent $PSScriptRoot), [switch]$OfficialCheck)
$ErrorActionPreference='Stop'
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('oss-update-ui-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$exe=Join-Path $fixture 'Tests.exe'
& $compiler /nologo /target:exe /platform:x64 /main:OllamaUpdateUiTests /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll ('/out:'+$exe) (Join-Path $Root 'src\OllamaUpdates.cs') (Join-Path $Root 'tests\OllamaUpdateUiTests.cs') (Join-Path $Root 'src\Localization.cs') ('/resource:'+(Join-Path $Root 'locales\messages.json')+',OSS.Translations') (Join-Path $Root 'src\LocalizationUI.cs')
if($LASTEXITCODE -ne 0){throw 'Update UI test compilation failed'}
if($OfficialCheck){ & $exe '--official-check' }else{ & $exe }
if($LASTEXITCODE -ne 0){throw 'Update UI/release checks failed'}
Write-Output ('Test executable: '+$exe)
