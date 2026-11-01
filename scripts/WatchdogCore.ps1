# Windows PowerShell 5.1. Called by the SYSTEM scheduled task, never by HTTP.
$script:WatchdogDelaySeconds = 300
$script:WatchdogRetrySeconds = 300

function Get-WatchdogTime { return [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
function Get-WatchdogBootId { return (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o') }
function Get-WatchdogPath([string]$Name) { return (Join-Path $script:StateRoot ('updater\'+$Name)) }
function Write-WatchdogLog([string]$Event, $Details) {
    $path=Join-Path $script:StateRoot 'logs\watchdog.jsonl'
    if ((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).Length -gt 5MB) {
        Move-Item -LiteralPath $path -Destination ($path+'.1') -Force
    }
    $line=[ordered]@{time=[datetime]::UtcNow.ToString('o');event=$Event;details=$Details} | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::AppendAllText($path,$line+"`r`n",[Text.UTF8Encoding]::new($false))
}
function New-WatchdogState {
    return [pscustomobject]@{schema=1;since=0L;last_seen=0L;last_attempt=0L;recovering=$false;reason='';last_error=''}
}
function Read-WatchdogState {
    $path=Get-WatchdogPath 'watchdog-state.json'
    if (-not (Test-Path -LiteralPath $path)) { return (New-WatchdogState) }
    try {
        $state=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($name in @('schema','since','last_seen','last_attempt','recovering','reason','last_error')) {
            if (-not $state.PSObject.Properties[$name]) { throw 'Incomplete watchdog state' }
        }
        if ($state.schema -ne 1) { throw 'Unsupported watchdog state' }
        return $state
    } catch {
        Write-WatchdogLog 'state_invalid' $_.Exception.Message
        return (New-WatchdogState)
    }
}
function Save-WatchdogState($State) {
    $path=Get-WatchdogPath 'watchdog-state.json'
    $temp=$path+'.tmp'
    Save-Json $State $temp
    if (Test-Path -LiteralPath $path) { [IO.File]::Replace($temp,$path,[System.Management.Automation.Language.NullString]::Value) }
    else { [IO.File]::Move($temp,$path) }
}
function Reset-WatchdogRecovery {
    foreach ($name in @('watchdog-state.json','watchdog-recovery.lock')) {
        $path=Get-WatchdogPath $name
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
}
function Test-WatchdogSuspended {
    if (Test-Path -LiteralPath (Get-WatchdogPath 'maintenance.lock')) { return $true }
    $stopMarker=Get-WatchdogPath 'operator-stop.json'
    if (Test-Path -LiteralPath $stopMarker) {
        $intent=Get-Content -LiteralPath $stopMarker -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $intent.PSObject.Properties['boot_id'] -or $intent.boot_id -eq (Get-WatchdogBootId)) { return $true }
        # Windows automatically starts the services after reboot. An old stop
        # intent must not silently disable supervision for that new lifecycle.
        Remove-Item -LiteralPath $stopMarker -Force
        Write-WatchdogLog 'operator_stop_expired_after_reboot' @{}
    }
    return $false
}
function Invoke-WatchdogApi([string]$Url) {
    $request=[Net.HttpWebRequest]::Create($Url)
    $request.Proxy=$null; $request.Timeout=4000; $request.ReadWriteTimeout=4000
    $response=$request.GetResponse()
    try {
        $reader=New-Object IO.StreamReader($response.GetResponseStream())
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
        finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}
function Get-WatchdogStatus([string]$BaseUrl) {
    $status=Invoke-WatchdogApi ($BaseUrl+'/status')
    if ($status.service -ne 'OllamaStatusService' -or
        $status.state -notin @('idle','busy','unknown','offline','stopping','maintenance')) {
        throw 'Unrecognized OSS status response'
    }
    foreach ($name in @('inflight_requests','queued_requests','uncertain_requests','ollama_online','accepting_requests','can_start_now')) {
        if (-not $status.PSObject.Properties[$name]) { throw ('Missing status field: '+$name) }
    }
    return $status
}
function Test-WatchdogActiveWork($Status) {
    if ($Status.PSObject.Properties['execution']) {
        if ($Status.execution.inference_active) { return $true }
    } elseif ($Status.inflight_requests -gt 0) { return $true }
    if ($Status.PSObject.Properties['inflight_model_transfers'] -and $Status.inflight_model_transfers -gt 0) { return $true }
    if ($Status.PSObject.Properties['execution'] -and $Status.execution.PSObject.Properties['model_workers']) {
        if ($Status.execution.model_workers -gt 0) { return $true }
    } elseif ($Status.PSObject.Properties['model_jobs'] -and $Status.model_jobs.active -gt 0) { return $true }
    if ($Status.PSObject.Properties['ollama_update'] -and
        ($Status.ollama_update.model_workers -gt 0 -or
         $Status.ollama_update.inflight_http_operations -gt $Status.queued_requests)) { return $true }
    if ($Status.PSObject.Properties['download_network'] -and
        $Status.download_network.PSObject.Properties['active_tunnels'] -and $Status.download_network.active_tunnels -gt 0) { return $true }
    return $false
}
function Get-WatchdogFailure($Status) {
    if (Test-WatchdogActiveWork $Status) { return '' }
    if ($Status.state -eq 'unknown') { return 'unknown' }
    if (-not $Status.ollama_online) { return 'backend_offline' }
    if ($Status.PSObject.Properties['execution'] -and -not $Status.execution.dispatcher_running) { return 'dispatcher_stopped' }
    if ($Status.inflight_requests -gt 0 -or $Status.queued_requests -gt 0) { return 'scheduler_stalled' }
    if ($Status.state -ne 'idle' -or -not $Status.accepting_requests -or -not $Status.can_start_now) { return 'not_accepting_without_work' }
    return ''
}
function Test-WatchdogServicesRunning {
    foreach ($name in @($script:GatewayName,$script:BackendName)) {
        $service=Get-Service -Name $name -ErrorAction Stop
        try { if ($service.Status -ne 'Running') { return $false } }
        finally { if ($service -is [IDisposable]) { $service.Dispose() } }
    }
    return $true
}
function Wait-WatchdogReady([string]$BaseUrl) {
    # Admission is still gated here, so 'maintenance' is the expected state.
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try {
            $status=Get-WatchdogStatus $BaseUrl
            if ($status.ollama_online -and $status.uncertain_requests -eq 0 -and
                $status.state -in @('idle','busy','maintenance') -and
                (-not $status.PSObject.Properties['execution'] -or $status.execution.dispatcher_running)) { return }
        } catch {}
        Start-Sleep -Seconds 2
    }
    throw 'OSS did not become ready after service recovery'
}
function Invoke-WatchdogRecovery($State,[string]$BaseUrl) {
    if (Test-WatchdogSuspended) { return }
    $gate=Get-WatchdogPath 'watchdog-recovery.lock'
    if (-not $State.recovering -or -not (Test-Path -LiteralPath $gate)) {
        try {
            $latest=Get-WatchdogStatus $BaseUrl
            if (-not (Get-WatchdogFailure $latest)) {
                Save-WatchdogState (New-WatchdogState)
                return
            }
        } catch {}
    }
    $gateHandle=$null
    try {
        # A failed attempt keeps admission closed until a later retry succeeds.
        $gateHandle=[IO.File]::Open($gate,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::Write,[IO.FileShare]::Read)
        if (Test-WatchdogSuspended) { return }
        $status=$null
        try { $status=Get-WatchdogStatus $BaseUrl } catch {}
        if ($status -and (Test-WatchdogActiveWork $status)) {
            Write-WatchdogLog 'deferred_active_work' @{reason=$State.reason}
            $gateHandle.Dispose(); $gateHandle=$null
            Remove-Item -LiteralPath $gate -Force
            Save-WatchdogState (New-WatchdogState)
            return
        }
        $State.recovering=$true
        $State.last_attempt=Get-WatchdogTime
        Save-WatchdogState $State  # Durable BEFORE stopping either service.
        Write-WatchdogLog 'recovery_started' @{reason=$State.reason;status=$status}
        if (Test-WatchdogSuspended) { return }
        Stop-ManagedServices
        # Confirms listener AND owned processes are gone. maintenance.py also
        # checks IPv4/IPv6 listeners and takes the exclusive journal lock.
        Clear-ConfirmedTasks -Automatic
        Start-ManagedServices
        Wait-WatchdogReady $BaseUrl
        $gateHandle.Dispose(); $gateHandle=$null
        Remove-Item -LiteralPath $gate -Force
        $ready=Get-WatchdogStatus $BaseUrl
        if (Get-WatchdogFailure $ready) { throw 'OSS is still unavailable after reopening admission' }
        Write-WatchdogLog 'recovery_succeeded' @{previous_reason=$State.reason;state=$ready.state}
        Save-WatchdogState (New-WatchdogState)
    } catch {
        $State.recovering=$true
        $State.last_attempt=Get-WatchdogTime
        $State.last_error=$_.Exception.Message
        Save-WatchdogState $State
        Write-WatchdogLog 'recovery_failed' @{reason=$State.reason;error=$State.last_error;retry_seconds=$script:WatchdogRetrySeconds}
    } finally { if ($gateHandle) { $gateHandle.Dispose() } }
}
function Invoke-OssWatchdog {
    if (Test-WatchdogSuspended) { return }
    $mutex=$null
    try {
        # IgnoreNew in Task Scheduler plus a lock covering manual invocations.
        try { $mutex=[IO.File]::Open((Get-WatchdogPath 'watchdog-run.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::Write,[IO.FileShare]::None) }
        catch [IO.IOException] { return }
        if (Test-WatchdogSuspended) { return }
        $config=Get-Content -LiteralPath (Join-Path $script:StateRoot 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $port=if ($config.PSObject.Properties['listen_port']) { [int]$config.listen_port } else { 11434 }
        $hostName=if ($config.PSObject.Properties['listen_host']) { [string]$config.listen_host } else { '0.0.0.0' }
        $hostName=switch ($hostName) { '0.0.0.0' {'127.0.0.1'} '::' {'[::1]'} default { $hostName } }
        if ($hostName.Contains(':') -and -not $hostName.StartsWith('[')) { $hostName='['+$hostName+']' }
        $baseUrl='http://'+$hostName+':'+$port
        $state=Read-WatchdogState
        $now=Get-WatchdogTime
        if (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock')) { $state.recovering=$true }
        if ($state.recovering) {
            if ($now -lt $state.last_attempt -or $now-$state.last_attempt -ge $script:WatchdogRetrySeconds) {
                Invoke-WatchdogRecovery $state $baseUrl
            }
            return
        }
        $failure=''; $status=$null
        try {
            if (-not (Test-WatchdogServicesRunning)) { $failure='service_stopped' }
            else {
                $status=Get-WatchdogStatus $baseUrl
                $failure=Get-WatchdogFailure $status
            }
        } catch { $failure='status_unreachable' }
        if (-not $failure) {
            if ($state.since) { Write-WatchdogLog 'condition_cleared' @{previous_reason=$state.reason} }
            Save-WatchdogState (New-WatchdogState)
            return
        }
        if (-not $state.since -or $now -lt $state.last_seen -or $now-$state.last_seen -gt 180) {
            $state.since=$now
            Write-WatchdogLog 'condition_detected' @{reason=$failure;status=$status;delay_seconds=$script:WatchdogDelaySeconds}
        }
        # Switching between unavailable states must not restart the countdown.
        $state.reason=$failure; $state.last_seen=$now
        Save-WatchdogState $state
        if ($now-$state.since -ge $script:WatchdogDelaySeconds) { Invoke-WatchdogRecovery $state $baseUrl }
    } finally { if ($mutex) { $mutex.Dispose() } }
}
