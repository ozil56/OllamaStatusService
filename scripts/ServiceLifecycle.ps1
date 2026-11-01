# Windows PowerShell 5.1. Only controls the two dedicated OSS services.
function Get-ServiceStopTime { return [Diagnostics.Stopwatch]::GetTimestamp() / [double][Diagnostics.Stopwatch]::Frequency }
function Write-ServiceControlLog([string]$Event,[string]$Name,$Details) {
    $record=[ordered]@{time=[datetime]::UtcNow.ToString('o');event=$Event;service=$Name;details=$Details}
    $line=$record | ConvertTo-Json -Compress -Depth 6
    Write-Host $line
    try {
        $directory=Join-Path $script:StateRoot 'logs'
        if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
        $path=Join-Path $directory 'service-control.jsonl'
        if ((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).Length -gt 5MB) {
            Move-Item -LiteralPath $path -Destination ($path+'.1') -Force
        }
        [IO.File]::AppendAllText($path,$line+"`r`n",[Text.UTF8Encoding]::new($false))
    } catch { Write-Warning ('Service control log unavailable: '+$_.Exception.Message) }
}
function Get-ManagedServiceState([string]$Name) {
    try { $service=Get-Service -Name $Name -ErrorAction Stop }
    catch {
        if ($_.FullyQualifiedErrorId -like 'NoServiceFoundForGivenName*') { return 'Missing' }
        throw
    }
    if (-not $service) { return 'Missing' }
    try { return [string]$service.Status }
    finally { $service.Dispose() }
}
function Send-ManagedStop([string]$Name) {
    $service=Get-Service -Name $Name -ErrorAction Stop
    try { $service.Stop() } finally { $service.Dispose() }
}
function Get-ManagedStopRecordPath([string]$Name) {
    if ($Name -notin @($script:GatewayName,$script:BackendName)) { throw 'Refusing to control a non-OSS service' }
    return (Join-Path $script:StateRoot ('updater\forced-stop-'+$Name+'.json'))
}
function Get-ManagedServiceRegistration([string]$Name) {
    $null=Get-ManagedStopRecordPath $Name
    return (Get-CimInstance Win32_Service -Filter ("Name='"+$Name+"'") -ErrorAction Stop)
}
function Assert-ManagedRegistration($Service,[string]$Name) {
    $expected=Join-Path $script:InstallRoot ($Name+'.exe')
    if (-not $Service -or $Service.Name -ne $Name -or $Service.ServiceType -ne 'Own Process' -or
        $Service.PathName.Trim().Trim('"') -ne $expected) {
        throw ('Service registration does not identify the dedicated OSS executable: '+$Name)
    }
    return $expected
}
function Read-ManagedStopRecord([string]$Name) {
    $path=Get-ManagedStopRecordPath $Name
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $record=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.name -ne $Name -or $record.executable -ne (Join-Path $script:InstallRoot ($Name+'.exe')) -or
        $record.start_mode -notin @('Auto','Manual','Disabled') -or $record.delayed -notin @(0,1)) {
        throw ('Invalid saved OSS service start mode: '+$Name)
    }
    return $record
}
function Suspend-ManagedServiceRestart($Service,[string]$Name) {
    $expected=Assert-ManagedRegistration $Service $Name
    $path=Get-ManagedStopRecordPath $Name
    $saved=Read-ManagedStopRecord $Name
    if (-not $saved) {
        Initialize-OllamaUpdateDirectory
        $key='Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\'+$Name
        $properties=Get-ItemProperty -LiteralPath $key -ErrorAction Stop
        $delayed=if ($properties.PSObject.Properties['DelayedAutoStart']) { [int]$properties.DelayedAutoStart } else { 0 }
        $record=[ordered]@{name=$Name;executable=$expected;start_mode=[string]$Service.StartMode;delayed=$delayed}
        if ($record.start_mode -notin @('Auto','Manual','Disabled')) { throw 'Unrecognized service start mode' }
        # Persist BEFORE disabling. A killed coordinator can restore this later.
        $temp=$path+'.tmp'; Save-Json $record $temp; [IO.File]::Move($temp,$path)
    }
    # Keep disabled until an explicit managed start/uninstall; this prevents SCM
    # failure-restart actions from racing journal cleanup after a forced kill.
    Set-Service -Name $Name -StartupType Disabled -ErrorAction Stop
}
function Restore-ManagedServiceStartMode([string]$Name) {
    $record=Read-ManagedStopRecord $Name
    if (-not $record) { return }
    $current=Get-ManagedServiceRegistration $Name
    $null=Assert-ManagedRegistration $current $Name
    if ($current.StartMode -ne 'Disabled') {
        # The updater deliberately selects Manual before its verification start.
        # Respect that newer decision instead of restoring a saved Disabled mode.
        Write-ServiceControlLog 'start_mode_already_restored' $Name @{mode=$current.StartMode}
        Remove-Item -LiteralPath (Get-ManagedStopRecordPath $Name) -Force
        return
    }
    $mode=@{Auto='Automatic';Manual='Manual';Disabled='Disabled'}[$record.start_mode]
    Set-Service -Name $Name -StartupType $mode -ErrorAction Stop
    Set-ItemProperty -LiteralPath ('Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\'+$Name) -Name DelayedAutoStart -Value ([int]$record.delayed) -ErrorAction Stop
    Write-ServiceControlLog 'start_mode_restored' $Name @{mode=$record.start_mode;delayed=$record.delayed}
    Remove-Item -LiteralPath (Get-ManagedStopRecordPath $Name) -Force
}
function Remove-ManagedStopRecord([string]$Name) {
    $path=Get-ManagedStopRecordPath $Name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
function Stop-ManagedServiceProcess([string]$Name) {
    if (-not (Test-Administrator)) { throw 'Administrator elevation is required to force-stop OSS' }
    $registration=Get-ManagedServiceRegistration $Name
    if (-not $registration -or $registration.State -eq 'Stopped') { return }
    $expected=Assert-ManagedRegistration $registration $Name
    $servicePid=[int]$registration.ProcessId
    if ($servicePid -le 0 -or $servicePid -eq $PID) { throw 'No valid dedicated service process to stop' }
    $process=Get-Process -Id $servicePid -ErrorAction Stop
    try {
        # Pin the process handle and check executable identity, not merely a PID
        # or image name. Never kill unrelated Python/Ollama processes by name.
        $null=$process.Handle
        if ($process.HasExited) { return }
        if ($process.MainModule.FileName -ne $expected) { throw 'Service process executable differs from its registration' }
        $owners=@(Get-CimInstance Win32_Service -Filter ("ProcessId="+$servicePid) -ErrorAction Stop)
        if ($owners.Count -ne 1 -or $owners[0].Name -ne $Name) { throw 'Refusing to terminate a shared service process' }
        Suspend-ManagedServiceRestart $registration $Name
        $current=Get-ManagedServiceRegistration $Name
        if (-not $current -or $current.State -eq 'Stopped' -or $process.HasExited) { return }
        $null=Assert-ManagedRegistration $current $Name
        if ([int]$current.ProcessId -ne $servicePid) { throw 'Service PID changed during force-stop; refusing stale termination' }
        Write-ServiceControlLog 'force_stop' $Name @{pid=$servicePid;executable=$expected}
        Invoke-Native (Join-Path $env:SystemRoot 'System32\taskkill.exe') @('/PID',[string]$servicePid,'/T','/F')
    } finally { $process.Dispose() }
}
function Stop-ManagedService([string]$Name,[double]$GraceSeconds=100,[double]$ForceSeconds=20) {
    $null=Get-ManagedStopRecordPath $Name
    $deadline=(Get-ServiceStopTime)+$GraceSeconds
    $nextRequest=0.0; $lastError=''; $lastState=''
    while ($true) {
        $state=Get-ManagedServiceState $Name
        if ($state -ne $lastState) { Write-ServiceControlLog 'stop_state' $Name @{state=$state}; $lastState=$state }
        if ($state -in @('Stopped','Missing')) { return }
        $now=Get-ServiceStopTime
        if ($now -ge $deadline) { break }
        if ($state -notin @('StartPending','StopPending','ContinuePending','PausePending') -and $now -ge $nextRequest) {
            try { Send-ManagedStop $Name }
            catch {
                $lastError=$_.Exception.ToString()
                Write-ServiceControlLog 'stop_request_failed' $Name @{state=$state;error=$lastError}
            }
            $nextRequest=$now+5
        }
        Start-Sleep -Milliseconds 500
    }
    Write-ServiceControlLog 'graceful_stop_timeout' $Name @{state=$lastState;seconds=$GraceSeconds;last_error=$lastError}
    try { Stop-ManagedServiceProcess $Name }
    catch {
        # The process may have exited normally between our last check and kill.
        if ((Get-ManagedServiceState $Name) -notin @('Stopped','Missing')) {
            throw ('Failed to stop OSS service '+$Name+'; state='+$lastState+'; force-stop: '+$_.Exception.Message+'; last control error: '+$lastError)
        }
    }
    $deadline=(Get-ServiceStopTime)+$ForceSeconds
    do {
        $state=Get-ManagedServiceState $Name
        if ($state -in @('Stopped','Missing')) { Write-ServiceControlLog 'stop_verified' $Name @{state=$state}; return }
        Start-Sleep -Milliseconds 500
    } while ((Get-ServiceStopTime) -lt $deadline)
    throw ('OSS service '+$Name+' did not reach Stopped; last state='+$state+'. See logs\service-control.jsonl.')
}
