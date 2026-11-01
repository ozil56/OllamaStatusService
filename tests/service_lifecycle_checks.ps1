# Isolated service/SCM/process fixtures; never contacts production services.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
. (Join-Path $Root 'scripts\Common.ps1')
$realForce=(Get-Item Function:Stop-ManagedServiceProcess).ScriptBlock
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('oss-service-lifecycle-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$script:StateRoot=Join-Path $fixture 'state'; $script:InstallRoot=Join-Path $fixture 'installed'
New-Item -ItemType Directory -Path (Join-Path $script:StateRoot 'updater'),$script:InstallRoot -Force | Out-Null
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Get-ServiceStopTime { return $script:clock }
function Start-Sleep { param($Milliseconds); $script:clock+=0.5 }
function Write-ServiceControlLog { param($Event,$Name,$Details); $script:events.Add($Event); if($Details -and $Details.ContainsKey('error')){$script:lastLoggedError=$Details.error} }
function Get-ManagedServiceState {
    param($Name)
    $script:reads++
    switch ($script:scenario) {
        'missing' { return 'Missing' }
        'already-stopped' { return 'Stopped' }
        'start-pending' { if ($script:clock -lt 2) { return 'StartPending' } }
        'stop-pending' { if ($script:clock -lt 2) { return 'StopPending' } else { return 'Stopped' } }
        'error-then-stop' { if ($script:requests) { return 'Stopped' } }
    }
    return $script:state
}
function Send-ManagedStop {
    param($Name)
    $script:requests++
    $script:events.Add('request')
    if ($script:scenario -eq 'retry' -and $script:requests -lt 3) { throw [ComponentModel.Win32Exception]::new(1061) }
    if ($script:scenario -eq 'error-then-stop') { throw [ComponentModel.Win32Exception]::new(109) }
    if ($script:scenario -in @('hung','force-error','force-not-stopped','force-race-exit')) { throw 'fixture control timeout' }
    $script:state='Stopped'
}
function Stop-ManagedServiceProcess {
    param($Name)
    $script:events.Add('force')
    if ($script:scenario -eq 'force-error') { throw 'fixture access denied' }
    if ($script:scenario -eq 'force-not-stopped') { return }
    $script:state='Stopped'
    if ($script:scenario -eq 'force-race-exit') { throw 'process already exited' }
}
try {
    foreach ($case in @('normal','missing','already-stopped','start-pending','stop-pending','retry','error-then-stop','hung','force-error','force-not-stopped','force-race-exit')) {
        $script:scenario=$case; $script:clock=0.0; $script:state='Running'; $script:requests=0; $script:reads=0
        $script:events=New-Object 'System.Collections.Generic.List[string]'; $script:lastLoggedError=''
        $caught=''
        try { Stop-ManagedService $script:GatewayName -GraceSeconds 15 -ForceSeconds 3 } catch { $caught=$_.Exception.Message }
        if ($case -in @('force-error','force-not-stopped')) {
            Assert ([bool]$caught) ('False successful stop: '+$case)
        } else { Assert (-not $caught) ('Unexpected failure: '+$caught) }
        if ($case -in @('missing','already-stopped','stop-pending')) { Assert ($script:requests -eq 0) ('Redundant control request: '+$case) }
        if ($case -eq 'retry') { Assert ($script:requests -eq 3 -and $script:lastLoggedError) 'Control retry or diagnostic lost' }
        if ($case -eq 'start-pending') { Assert ($script:clock -ge 2) 'Did not wait for StartPending' }
        if ($case -notin @('hung','force-error','force-not-stopped','force-race-exit')) { Assert (-not $script:events.Contains('force')) ('Premature force: '+$case) }
        else { Assert ($script:events.Contains('force') -and $script:clock -ge 15) ('Missing bounded fallback: '+$case) }
        Write-Output ('PASS stop sequence '+$case)
    }

    # Exercise actual process ownership, restart suppression and restoration.
    function Test-Administrator { return $script:admin }
    function Get-ManagedServiceRegistration { param($Name); return $script:registration }
    function Get-Process { param($Id); return $script:process }
    function Get-CimInstance { param($ClassName,$Filter); return $script:owners }
    function Initialize-OllamaUpdateDirectory {}
    function Get-ItemProperty { return [pscustomobject]@{DelayedAutoStart=1} }
    function Set-ItemProperty { param($LiteralPath,$Name,$Value); $script:restoredDelay=$Value }
    function Set-Service {
        param($Name,$StartupType)
        $script:events.Add('mode:'+$StartupType)
        $script:registration.StartMode=if($StartupType -eq 'Automatic'){'Auto'}else{$StartupType}
        if ($script:scenario -eq 'pid-race' -and $StartupType -eq 'Disabled') { $script:registration.ProcessId=60002 }
    }
    function Invoke-Native {
        param($File,$Arguments)
        $script:events.Add('kill')
        Assert ($script:registration.StartMode -eq 'Disabled') 'Killed before suppressing restart'
        Assert (Test-Path -LiteralPath (Get-ManagedStopRecordPath $script:GatewayName)) 'Killed before durable mode save'
        Assert (($Arguments -join ',') -eq '/PID,60001,/T,/F') 'Incorrect process-tree kill scope'
        Assert ([IO.Path]::GetFileName($File) -eq 'taskkill.exe') 'Wrong force-stop executable'
        $script:registration.State='Stopped'
    }
    foreach ($case in @('owned','not-admin','wrong-registration','wrong-process','shared-service','pid-race','already-exited','updater-mode','repeat-force')) {
        $script:scenario=$case; $script:admin=($case -ne 'not-admin'); $script:events.Clear(); $script:restoredDelay=-1
        Remove-ManagedStopRecord $script:GatewayName
        $expected=Join-Path $script:InstallRoot ($script:GatewayName+'.exe')
        $script:registration=[pscustomobject]@{Name=$script:GatewayName;PathName='"'+$expected+'"';ServiceType='Own Process';ProcessId=60001;State='Running';StartMode='Auto'}
        $script:process=[pscustomobject]@{Handle=123;HasExited=($case -eq 'already-exited');MainModule=[pscustomobject]@{FileName=$expected}}
        $script:process | Add-Member ScriptMethod Dispose {}
        $script:owners=@([pscustomobject]@{Name=$script:GatewayName})
        if ($case -eq 'wrong-registration') { $script:registration.PathName='C:\unrelated.exe' }
        if ($case -eq 'wrong-process') { $script:process.MainModule.FileName='C:\unrelated.exe' }
        if ($case -eq 'shared-service') { $script:owners+= [pscustomobject]@{Name='UnrelatedService'} }
        $caught=''
        try { & $realForce $script:GatewayName } catch { $caught=$_.Exception.Message }
        if ($case -in @('not-admin','wrong-registration','wrong-process','shared-service','pid-race')) {
            Assert ($caught -and -not $script:events.Contains('kill')) ('Unsafe kill or missing refusal: '+$case)
        } elseif ($case -eq 'already-exited') { Assert (-not $caught -and -not $script:events.Contains('kill')) 'Exited process should be a no-op' }
        else {
            Assert (-not $caught -and $script:events.Contains('kill')) ('Owned force failed: '+$case+': '+$caught)
            if ($case -eq 'repeat-force') {
                $script:registration.State='Running'
                & $realForce $script:GatewayName
                Assert ((Read-ManagedStopRecord $script:GatewayName).start_mode -eq 'Auto') 'Retry overwrote original start mode'
            }
            if ($case -eq 'updater-mode') { $script:registration.StartMode='Manual' }
            Restore-ManagedServiceStartMode $script:GatewayName
            Assert ($script:registration.StartMode -eq $(if($case -eq 'updater-mode'){'Manual'}else{'Auto'})) 'Start mode restored incorrectly'
            Assert (-not (Test-Path -LiteralPath (Get-ManagedStopRecordPath $script:GatewayName))) 'Restoration record leaked'
            if ($case -ne 'updater-mode') { Assert ($script:restoredDelay -eq 1) 'Delayed autostart lost' }
        }
        Write-Output ('PASS process ownership '+$case)
    }
    Write-Output 'PASS 20 service lifecycle scenarios; no real service or process was stopped.'
} finally {
    $resolved=[IO.Path]::GetFullPath($fixture)
    $base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $resolved.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($resolved).StartsWith('oss-service-lifecycle-')) { throw 'Unsafe cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
