param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('oss-dialogs-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$exe=Join-Path $fixture 'Tests.exe'
$sources=@('src\Localization.cs','src\LocalizationUI.cs','src\ModelDiscovery.cs','src\SetupHost.cs','tests\DialogTests.cs') | ForEach-Object {Join-Path $Root $_}
& $compiler /nologo /target:exe /platform:x64 /main:DialogTests /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll ('/out:'+$exe) ('/resource:'+(Join-Path $Root 'locales\messages.json')+',OSS.Translations') @sources
if($LASTEXITCODE -ne 0){throw 'Dialog test compilation failed'}
& $exe $fixture
if($LASTEXITCODE -ne 0){throw 'Native dialog checks failed'}
# Exercise the actual PowerShell 5.1 dialog bridge used by elevated tray workers.
$env:OSS_UI_LANGUAGE='zh-CN'
. (Join-Path $Root 'scripts\Localization.ps1')
Initialize-UiDialogs
$script:failure=$null
$timer=New-Object Windows.Forms.Timer
$timer.Interval=150
$timer.Add_Tick({
    $timer.Stop()
    $form=[Windows.Forms.Application]::OpenForms[0]
    try {
        $cancel=$form.Controls.Find('cancel',$true)[0]
        if($cancel.Text -ne '取消' -or $form.AcceptButton -ne $cancel -or $cancel.FlatStyle -ne 'Flat'){throw 'PowerShell worker theme or default changed'}
        $cancel.PerformClick()
    } catch { $script:failure=$_.Exception.Message; $form.Close() }
})
try {
    $timer.Start()
    if(Show-UiConfirmation '此操作会停止 Ollama，尚未完成的任务将被中止。是否继续？' 'Ollama 状态服务'){throw 'Cancellation became confirmation'}
    if($script:failure){throw $script:failure}
} finally {$timer.Dispose()}
Write-Output 'PASS PowerShell management worker uses the shared dialog and cancels safely.'
Write-Output ('Evidence: '+$fixture)
