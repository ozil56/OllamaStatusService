param([ValidateSet('Start','Stop','Recover')][string]$Action='Start')
. (Join-Path $PSScriptRoot 'Common.ps1')
if (-not (Test-Administrator)) {
    try { $p=Start-Process (Join-Path (Split-Path -Parent $PSScriptRoot) 'OllamaTray.exe') -ArgumentList ('--manage-worker '+$Action) -Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode }
    catch { exit 1 }
}
try {
    if (Test-Path -LiteralPath (Join-Path $script:StateRoot 'updater\maintenance.lock')) { throw 'Ollama 升级维护中，请从托盘的“检查更新／升级 Ollama”查看进度或恢复。' }
    if ($Action -in @('Stop','Recover')) {
        Add-Type -AssemblyName System.Windows.Forms
        $answer=Show-UiConfirmation '此操作会停止 Ollama，尚未完成的任务将被中止。是否继续？' 'Ollama 状态服务'
        if (-not $answer) { exit 0 }
        # Stop watchdog while performing intentional maintenance; task itself also ignores stopped services.
        Disable-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue | Out-Null
        Stop-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue
        try { Stop-ManagedServices; Clear-ConfirmedTasks }
        finally { Enable-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue | Out-Null }
    }
    if ($Action -ne 'Stop') { Start-ManagedServices }
} catch { Show-Message ('操作失败：' + $_.Exception.Message); exit 1 }
