param([ValidateSet('Start','Stop','Recover')][string]$Action='Start')
. (Join-Path $PSScriptRoot 'Common.ps1')
if (-not (Test-Administrator)) {
    try { $p=Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList ('-NoProfile -STA -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Action ' + $Action) -Verb RunAs -Wait -PassThru -WindowStyle Normal; exit $p.ExitCode }
    catch { exit 1 }
}
try {
    if ($Action -in @('Stop','Recover')) {
        Add-Type -AssemblyName System.Windows.Forms
        $answer=[Windows.Forms.MessageBox]::Show('此操作会停止 Ollama，尚未完成的任务将被中止。是否继续？','Ollama 状态服务','YesNo','Warning')
        if ($answer -ne 'Yes') { exit 0 }
        # Stop watchdog while performing intentional maintenance; task itself also ignores stopped services.
        Disable-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue | Out-Null
        Stop-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue
        try { Stop-ManagedServices; Clear-ConfirmedTasks }
        finally { Enable-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue | Out-Null }
    }
    if ($Action -ne 'Stop') { Start-ManagedServices }
} catch { Show-Message ('操作失败：' + $_.Exception.Message); exit 1 }
