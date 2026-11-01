param([ValidateSet('Start','Stop','Recover')][string]$Action='Start')
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'WatchdogCore.ps1')
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
    }
    Disable-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue | Out-Null
    Stop-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue
    try {
        $stopMarker=Get-WatchdogPath 'operator-stop.json'
        if ($Action -eq 'Stop') { Save-Json @{stopped_at=[datetime]::UtcNow.ToString('o');boot_id=(Get-WatchdogBootId)} $stopMarker }
        elseif (Test-Path -LiteralPath $stopMarker) { Remove-Item -LiteralPath $stopMarker -Force }
        if ($Action -in @('Stop','Recover') -or (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock'))) {
            Stop-ManagedServices
            Clear-ConfirmedTasks
        }
        Reset-WatchdogRecovery
        if ($Action -ne 'Stop') { Start-ManagedServices }
    } finally { Enable-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue | Out-Null }
} catch { Show-Message ('操作失败：' + $_.Exception.Message); exit 1 }
