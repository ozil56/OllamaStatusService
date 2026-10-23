# OS boundaries are replaced by fixtures. NEVER updates real services or Ollama.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
. (Join-Path $Root 'scripts\Common.ps1')
. (Join-Path $Root 'scripts\OllamaUpdateCore.ps1')
$script:OllamaUpdateLog=$null
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Expect-Failure([scriptblock]$Action,[string]$Pattern) {
    $caught=$null
    try { & $Action } catch { $caught=$_.Exception.Message }
    Assert ($caught -and $caught -match $Pattern) ('Expected failure '+$Pattern+', got: '+$caught)
}

# Semantic versions, HTTPS/repository binding, checksum and signature rejection.
Assert ((ConvertTo-OllamaVersion '0.10.0') -gt (ConvertTo-OllamaVersion '0.9.9')) 'Numeric version comparison'
Expect-Failure { ConvertTo-OllamaVersion 'latest' } '无法识别'
& {
    $script:releaseFixture=[pscustomobject]@{draft=$false;prerelease=$false;tag_name='v1.2.3';assets=@([pscustomobject]@{
        name='OllamaSetup.exe';browser_download_url='https://github.com/ollama/ollama/releases/download/v1.2.3/OllamaSetup.exe';digest=('sha256:'+('a'*64));size=10})}
    function Invoke-RestMethod { return $script:releaseFixture }
    $release=Get-OllamaRelease '1.2.3'
    Assert ($release.sha256 -eq ('a'*64)) 'Release SHA256 lost'
    $script:releaseFixture.assets[0].browser_download_url='https://example.com/OllamaSetup.exe'
    Expect-Failure { Get-OllamaRelease '1.2.3' } '地址'
    $script:releaseFixture.prerelease=$true
    Expect-Failure { Get-OllamaRelease '1.2.3' } '稳定'
}
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('oss-update-tests-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    & {
        $file=Join-Path $temporary 'fake.exe'; [IO.File]::WriteAllText($file,'fake')
        $release=[pscustomobject]@{size=4;sha256=('0'*64)}
        Expect-Failure { Test-OllamaInstaller $file $release } 'SHA256'
        $release.sha256=(Get-FileHash -LiteralPath $file).Hash
        Expect-Failure { Test-OllamaInstaller $file $release } '签名'
    }
    $cases=@('success','busy','race','unknown','uploading','owned','bad-signature','install-failed','restore-failed','reboot','stop-failed','other-process')
    foreach ($case in $cases) {
        & {
            param($Scenario)
            $script:StateRoot=Join-Path $temporary $Scenario
            $script:InstallRoot=Join-Path $script:StateRoot 'program'
            New-Item -ItemType Directory -Path $script:InstallRoot,(Join-Path $script:StateRoot 'updater') -Force | Out-Null
            $script:RegistrationPath=Join-Path $script:StateRoot 'installation.json'
            $files=@($script:RegistrationPath,(Join-Path $script:StateRoot 'config.json'),(Join-Path $script:InstallRoot 'OllamaStatusBackend.xml'),(Join-Path $script:InstallRoot 'OllamaStatusGateway.xml'))
            $files += (Join-Path $script:InstallRoot 'download-network.json')
            foreach($file in $files) { [IO.File]::WriteAllText($file,'preserve-this') }
            $script:events=New-Object 'System.Collections.Generic.List[string]'
            $script:checks=0
            $script:ctx=[pscustomobject]@{source=(Join-Path $script:StateRoot 'ollama.exe');source_dir=$script:StateRoot;gateway='http://fixture';backend='http://fixture-backend';config_path=$files[1];backend_path=$files[2]}
            function Get-OllamaUpdateContext { return $script:ctx }
            function Write-OllamaUpdateStatus { param($Phase,$Message,$Active=$true,$Success=$false); $script:events.Add('phase:'+$Phase) }
            function Invoke-OllamaUpdateApi {
                param($Url)
                if ($Url.EndsWith('/api/version')) { return [pscustomobject]@{version='1.0.0'} }
                $script:checks++
                $busy=($Scenario -eq 'busy' -or ($Scenario -eq 'race' -and $script:checks -gt 1))
                return [pscustomobject]@{service='OllamaStatusService';state=$(if($busy){'busy'}else{'idle'});ollama_online=$true;
                    inflight_requests=0;queued_requests=0;uncertain_requests=$(if($Scenario -eq 'unknown'){1}else{0});inflight_model_transfers=0;
                    model_jobs=[pscustomobject]@{active=$(if($Scenario -eq 'owned'){1}else{0})};accepting_requests=$false;
                    ollama_update=[pscustomobject]@{protocol=1;maintenance=$true;safe_to_stop=$true;unknown_model_jobs=0;model_workers=0;
                        inflight_http_operations=$(if($Scenario -eq 'uploading'){1}else{0})}}
            }
            function Assert-OllamaProcessOwnership { if($Scenario -eq 'other-process'){throw 'other process'} }
            function Get-OllamaRelease { return [pscustomobject]@{version='1.1.0';size=1} }
            function Get-OllamaInstaller { $script:events.Add('download'); return (Join-Path $script:StateRoot 'fake.exe') }
            function Test-OllamaInstaller { if($Scenario -eq 'bad-signature'){throw 'signature failure'} }
            function Get-OllamaServiceModes { return @([pscustomobject]@{name='OllamaStatusBackend';start=2;delayed=1}) }
            function Get-ScheduledTask { return [pscustomobject]@{Settings=[pscustomobject]@{Enabled=$true}} }
            function Get-CimInstance { return [pscustomobject]@{LastBootUpTime=[datetime]'2026-01-01'} }
            function Get-OllamaInventory { return @('model:1|digest') }
            function Disable-ScheduledTask { $script:events.Add('disable-watchdog') }
            function Stop-ScheduledTask { $script:events.Add('stop-watchdog') }
            function Set-Service { param($Name,$StartupType); $script:events.Add('disable:'+$Name) }
            function Stop-ManagedServices { $script:events.Add('stop'); if($Scenario -eq 'stop-failed'){throw 'stop failure'} }
            function Assert-BackendStopped { $script:events.Add('confirmed-stopped') }
            function Invoke-OllamaOfficialInstaller { $script:events.Add('install'); if($Scenario -eq 'reboot'){return 3010}; if($Scenario -eq 'install-failed'){return 2}; return 0 }
            function Restore-OllamaOwnership { $script:events.Add('restore'); if($Scenario -eq 'restore-failed'){throw 'restore failure'} }
            function Assert-OllamaUpdateRestored { $script:events.Add('verify'); return '1.1.0' }
            function Restore-OllamaServiceModes { $script:events.Add('modes') }
            $errorMessage=$null
            try { Invoke-OllamaManagedUpdate '1.1.0' } catch { $errorMessage=$_.Exception.Message }
            $marker=Join-Path $script:StateRoot 'updater\maintenance.lock'
            if($Scenario -eq 'success') {
                Assert (-not $errorMessage) $errorMessage
                Assert ($script:events.IndexOf('download') -lt $script:events.IndexOf('stop')) 'Download must precede stop'
                Assert ($script:events.IndexOf('confirmed-stopped') -lt $script:events.IndexOf('install')) 'Installer preceded confirmed stop'
                Assert ($script:events.Contains('verify') -and $script:events.Contains('modes')) 'Missing verification or mode restoration'
                Assert (-not (Test-Path -LiteralPath $marker)) 'Successful gate leaked'
            } else {
                Assert ([bool]$errorMessage) ('Expected failure: '+$Scenario)
                if($Scenario -in @('busy','race','unknown','uploading','owned','bad-signature','other-process')) {
                    Assert (-not $script:events.Contains('stop') -and -not $script:events.Contains('install')) ('Interrupted work: '+$Scenario)
                    Assert (-not (Test-Path -LiteralPath $marker)) ('Preflight gate leaked: '+$Scenario)
                } elseif($Scenario -in @('restore-failed','reboot')) {
                    Assert (Test-Path -LiteralPath $marker) ('Failure reopened admission: '+$Scenario)
                    if($Scenario -eq 'reboot'){Assert (-not $script:events.Contains('restore')) 'Restarted before required reboot'}
                } else {
                    Assert ($script:events.Contains('restore') -and $script:events.Contains('verify')) 'Automatic recovery missing'
                    Assert (-not (Test-Path -LiteralPath $marker)) 'Recovered gate leaked'
                }
            }
            foreach($file in $files) { Assert ([IO.File]::ReadAllText($file) -eq 'preserve-this') ('Changed protected config: '+$file) }
            Write-Output ('PASS '+$Scenario)
        } $case
    }
    # Exercise the real recovery verifier (not the orchestration mock).
    & {
        $context=[pscustomobject]@{gateway='http://gateway';backend='http://backend'}
        $transaction=[pscustomobject]@{inventory=@('original:1|digest')}
        $script:badVersion=$false; $script:badModels=$false
        function Start-Sleep {}
        function Invoke-OllamaUpdateApi {
            param($Url)
            if($Url.EndsWith('/status')){return [pscustomobject]@{service='OllamaStatusService';ollama_online=$true;ollama_update=[pscustomobject]@{maintenance=$true;safe_to_stop=$true}}}
            return [pscustomobject]@{version=$(if($script:badVersion){'1.0.0'}else{'1.1.0'})}
        }
        function Get-OllamaInventory { if($script:badModels){return @('different:1|changed')}; return @('original:1|digest') }
        Assert ((Assert-OllamaUpdateRestored $context $transaction '1.1.0' 0) -eq '1.1.0') 'Verified version lost'
        $script:badVersion=$true
        Expect-Failure { Assert-OllamaUpdateRestored $context $transaction '1.1.0' 0 } '预期'
        $script:badVersion=$false; $script:badModels=$true
        Expect-Failure { Assert-OllamaUpdateRestored $context $transaction '1.1.0' 0 } '模型'
        Write-Output 'PASS actual version and model-inventory verifier'
    }
    # Atomic progress replacement works on Windows PowerShell 5.1; old files
    # cannot leave the UI reading partial JSON.
    $script:StateRoot=Join-Path $temporary 'atomic'
    New-Item -ItemType Directory -Path (Join-Path $script:StateRoot 'updater') -Force | Out-Null
    Write-OllamaUpdateStatus 'checking' '中文检查'
    Write-OllamaUpdateStatus 'complete' '验证完成' $false $true
    $progress=Get-Content -LiteralPath (Join-Path $script:StateRoot 'updater\status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert ($progress.success -and -not $progress.active -and $progress.message -eq '验证完成') 'Atomic status was not readable'
    Write-Output 'PASS atomic progress and Chinese text'
    & {
        function Invoke-OllamaUpdateApi { return [pscustomobject]@{models=@([pscustomobject]@{name='b:2';digest='digest-b'},[pscustomobject]@{name='a:1';digest='digest-a'})} }
        $context=[pscustomobject]@{gateway='http://gateway';backend='http://backend'}
        $inventory=@(Get-OllamaInventory $context)
        $roundtrip=(@{inventory=$inventory} | ConvertTo-Json -Depth 5 | ConvertFrom-Json).inventory
        Assert ($inventory.Count -eq 2 -and ($roundtrip -join "`n") -eq ($inventory -join "`n")) 'Multi-model inventory changed after durable serialization'
        Write-Output 'PASS multi-model inventory serialization'
    }
    foreach($empty in @($true,$false)) {
        & {
            $script:StateRoot=Join-Path $temporary ('recover-'+$empty)
            New-Item -ItemType Directory -Path (Join-Path $script:StateRoot 'updater') -Force | Out-Null
            $marker=Join-Path $script:StateRoot 'updater\maintenance.lock'
            if($empty){[IO.File]::WriteAllText($marker,'')}
            else { Save-Json @{id=('a'*32);backup=(Join-Path $script:StateRoot ('updater\backups\ollama-update-'+('a'*32)));services_touched=$false} $marker }
            function Get-OllamaUpdateContext { return [pscustomobject]@{} }
            function Restore-OllamaOwnership { throw 'Recovery must not interrupt a preflight-only transaction' }
            Invoke-OllamaManagedUpdate -Recover
            Assert (-not (Test-Path -LiteralPath $marker)) 'Preflight recovery retained gate'
        }
    }
    Write-Output 'PASS interrupted preflight recovery'
} finally {
    $resolved=[IO.Path]::GetFullPath($temporary)
    $base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if(-not $resolved.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($resolved) -notlike 'oss-update-tests-*'){throw 'Unsafe fixture cleanup path'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Output 'Ollama updater checks passed. No production services, models or installers were touched.'
