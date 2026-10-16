# Executes the REAL Upgrade/Uninstall/Setup scripts on temporary files. Windows
# services/firewall/task scheduler/COM are mocked; production paths are inaccessible.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
# Real Get-Service loads this assembly; the mocked Get-Service does not.
Add-Type -AssemblyName System.ServiceProcess
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('OllamaUpgrade-' + [guid]::NewGuid().ToString('N'))
$originalProgramFiles=$env:ProgramFiles
$originalProgramData=$env:ProgramData
New-Item -ItemType Directory -Path $fixture | Out-Null
function Assert([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message } }
function Set-Progress([string]$Message,[bool]$Active=$true) { $script:Events.Add($Message) }
function Write-SetupPhase([string]$Message) { $script:Events.Add($Message) }
function Complete-Setup([bool]$Success,[string]$Message) { $script:Results.Add($Success) }
function Test-PackageIntegrity { if ($script:Scenario -eq 'bad-package') { throw 'fixture bad package' }; return 1 }
function Invoke-Native([string]$File,[string[]]$Arguments) {
    if ($File -like '*python.exe') { return }
    $name=[IO.Path]::GetFileNameWithoutExtension($File)
    $script:Events.Add($Arguments[0]+':'+$name)
    if ($Arguments[0] -eq 'uninstall') {
        if ($script:Scenario -eq 'uninstall-failure') { throw 'fixture uninstall failure' }
        $script:Services.Remove($name)
    } elseif ($Arguments[0] -eq 'install') {
        if ($script:Scenario -eq 'install-failure' -and $name -eq $script:GatewayName) { throw 'fixture install failure' }
        $script:Services[$name]='Stopped'
    }
}
function Get-Service([string[]]$Name) {
    foreach ($serviceName in $Name) {
        if ($script:Services.ContainsKey($serviceName)) {
            $obj=[pscustomobject]@{Name=$serviceName;Status=$script:Services[$serviceName]}
            $obj | Add-Member ScriptMethod Dispose { }
            $obj | Add-Member ScriptMethod WaitForStatus { param($status,$timeout) }
            $obj
        }
    }
}
function Stop-Service([string]$Name,[switch]$Force) { $script:Services[$Name]='Stopped' }
function Start-Service([string]$Name) { $script:Services[$Name]='Running' }
function Get-CimInstance([string]$ClassName,[string]$Filter) {
    if ($ClassName -eq 'Win32_Service') {
        $name=$Filter.Split("'")[1]
        if ($script:Services.ContainsKey($name)) { [pscustomobject]@{PathName='"'+(Join-Path $script:InstallRoot ($name+'.exe'))+'"'} }
    }
}
function Get-NetTCPConnection([int]$LocalPort) {
    if ($script:Scenario -eq 'proxy-port-conflict' -and $LocalPort -eq 11436) { [pscustomobject]@{OwningProcess=99999} }
}
function Start-Sleep { }
function Get-NetFirewallRule { if ($script:Firewall) { $script:Firewall } }
function Get-NetFirewallAddressFilter { $script:Firewall }
function Remove-NetFirewallRule { $script:Firewall=$null }
function New-NetFirewallRule([string]$Name,[string[]]$RemoteAddress,[string]$Profile,[string[]]$LocalAddress=@('Any'),[string]$Enabled='True',
    [string]$Direction,[string]$Action,[string]$Protocol,[int]$LocalPort,[string]$Program) {
    $script:Firewall=[pscustomobject]@{Name=$Name;RemoteAddress=$RemoteAddress;LocalAddress=$LocalAddress;Profile=$Profile;Enabled=$Enabled;
        Direction=$Direction;Action=$Action;Protocol=$Protocol;LocalPort=$LocalPort;Program=$Program}
}
function Set-NetFirewallRule([string]$Name,[string[]]$RemoteAddress,[string[]]$LocalAddress,[string]$Profile,[string]$Enabled) {
    $script:Firewall=[pscustomobject]@{Name=$Name;RemoteAddress=$RemoteAddress;LocalAddress=$LocalAddress;Profile=$Profile;Enabled=$Enabled}
}
function Stop-ScheduledTask { }
function Unregister-ScheduledTask { }
function New-ScheduledTaskAction { [pscustomobject]@{} }
function New-ScheduledTaskTrigger { [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { [pscustomobject]@{} }
function Register-ScheduledTask { }
function Invoke-RestMethod([string]$Uri) {
    $script:ProbeUrls.Add($Uri)
    if ($script:Scenario -eq 'proxy-health-failure' -and $Uri -like '*:11436/*') { throw 'fixture proxy endpoint unavailable' }
    [pscustomobject]@{ok=$true;request_origin='remote';service='OllamaStatusService';state=$(if ($script:Scenario -eq 'busy') {'busy'} else {'idle'});ollama_online=$true;version='1.2.4';scheduling_policy='local_first_fifo_per_origin'} }
function Start-Transcript { }
function Stop-Transcript { }
function Test-Path([string]$LiteralPath,[string]$PathType) {
    if ($LiteralPath -like 'Registry::*') { return $false }
    if (-not $LiteralPath.StartsWith($fixture,[StringComparison]::OrdinalIgnoreCase)) { return $false }
    if ($PathType) { Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath -PathType $PathType }
    else { Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath }
}
function New-Object([string]$TypeName,[string]$ComObject) {
    if ($ComObject) {
        $shell=[pscustomobject]@{}
        $shell | Add-Member ScriptMethod CreateShortcut {
            param($path)
            $link=[pscustomobject]@{TargetPath='';WorkingDirectory='';Description=''}
            $link | Add-Member ScriptMethod Save { }
            return $link
        }
        return $shell
    }
    Microsoft.PowerShell.Utility\New-Object -TypeName $TypeName
}
try {
    $successfulCases=@('success','legacy-local-subnet','service-model-overrides-record','custom-proxy')
    foreach ($case in @('success','legacy-local-subnet','service-model-overrides-record','busy','bad-package','uninstall-failure','install-failure','proxy-port-conflict','proxy-health-failure','custom-proxy')) {
        $script:Scenario=$case
        $script:ProbeUrls=[Collections.Generic.List[string]]::new()
        $caseRoot=Join-Path $fixture $case
        $env:ProgramFiles=Join-Path $caseRoot 'Program Files'
        $env:ProgramData=Join-Path $caseRoot 'Program Data'
        # Load the real common routines, then replace only OS-specific operations.
        . (Join-Path $Root 'scripts\Common.ps1')
        function Grant-Directory { }
        function Restore-OriginalSettings { }
        function Test-PackageIntegrity { if ($script:Scenario -eq 'bad-package') { throw 'fixture bad package' }; return 1 }
        function Invoke-Native([string]$File,[string[]]$Arguments) {
            if ($File -like '*python.exe') { return }
            $name=[IO.Path]::GetFileNameWithoutExtension($File)
            $script:Events.Add($Arguments[0]+':'+$name)
            if ($Arguments[0] -eq 'uninstall') {
                if ($script:Scenario -eq 'uninstall-failure') { throw 'fixture uninstall failure' }
                $script:Services.Remove($name)
            } elseif ($Arguments[0] -eq 'install') {
                if ($script:Scenario -eq 'install-failure' -and $name -eq $script:GatewayName) { throw 'fixture install failure' }
                $script:Services[$name]='Stopped'
            }
        }
        $script:Services=@{OllamaStatusBackend='Running';OllamaStatusGateway='Running'}
        $script:Firewall=[pscustomobject]@{Name=$script:FirewallName;RemoteAddress=@('192.0.2.0/24','192.168.2.0/24');LocalAddress=@('Any');Profile='Domain, Private';Enabled='False'}
        if ($case -eq 'legacy-local-subnet') { $script:Firewall.RemoteAddress=@('LocalSubnet') }
        $previousRemoteAddress=@($script:Firewall.RemoteAddress)
        $script:Events=[Collections.Generic.List[string]]::new()
        $script:Results=[Collections.Generic.List[bool]]::new()
        $packageRoot=Join-Path $caseRoot '新版 包 & 测试'
        $sourceFolder=Join-Path $caseRoot '原 Ollama'
        $models=Join-Path $caseRoot '模型'
        New-Item -ItemType Directory -Path $script:InstallRoot,$script:StateRoot,$sourceFolder,$models,(Join-Path $script:StateRoot 'data'),(Join-Path $packageRoot 'scripts') -Force | Out-Null
        foreach ($name in @('Setup.ps1','Uninstall.ps1','Upgrade.ps1')) { Copy-Item -LiteralPath (Join-Path $Root ('scripts\'+$name)) -Destination (Join-Path $packageRoot 'scripts') }
        foreach ($entry in @('app','runtime','vendor','src','licenses','bin','examples')) {
            New-Item -ItemType Directory -Path (Join-Path $packageRoot $entry) -Force | Out-Null
            [IO.File]::WriteAllText((Join-Path $packageRoot ($entry+'\new.txt')),'new')
        }
        foreach ($file in @('OllamaTray.exe','部署说明.html','部署说明.txt','AI接入说明.txt','config.example.json','requirements.lock.txt','bin\WinSW.exe','runtime\python.exe')) {
            [IO.File]::WriteAllText((Join-Path $packageRoot $file),'new')
        }
        foreach ($name in @($script:BackendName,$script:GatewayName)) { [IO.File]::WriteAllText((Join-Path $script:InstallRoot ($name+'.exe')),'old') }
        [IO.File]::WriteAllText((Join-Path $script:InstallRoot ($script:BackendName+'.xml')),'<service><env name="OLLAMA_CONTEXT_LENGTH" value="32768"/><env name="CUDA_VISIBLE_DEVICES" value="0"/><env name="OLLAMA_HOST" value="127.0.0.1:11435"/></service>')
        $sourceExe=Join-Path $sourceFolder 'ollama.exe'
        [IO.File]::WriteAllText($sourceExe,'original-ollama')
        [IO.File]::WriteAllText((Join-Path $models 'model.bin'),'original-model')
        if ($case -eq 'service-model-overrides-record') {
            $activeModels=Join-Path $caseRoot '服务实际使用的模型'
            New-Item -ItemType Directory -Path $activeModels | Out-Null
            [IO.File]::WriteAllText((Join-Path $activeModels 'model.bin'),'original-model')
            $xmlPath=Join-Path $script:InstallRoot ($script:BackendName+'.xml')
            $xmlText=[IO.File]::ReadAllText($xmlPath).Replace('</service>',('<env name="OLLAMA_MODELS" value="'+[Security.SecurityElement]::Escape($activeModels)+'"/></service>'))
            [IO.File]::WriteAllText($xmlPath,$xmlText,[Text.UTF8Encoding]::new($true))
        }
        [IO.File]::WriteAllText((Join-Path $script:StateRoot 'data\history.txt'),'original-history')
        $configPath=Join-Path $script:StateRoot 'config.json'
        Save-Json @{data_dir=(Join-Path $script:StateRoot 'data');queue_timeout_seconds=71;listen_host='0.0.0.0'} $configPath
        if ($case -eq 'custom-proxy') { Save-Json @{data_dir=(Join-Path $script:StateRoot 'data');proxy_listen_host='::1';proxy_listen_port=19136} $configPath }
        $configHash=(Get-FileHash -LiteralPath $configPath).Hash
        Save-Json @{version='1.2.1';source_exe=$sourceExe;models=$models;caller_profile=$caseRoot;caller_sid='S-1-0-0'} $script:RegistrationPath
        $context=[pscustomobject]@{SourceExe='';Models='';CallerProfile='';CallerSid=''}
        $BootstrapLog=Join-Path $caseRoot 'bootstrap.log'
        $failed=$false; $errorMessage=''
        try { . (Join-Path $packageRoot 'scripts\Upgrade.ps1') } catch { $failed=$true; $errorMessage=$_.Exception.Message }
        Assert ($failed -eq ($case -notin $successfulCases)) ("Unexpected outcome for $case; $errorMessage`r`n" + ($script:Events -join "`r`n"))
        Assert ((Get-FileHash -LiteralPath $configPath).Hash -eq $configHash) "Config changed: $case"
        Assert ([IO.File]::ReadAllText((Join-Path $models 'model.bin')) -eq 'original-model') "Model changed: $case"
        Assert ([IO.File]::ReadAllText((Join-Path $script:StateRoot 'data\history.txt')) -eq 'original-history') "History changed: $case"
        if ($case -in $successfulCases) {
            $expectedProxyUrl=if ($case -eq 'custom-proxy') {'http://[::1]:19136/healthz'} else {'http://127.0.0.1:11436/healthz'}
            Assert ($script:ProbeUrls.Contains($expectedProxyUrl)) 'Proxy listener readiness was not checked'
            Assert ($script:Results.Count -eq 1 -and $script:Results[0]) 'Intermediate uninstall completion escaped to GUI'
            Assert ($script:Events.IndexOf('uninstall:OllamaStatusBackend') -lt $script:Events.IndexOf('install:OllamaStatusBackend')) 'Install preceded uninstall'
            Assert ($script:Firewall.RemoteAddress.Count -eq 1 -and $script:Firewall.RemoteAddress[0] -eq 'Any') 'Upgrade did not allow all source addresses'
            Assert ($script:Firewall.LocalAddress.Count -eq 1 -and $script:Firewall.LocalAddress[0] -eq 'Any') 'Upgrade did not allow both local address families'
            Assert ($script:Firewall.Enabled -eq 'True' -and $script:Firewall.Profile -eq 'Any') 'Upgrade did not enable all network profiles'
            Assert ($script:Firewall.Direction -eq 'Inbound' -and $script:Firewall.Action -eq 'Allow' -and $script:Firewall.Protocol -eq 'TCP' -and $script:Firewall.LocalPort -eq 11434) 'Firewall rule has incorrect direction/protocol/port'
            Assert ($script:Firewall.Program -eq (Join-Path $script:InstallRoot 'runtime\python.exe')) 'Firewall rule lost its application scope'
            $savedFirewall=(Get-Content -LiteralPath (Join-Path $upgradeBackup 'upgrade-settings.json') -Raw | ConvertFrom-Json).Firewall
            Assert (-not (Compare-Object $savedFirewall.RemoteAddress $previousRemoteAddress) -and $savedFirewall.Enabled -eq 'False') 'Previous firewall policy was not backed up'
            [xml]$backend=Get-Content -LiteralPath (Join-Path $script:InstallRoot ($script:BackendName+'.xml')) -Raw
            Assert (($backend.service.env | Where-Object name -eq 'OLLAMA_CONTEXT_LENGTH').value -eq '32768') 'Context tuning lost'
            Assert (($backend.service.env | Where-Object name -eq 'CUDA_VISIBLE_DEVICES').value -eq '0') 'GPU tuning lost'
            Assert ((Get-Content -LiteralPath $script:RegistrationPath -Raw | ConvertFrom-Json).version -eq '1.2.4') 'Wrong installed version'
            if ($case -eq 'service-model-overrides-record') {
                Assert ((Get-Content -LiteralPath $script:RegistrationPath -Raw | ConvertFrom-Json).models -eq $activeModels) 'Reinstall used stale model path'
                Assert (($backend.service.env | Where-Object name -eq 'OLLAMA_MODELS').value -eq $activeModels) 'Backend model path was not preserved'
            }
        } elseif ($case -in @('busy','bad-package','uninstall-failure','proxy-port-conflict')) {
            Assert (@($script:Events | Where-Object { $_ -like 'install:*' }).Count -eq 0) 'Installation ran after preflight/uninstall failure'
        } elseif ($case -in @('install-failure','proxy-health-failure')) {
            Assert ($script:Results.Count -eq 0) 'Failed upgrade was reported successful'
            Assert ($errorMessage -like '*不会自动回装*') 'Missing honest failure guidance'
        }
        if ($case -eq 'proxy-port-conflict') { Assert (@($script:Events | Where-Object { $_ -like 'uninstall:*' }).Count -eq 0) 'Uninstalled before checking proxy port conflict' }
        Write-Output ('PASS reinstall upgrade: ' + $case)
    }
} finally {
    $env:ProgramFiles=$originalProgramFiles
    $env:ProgramData=$originalProgramData
    $resolved=[IO.Path]::GetFullPath($fixture)
    $tempBase=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $resolved.StartsWith($tempBase,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($resolved).StartsWith('OllamaUpgrade-')) { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
