. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'WatchdogCore.ps1')
try { Invoke-OssWatchdog }
catch {
    Write-WatchdogLog 'watchdog_error' $_.Exception.Message
    exit 1
}
