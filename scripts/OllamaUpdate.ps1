param([string]$TargetVersion='', [switch]$Recover)
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'OllamaUpdateCore.ps1')
$script:OllamaUpdateLog=$null
$mutex=$null; $owns=$false
try {
    if (-not (Test-Administrator)) { throw '升级需要 Windows 管理员权限，请从 OSS 托盘启动。' }
    $mutex=New-Object Threading.Mutex($false,'Global\OllamaStatusService.Setup')
    try { $owns=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owns=$true }
    if (-not $owns) { throw '另一个 OSS 安装、升级或恢复操作正在执行，请等待完成。' }
    Initialize-OllamaUpdateDirectory
    $script:OllamaUpdateLog=Join-Path $script:StateRoot ('logs\ollama-update-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
    Write-OllamaUpdateStatus 'checking' '正在核对官方版本和当前 OSS 状态…'
    Invoke-OllamaManagedUpdate -TargetVersion $TargetVersion -Recover:$Recover
    exit 0
} catch {
    $failure=$_.Exception.Message
    if ($owns) {
        try { Write-OllamaUpdateStatus 'failed' $failure $false $false }
        catch { Show-Message $failure 'Ollama 更新未启动' }
    } else { Show-Message $failure 'Ollama 更新未启动' }
    exit 1
} finally {
    if ($mutex) { if ($owns) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
}
