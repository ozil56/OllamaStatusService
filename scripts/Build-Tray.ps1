param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll (('/out:{0}' -f (Join-Path $Root 'OllamaTray.exe'))) (Join-Path $Root 'src\OllamaTray.cs') (Join-Path $Root 'src\OllamaUpdates.cs') (Join-Path $Root 'src\Localization.cs') ('/resource:'+(Join-Path $Root 'locales\messages.json')+',OSS.Translations') (Join-Path $Root 'src\LocalizationUI.cs')
if ($LASTEXITCODE -ne 0) { throw '托盘程序编译失败。' }
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll (('/out:{0}' -f (Join-Path $Root 'Open-Tray.exe'))) (Join-Path $Root 'src\TrayLauncher.cs') (Join-Path $Root 'src\Localization.cs') ('/resource:'+(Join-Path $Root 'locales\messages.json')+',OSS.Translations') (Join-Path $Root 'src\LocalizationUI.cs')
if ($LASTEXITCODE -ne 0) { throw '托盘启动器编译失败。' }
