# Real watchdog state machine, filesystem gates and persistence; all OS/HTTP
# boundaries are fixtures. NEVER stops production services or uses real ports.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
. (Join-Path $Root 'scripts\Common.ps1')
. (Join-Path $Root 'scripts\WatchdogCore.ps1')
$readyImplementation=(Get-Item Function:Wait-WatchdogReady).ScriptBlock
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('oss-watchdog-tests-'+[guid]::NewGuid().ToString('N'))
$held=$null
New-Item -ItemType Directory -Path $fixture | Out-Null
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function New-Status([string]$State='unknown') {
    return [pscustomobject]@{service='OllamaStatusService';state=$State;ollama_online=$true;
        inflight_requests=0;queued_requests=0;uncertain_requests=$(if($State -eq 'unknown'){1}else{0});
        accepting_requests=($State -eq 'idle');can_start_now=($State -eq 'idle');inflight_model_transfers=0;
        execution=[pscustomobject]@{inference_active=$false;dispatcher_running=$true};
        model_jobs=[pscustomobject]@{active=0};
        ollama_update=[pscustomobject]@{model_workers=0;inflight_http_operations=0};
        download_network=[pscustomobject]@{active_tunnels=0}}
}
function Get-WatchdogTime { return $script:clock }
function Get-WatchdogBootId { return 'fixture-current-boot' }
function Test-WatchdogServicesRunning { return $script:servicesRunning }
function Invoke-WatchdogApi {
    param($Url)
    $script:urls.Add($Url)
    if ($script:unreachable) { throw 'fixture timeout' }
    if ($script:race -and (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock'))) {
        $script:status.execution.inference_active=$true
    }
    return $script:status
}
function Stop-ManagedServices {
    $script:events.Add('stop')
    Assert (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock')) 'Stopped before gating admission'
    Assert ((Read-WatchdogState).recovering) 'Stopped before durable recovery record'
    if ($script:failure -eq 'stop') { throw 'fixture stop failure' }
    $script:servicesRunning=$false
}
function Clear-ConfirmedTasks {
    $script:events.Add('clear')
    Assert (-not $script:servicesRunning) 'Cleared before stopping services'
    if ($script:failure -eq 'clear') { throw 'fixture backend still alive' }
}
function Start-ManagedServices {
    $script:events.Add('start')
    if ($script:failure -eq 'start') { throw 'fixture startup failure' }
    $script:servicesRunning=$true; $script:unreachable=$false
    $script:status=New-Status 'idle'
}
function Wait-WatchdogReady {
    param($BaseUrl)
    $script:events.Add('verify')
    if ($script:failure -eq 'verify') { throw 'fixture readiness failure' }
}
function Tick([int]$Seconds) { $script:clock=10000+$Seconds; Invoke-OssWatchdog }
function Advance-To-Recovery { foreach ($seconds in @(0,60,120,180,240,299,300)) { Tick $seconds } }
$cases=@('unknown','offline','unreachable','stopped','idle-not-accepting','stale-busy','dead-dispatcher','queue-stalled',
    'healthy','busy','download','model-worker','model-job','upload','relay','offline-active','race','transient','alternating',
    'manual-stop','maintenance','stop-failure','clear-failure','start-failure','verify-failure','orphan-marker',
    'clock-backward','observation-gap','custom-listener','run-lock','corrupt-state','manual-stop-current-boot','old-stop-after-reboot','stale-model-job')
try {
    foreach ($case in $cases) {
        $script:StateRoot=Join-Path $fixture $case
        New-Item -ItemType Directory -Path (Join-Path $script:StateRoot 'updater'),(Join-Path $script:StateRoot 'logs') -Force | Out-Null
        Save-Json @{listen_host='0.0.0.0';listen_port=11434} (Join-Path $script:StateRoot 'config.json')
        $script:status=New-Status
        $script:servicesRunning=$true; $script:unreachable=$false; $script:race=$false; $script:failure=''
        $script:events=New-Object 'System.Collections.Generic.List[string]'
        $script:urls=New-Object 'System.Collections.Generic.List[string]'
        switch ($case) {
            'offline' { $script:status.state='offline'; $script:status.ollama_online=$false }
            'unreachable' { $script:unreachable=$true }
            'stopped' { $script:servicesRunning=$false; $script:unreachable=$true }
            'idle-not-accepting' { $script:status=New-Status 'idle'; $script:status.accepting_requests=$false }
            'stale-busy' { $script:status=New-Status 'busy'; $script:status.inflight_requests=1 }
            'dead-dispatcher' { $script:status=New-Status 'idle'; $script:status.execution.dispatcher_running=$false }
            'queue-stalled' { $script:status=New-Status 'busy'; $script:status.queued_requests=1; $script:status.ollama_update.inflight_http_operations=1 }
            'healthy' { $script:status=New-Status 'idle' }
            'busy' { $script:status=New-Status 'busy'; $script:status.execution.inference_active=$true; $script:status.inflight_requests=1 }
            'download' { $script:status.inflight_model_transfers=1 }
            'model-worker' { $script:status.ollama_update.model_workers=1 }
            'model-job' { $script:status.model_jobs.active=1 }
            'stale-model-job' {
                $script:status=New-Status 'busy'; $script:status.model_jobs.active=1
                $script:status.execution | Add-Member NoteProperty model_workers 0
            }
            'upload' { $script:status.ollama_update.inflight_http_operations=1 }
            'relay' { $script:status.download_network.active_tunnels=1 }
            'offline-active' { $script:status.state='offline'; $script:status.ollama_online=$false; $script:status.execution.inference_active=$true }
            'race' { $script:race=$true }
            'manual-stop' { Save-Json @{} (Get-WatchdogPath 'operator-stop.json') }
            'manual-stop-current-boot' { Save-Json @{boot_id='fixture-current-boot'} (Get-WatchdogPath 'operator-stop.json') }
            'old-stop-after-reboot' { Save-Json @{boot_id='fixture-old-boot'} (Get-WatchdogPath 'operator-stop.json') }
            'maintenance' { Save-Json @{} (Get-WatchdogPath 'maintenance.lock') }
            'stop-failure' { $script:failure='stop' }
            'clear-failure' { $script:failure='clear' }
            'start-failure' { $script:failure='start' }
            'verify-failure' { $script:failure='verify' }
            'orphan-marker' { [IO.File]::WriteAllText((Get-WatchdogPath 'watchdog-recovery.lock'),'') }
            'custom-listener' { Save-Json @{listen_host='::';listen_port=23456} (Join-Path $script:StateRoot 'config.json') }
            'run-lock' { $held=[IO.File]::Open((Get-WatchdogPath 'watchdog-run.lock'),[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None) }
            'corrupt-state' { [IO.File]::WriteAllText((Get-WatchdogPath 'watchdog-state.json'),'{bad json') }
        }
        if ($case -eq 'orphan-marker') { Tick 0 }
        elseif ($case -eq 'transient') {
            Tick 0; Tick 60; $script:status=New-Status 'idle'; Tick 120
            $script:status=New-Status; Tick 180; Tick 240; Tick 300
        } elseif ($case -eq 'alternating') {
            foreach ($s in @(0,60,120,180,240,300)) {
                $script:status=New-Status $(if($s % 120 -eq 0){'unknown'}else{'offline'})
                Tick $s
            }
        } elseif ($case -eq 'clock-backward') { Tick 0; Tick 60; Tick -60; Tick 0; Tick 60; Tick 120 }
        elseif ($case -eq 'observation-gap') { Tick 0; Tick 240; Tick 300 }
        else {
            foreach ($s in @(0,60,120,180,240,299)) { Tick $s }
            Assert ($script:events.Count -eq 0) ('Premature recovery: '+$case)
            Tick 300
        }
        if ($case -in @('healthy','busy','download','model-worker','model-job','upload','relay','offline-active','race','transient',
                         'manual-stop','manual-stop-current-boot','maintenance','clock-backward','observation-gap','run-lock')) {
            Assert ($script:events.Count -eq 0) ('Interrupted work or maintenance: '+$case)
            if ($case -eq 'race') { Assert (-not (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock'))) 'Race leaked gate' }
        } elseif ($case -like '*-failure') {
            Assert ((Read-WatchdogState).recovering) ('Lost retry: '+$case)
            Assert (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock')) ('Failure reopened admission: '+$case)
            if ($case -eq 'stop-failure') { Assert (-not $script:events.Contains('clear')) 'Cleared after failed stop' }
            if ($case -eq 'clear-failure') { Assert (-not $script:events.Contains('start')) 'Started before verified clearing' }
            $count=$script:events.Count; Tick 360; Tick 599
            Assert ($script:events.Count -eq $count) 'Retry storm'
            $script:failure=''; Tick 600
            Assert (-not (Read-WatchdogState).recovering) 'Failed retry did not resume'
            Assert (-not (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock'))) 'Retry leaked gate'
        } else {
            Assert (($script:events -join ',') -eq 'stop,clear,start,verify') ('Wrong recovery sequence: '+$case+': '+($script:events -join ','))
            Assert (-not (Read-WatchdogState).recovering) 'Recovery did not finish'
            Assert (-not (Test-Path -LiteralPath (Get-WatchdogPath 'watchdog-recovery.lock'))) 'Success leaked gate'
        }
        if ($case -eq 'custom-listener') { Assert ($script:urls[0] -eq 'http://[::1]:23456/status') 'Configured listener ignored' }
        if ($case -eq 'run-lock') { $held.Dispose() }
        Write-Output ('PASS '+$case)
    }
    Write-Output ('PASS '+$cases.Count+' watchdog scenarios')
    function Start-Sleep { param($Seconds) }
    $script:status=New-Status 'maintenance'
    & $readyImplementation 'http://fixture'
    foreach ($case in @('unknown','offline','dead-dispatcher')) {
        $script:status=New-Status $(if($case -eq 'dead-dispatcher'){'idle'}else{$case})
        if ($case -eq 'offline') { $script:status.ollama_online=$false }
        if ($case -eq 'dead-dispatcher') { $script:status.execution.dispatcher_running=$false }
        $caught=$false
        try { & $readyImplementation 'http://fixture' } catch { $caught=$true }
        Assert $caught ('Readiness incorrectly succeeded: '+$case)
    }
    Write-Output 'PASS actual readiness verification: healthy gate, unknown, offline and dead dispatcher'

    # Execute the actual tray lifecycle body with only its OS/UI boundaries replaced.
    $manage=Get-Content -LiteralPath (Join-Path $Root 'scripts\Manage.ps1') -Raw -Encoding UTF8
    $manage=[regex]::Replace($manage,'(?m)^\. \(Join-Path \$PSScriptRoot[^\r\n]+\r?\n','')
    $manageBlock=[scriptblock]::Create($manage)
    function Test-Administrator { return $true }
    function Show-UiConfirmation { return $true }
    function Disable-ScheduledTask {}
    function Stop-ScheduledTask {}
    function Enable-ScheduledTask {}
    function Stop-ManagedServices { $script:events.Add('stop') }
    function Clear-ConfirmedTasks { $script:events.Add('clear') }
    function Start-ManagedServices { $script:events.Add('start') }
    function Show-Message { param($Message); throw $Message }
    foreach ($action in @('Stop','Start','Recover')) {
        $script:events.Clear()
        & $manageBlock -Action $action
        Assert ((Test-Path -LiteralPath (Get-WatchdogPath 'operator-stop.json')) -eq ($action -eq 'Stop')) ('Wrong operator intent: '+$action)
        $expected=if($action -eq 'Stop'){'stop,clear'}elseif($action -eq 'Start'){'start'}else{'stop,clear,start'}
        Assert (($script:events -join ',') -eq $expected) ('Wrong tray sequence: '+$action)
    }
    Write-Output 'PASS actual tray Stop/Start/Recover preserves intentional-stop semantics'
} finally {
    if ($held) { $held.Dispose() }
    $resolved=[IO.Path]::GetFullPath($fixture)
    $base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $resolved.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($resolved).StartsWith('oss-watchdog-tests-')) { throw 'Unexpected cleanup boundary' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
