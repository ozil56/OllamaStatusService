param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('oss-localization-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$exe=Join-Path $fixture 'Tests.exe'
$sources=@('src\Localization.cs','src\LocalizationUI.cs','src\ModelDiscovery.cs','src\SetupHost.cs','src\OllamaUpdates.cs','tests\LocalizationTests.cs') | ForEach-Object {Join-Path $Root $_}
& $compiler /nologo /target:exe /platform:x64 /main:LocalizationTests /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll ('/out:'+$exe) ('/resource:'+(Join-Path $Root 'locales\messages.json')+',OSS.Translations') @sources
if($LASTEXITCODE -ne 0){throw 'Localization test compilation failed'}
& $exe $fixture
if($LASTEXITCODE -ne 0){throw 'Native localization tests failed'}
# Check the PowerShell 5.1 engine used by the GUI broker too.
$env:OSS_UI_LANGUAGE='en'
. (Join-Path $Root 'scripts\Localization.ps1')
$value=Get-UiText '正在下载 Ollama 0.40.9：23.5%（123 / 456 MB），服务继续运行。'
if($value -match '[\u4e00-\u9fff]' -or $value -notlike '*23.5%*'){throw ('Formatted progress translation failed: '+$value)}
$value=Get-UiText '模型目录（来源：D:\中文模型）'
if($value -ne 'Model directory (source: D:\中文模型)'){throw ('User path changed: '+$value)}
Write-Output 'PASS PowerShell translated progress and preserved the original path.'
# Exercise the actual worker serializers against fixture paths only.
. (Join-Path $Root 'scripts\Common.ps1')
. (Join-Path $Root 'scripts\OllamaUpdateCore.ps1')
$script:StateRoot=Join-Path $fixture 'state'
$script:OllamaUpdateLog=$null
New-Item -ItemType Directory -Path (Join-Path $script:StateRoot 'updater') | Out-Null
$backup='D:\中文模型\关闭\{0}\backup'
$source='已恢复 OSS 接管，Ollama 版本 0.40.9。原模型和配置验证通过。备份与日志：'+$backup
foreach($language in @('zh-CN','zh-TW','en','ja','ko','de','fr','es')) {
    $script:UiLanguage=$language
    $expected=(Get-UiText '已恢复 OSS 接管，Ollama 版本 {0}。原模型和配置验证通过。备份与日志：{1}') -f '0.40.9',$backup
    Write-OllamaUpdateStatus 'complete' $source $false $true
    $status=Get-Content -LiteralPath (Join-Path $script:StateRoot 'updater\status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($status.source_message -cne $source -or $status.message -cne $expected -or $status.language -ne $language) { throw ('Updater status localization failed: '+$language) }
    foreach($message in @('正在下载官方 Ollama 0.40.9；服务继续运行。','正在安装官方 Ollama 0.40.9，保留原安装路径、模型和配置。请勿关机。')) {
        $value=Get-UiText $message
        if($language -in @('en','ko','de','fr','es') -and $value -match '[\u4e00-\u9fff]'){throw ('Mixed worker message: '+$value)}
    }
}
$tokens=$null; $parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'scripts\SetupWorker.ps1'),[ref]$tokens,[ref]$parseErrors)
$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Write-SetupPhase','Set-Progress','Complete-Setup')},$true) | ForEach-Object { . ([scriptblock]::Create($_.Extent.Text)) }
$BootstrapLog=Join-Path $fixture 'setup.log'; $progressFile=Join-Path $fixture 'progress.json'; $resultFile=Join-Path $fixture 'result.json'
$script:UiLanguage='en'
$source='校验通过，共 716 个文件。未修改系统或现有服务。'
Complete-Setup $true $source
foreach($file in @($progressFile,$resultFile)) {
    $result=Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
    if($result.source_message -cne $source -or $result.language -ne 'en' -or $result.message -match '[\u4e00-\u9fff]' -or $result.message -notmatch '716'){throw ('Setup result localization failed: '+$file)}
}
Write-Output 'PASS PowerShell worker status and setup completion preserve source messages in all eight languages.'

Get-ChildItem -LiteralPath $fixture -Filter '*.json' | ForEach-Object {
    $report=Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    if($report.PSObject.Properties['clipped_labels'] -and $report.clipped_labels.Count){throw ('Clipped native labels in '+$_.Name+': '+($report.clipped_labels -join '; '))}
}
Write-Output ('Evidence: '+$fixture)
