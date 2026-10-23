param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ (('/win32manifest:{0}' -f (Join-Path $Root 'src\SetupHost.manifest'))) /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll (('/out:{0}' -f (Join-Path $Root 'Install.exe'))) (Join-Path $Root 'src\SetupHost.cs') (Join-Path $Root 'src\ModelDiscovery.cs') (Join-Path $Root 'src\Localization.cs') ('/resource:'+(Join-Path $Root 'locales\messages.json')+',OSS.Translations') (Join-Path $Root 'src\LocalizationUI.cs')
if ($LASTEXITCODE -ne 0) { throw '安装程序编译失败。' }
Copy-Item -LiteralPath (Join-Path $Root 'Install.exe') -Destination (Join-Path $Root 'Uninstall.exe') -Force
