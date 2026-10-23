param([Parameter(Mandatory=$true)][string]$ContextPath)
. (Join-Path $PSScriptRoot 'Common.ps1')
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$context=Get-Content -LiteralPath $ContextPath -Raw -Encoding UTF8 | ConvertFrom-Json
$BootstrapLog=[string]$context.BootstrapLog
$progressFile=[string]$context.ProgressFile
$resultFile=[string]$context.ResultFile
function Write-SetupPhase([string]$Message) {
    $line='[{0}] PID={1} {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),$PID,$Message
    Add-Content -LiteralPath $BootstrapLog -Value $line -Encoding UTF8
    Write-Host $Message
}
function Set-Progress([string]$Message, [bool]$Active=$true) {
    Write-SetupPhase (Get-UiText $Message)
    Save-Json ([ordered]@{source_message=$Message; language=$script:UiLanguage; message=(Get-UiText $Message); active=$Active}) $progressFile
}
function Complete-Setup([bool]$Success, [string]$Message) {
    Set-Progress $Message $false
    Save-Json ([ordered]@{source_message=$Message; language=$script:UiLanguage; success=$Success; message=(Get-UiText $Message)}) $resultFile
}
try {
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class SetupConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
    Write-SetupPhase ('WORKER_CONSOLE_HANDLE=' + [SetupConsoleProbe]::GetConsoleWindow().ToInt64())
    $packageRoot=Split-Path -Parent $PSScriptRoot
    if ($context.Action -eq 'Verify') {
        Set-Progress '正在校验安装包文件…'
        $count=Test-PackageIntegrity $packageRoot
        Write-SetupPhase ('PACKAGE_VERIFIED_FILES=' + $count)
        Complete-Setup $true ('校验通过，共 ' + $count + ' 个文件。未修改系统或现有服务。')
        exit 0
    }
    if (-not (Test-Administrator)) { throw '安装和卸载需要管理员权限，请双击 Install.exe 或 Uninstall.exe。' }
    $marker=Join-Path $script:StateRoot 'updater\maintenance.lock'
    if (Test-Path -LiteralPath $marker) {
        $pending=$null
        try { $pending=Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
        # SetupHost holds the shared setup mutex; a previous OSS installer is gone.
        if ($pending -and $pending.PSObject.Properties['kind'] -and $pending.kind -eq 'oss-install') { Remove-Item -LiteralPath $marker }
        else { throw 'Ollama 官方升级尚未恢复，请先通过 OSS 托盘恢复，再安装或卸载 OSS。' }
    }
    if (-not [Environment]::Is64BitOperatingSystem) { throw '此安装包只支持 Windows x64。' }
    if ($context.Action -eq 'Install') { . (Join-Path $PSScriptRoot 'Setup.ps1') }
    elseif ($context.Action -eq 'Upgrade') { . (Join-Path $PSScriptRoot 'Upgrade.ps1') }
    elseif ($context.Action -eq 'Uninstall') { . (Join-Path $PSScriptRoot 'Uninstall.ps1') }
    else { throw '未知操作。' }
} catch {
    $message=$_.Exception.Message
    Write-SetupPhase ($message + "`r`n" + $_.ScriptStackTrace)
    Complete-Setup $false $message
    exit 1
}
