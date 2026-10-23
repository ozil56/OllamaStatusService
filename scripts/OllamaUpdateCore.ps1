# Functions only: isolated tests replace OS/network boundaries without installing anything.
function ConvertTo-OllamaVersion([string]$Value) {
    if ($Value -notmatch '^v?(\d+\.\d+\.\d+)(?:[-+][0-9A-Za-z.-]+)?$') { throw ('无法识别 Ollama 版本：'+$Value) }
    return [version]$Matches[1]
}

function Test-NewOllamaVersion([string]$Latest,[string]$Current) {
    $latestVersion=ConvertTo-OllamaVersion $Latest
    $currentVersion=ConvertTo-OllamaVersion $Current
    return ($latestVersion -gt $currentVersion -or ($latestVersion -eq $currentVersion -and $Current.Contains('-')))
}

function Get-OllamaRelease([string]$TargetVersion='') {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $url='https://api.github.com/repos/ollama/ollama/releases/latest'
    if ($TargetVersion) {
        if ($TargetVersion -notmatch '^\d+\.\d+\.\d+$') { throw '更新版本格式无效。' }
        $url='https://api.github.com/repos/ollama/ollama/releases/tags/v'+$TargetVersion
    }
    $release=Invoke-RestMethod -Uri $url -Headers @{'User-Agent'='OllamaStatusService-Updater';Accept='application/vnd.github+json'} -TimeoutSec 30
    if ($release.draft -or $release.prerelease -or $release.tag_name -notmatch '^v(\d+\.\d+\.\d+)$') { throw '仅允许安装官方稳定版本。' }
    $version=$Matches[1]
    if ($TargetVersion -and $version -ne $TargetVersion) { throw '官方版本与选定版本不一致。' }
    $assets=@($release.assets | Where-Object name -eq 'OllamaSetup.exe')
    if ($assets.Count -ne 1) { throw '官方发行版未提供唯一的 Windows 安装包。' }
    $asset=$assets[0]
    $expected='https://github.com/ollama/ollama/releases/download/v'+$version+'/OllamaSetup.exe'
    if ($asset.browser_download_url -cne $expected -or $asset.digest -notmatch '^sha256:([a-fA-F0-9]{64})$' -or [long]$asset.size -le 0) {
        throw '官方安装包地址或 SHA256 校验信息无效，未启动更新。'
    }
    return [pscustomobject]@{version=$version;url=$expected;sha256=$Matches[1].ToLowerInvariant();size=[long]$asset.size}
}

function Write-OllamaUpdateStatus([string]$Phase,[string]$Message,[bool]$Active=$true,[bool]$Success=$false) {
    $sourceMessage=$Message
    $Message=Get-UiText $Message
    $value=[ordered]@{source_message=$sourceMessage;language=$script:UiLanguage;phase=$Phase;message=$Message;active=$Active;success=$Success;updated=(Get-Date).ToUniversalTime().ToString('o')}
    $path=Join-Path $script:StateRoot 'updater\status.json'
    $temporary=$path+'.'+$PID+'.tmp'
    Save-Json $value $temporary
    if (Test-Path -LiteralPath $path) { [IO.File]::Replace($temporary,$path,[System.Management.Automation.Language.NullString]::Value) }
    else { [IO.File]::Move($temporary,$path) }
    if ($script:OllamaUpdateLog) { Add-Content -LiteralPath $script:OllamaUpdateLog -Value ($value.updated+' '+$Phase+' '+$Message) -Encoding UTF8 }
}

function Save-OllamaTransaction($Transaction,[string]$Marker) {
    $temporary=$Marker+'.'+$PID+'.tmp'
    Save-Json $Transaction $temporary
    [IO.File]::Replace($temporary,$Marker,[System.Management.Automation.Language.NullString]::Value)
    Save-Json $Transaction (Join-Path $Transaction.backup 'transaction.json')
}

function Invoke-OllamaUpdateApi([string]$Url) {
    $request=[Net.HttpWebRequest]::Create($Url)
    $request.Proxy=$null; $request.Timeout=5000; $request.ReadWriteTimeout=10000
    $response=$request.GetResponse()
    try {
        $reader=New-Object IO.StreamReader($response.GetResponseStream(),[Text.Encoding]::UTF8)
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}

function Get-OllamaUpdateContext {
    $registration=Get-Content -LiteralPath $script:RegistrationPath -Encoding UTF8 -Raw | ConvertFrom-Json
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($sid -ne $registration.caller_sid) { throw '请使用原安装 Ollama 的 Windows 账户升级；不能用其他管理员账户代替，以免更新到另一个用户目录。' }
    $configPath=Join-Path $script:StateRoot 'config.json'
    $config=Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw | ConvertFrom-Json
    $backendPath=Join-Path $script:InstallRoot ($script:BackendName+'.xml')
    [xml]$backend=Get-Content -LiteralPath $backendPath -Encoding UTF8 -Raw
    $source=[IO.Path]::GetFullPath([string]$backend.service.executable)
    $sourceDir=Split-Path -Parent $source
    $modelNode=$backend.SelectSingleNode('/service/env[@name="OLLAMA_MODELS"]/@value')
    if (-not $modelNode -or $source -ne $registration.source_exe -or [IO.Path]::GetFileName($source) -ne 'ollama.exe' -or
        -not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Ollama 原程序路径与服务注册不一致，未修改安装。' }
    $models=[Environment]::ExpandEnvironmentVariables($modelNode.Value)
    if ($models -notmatch '^[A-Za-z]:[\\/]' -or $models -match '%') { throw '模型目录必须为已解析的本机绝对路径。' }
    $models=[IO.Path]::GetFullPath($models)
    if ($models -eq $sourceDir -or $models.StartsWith($sourceDir+'\',[StringComparison]::OrdinalIgnoreCase)) {
        throw '模型位于 Ollama 程序目录内，官方安装器可能覆盖该位置，已停止升级。请先迁移模型。'
    }
    if (-not (Test-Path -LiteralPath $models -PathType Container)) { throw '原模型目录不存在。' }
    foreach ($name in @($script:BackendName,$script:GatewayName)) {
        $service=Get-CimInstance Win32_Service -Filter ("Name='"+$name+"'")
        if (-not $service -or $service.PathName.Trim('"') -ne (Join-Path $script:InstallRoot ($name+'.exe'))) { throw ('服务路径无效：'+$name) }
    }
    $bind=[string]$config.listen_host
    if ($bind -in @('0.0.0.0','::','localhost','')) { $bind='127.0.0.1' }
    if ($bind.Contains(':')) { $bind='['+$bind.Trim('[',']')+']' }
    $gateway='http://'+$bind+':'+$config.listen_port
    return [pscustomobject]@{registration=$registration;config=$config;source=$source;source_dir=$sourceDir;models=$models;
        gateway=$gateway;backend=([string]$config.backend_url).TrimEnd('/');config_path=$configPath;backend_path=$backendPath}
}

function Assert-OllamaUpdateIdle($Context,[bool]$Gated=$false) {
    $status=Invoke-OllamaUpdateApi ($Context.gateway+'/status')
    if ($status.service -ne 'OllamaStatusService' -or -not $status.PSObject.Properties['ollama_update'] -or $status.ollama_update.protocol -ne 1) {
        throw '当前后台尚未加载安全升级功能，请先安装新版 OSS；已有任务不会被停止。'
    }
    if (-not $status.ollama_online -or $status.state -notin @('idle','maintenance') -or
        $status.inflight_requests -ne 0 -or $status.queued_requests -ne 0 -or $status.uncertain_requests -ne 0 -or
        $status.inflight_model_transfers -ne 0 -or $status.model_jobs.active -ne 0 -or
        $status.ollama_update.inflight_http_operations -ne 0 -or $status.ollama_update.unknown_model_jobs -ne 0 -or
        $status.ollama_update.model_workers -ne 0) {
        throw '存在执行中、排队、下载/上传或结果未知的任务，或后台尚未就绪。本次不升级、不停止服务，请任务结束后重试。'
    }
    if ($Gated -and (-not $status.ollama_update.maintenance -or -not $status.ollama_update.safe_to_stop -or $status.accepting_requests)) {
        throw '新任务入口尚未可靠关闭，未停止服务。'
    }
}

function Get-OllamaInventory($Context) {
    $backend=Invoke-OllamaUpdateApi ($Context.backend+'/api/tags')
    $gateway=Invoke-OllamaUpdateApi ($Context.gateway+'/api/tags')
    if (-not $backend.PSObject.Properties['models'] -or -not $gateway.PSObject.Properties['models']) { throw '模型列表无效。' }
    $a=@($backend.models | ForEach-Object { [string]$_.name+'|'+[string]$_.digest } | Sort-Object)
    $b=@($gateway.models | ForEach-Object { [string]$_.name+'|'+[string]$_.digest } | Sort-Object)
    if (($a -join "`n") -cne ($b -join "`n")) { throw '网关与后端的模型列表不一致。' }
    return $a
}

function Test-OllamaInstaller([string]$Path,$Release) {
    if ((Get-Item -LiteralPath $Path).Length -ne $Release.size) { throw '安装包大小校验失败。' }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $Release.sha256) { throw '安装包 SHA256 校验失败。' }
    $signature=Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
        $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false) -ne 'Ollama Inc.') {
        throw '安装包不是具有有效签名的 Ollama Inc. 官方程序。'
    }
}

function Get-OllamaInstaller($Release) {
    $directory=Join-Path $script:StateRoot ('updater\downloads\'+$Release.version)
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $path=Join-Path $directory 'OllamaSetup.exe'
    if (Test-Path -LiteralPath $path) {
        try { Test-OllamaInstaller $path $Release; return $path } catch { Remove-Item -LiteralPath $path -Force }
    }
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($directory))
    if ($drive.AvailableFreeSpace -lt ($Release.size*3+1GB)) { throw '更新缓存磁盘空间不足，请至少保留安装包大小三倍加 1 GB 的空间。' }
    Write-OllamaUpdateStatus 'downloading' ('正在下载官方 Ollama '+$Release.version+'；服务继续运行。')
    $request=[Net.HttpWebRequest]::Create($Release.url)
    $request.UserAgent='OllamaStatusService-Updater'; $request.Timeout=30000; $request.ReadWriteTimeout=60000
    $response=$request.GetResponse()
    $part=$path+'.part'
    try {
        if ($response.ResponseUri.Scheme -ne 'https' -or ($response.ResponseUri.Host -ne 'github.com' -and
            -not $response.ResponseUri.Host.EndsWith('.githubusercontent.com',[StringComparison]::OrdinalIgnoreCase))) { throw '安装包被重定向到非官方地址。' }
        $input=$response.GetResponseStream()
        $output=[IO.File]::Open($part,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {
            $buffer=New-Object byte[] (1MB); $total=0L; $clock=[Diagnostics.Stopwatch]::StartNew()
            while (($count=$input.Read($buffer,0,$buffer.Length)) -gt 0) {
                $total+=$count
                if ($total -gt $Release.size) { throw '下载内容超过官方公布的大小。' }
                $output.Write($buffer,0,$count)
                if ($clock.Elapsed.TotalSeconds -ge 1) {
                    Write-OllamaUpdateStatus 'downloading' ('正在下载 Ollama {0}：{1:N1}%（{2:N0} / {3:N0} MB），服务继续运行。' -f $Release.version,($total*100.0/$Release.size),($total/1MB),($Release.size/1MB))
                    $clock.Restart()
                }
            }
        } finally { $output.Dispose(); $input.Dispose() }
    } finally { $response.Dispose() }
    Write-OllamaUpdateStatus 'checking' '正在校验安装包 SHA256 与官方数字签名…'
    Test-OllamaInstaller $part $Release
    Move-Item -LiteralPath $part -Destination $path -Force
    return $path
}

function Get-OllamaSourceProcesses($Context) {
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Context.source_dir+'\',[StringComparison]::OrdinalIgnoreCase) -and
        $_.Name -match '^(ollama(?:[ ._-].*)?|llama-server)\.exe$'
    })
}

function Assert-OllamaProcessOwnership($Context) {
    $all=@(Get-CimInstance Win32_Process)
    $backend=Get-CimInstance Win32_Service -Filter ("Name='"+$script:BackendName+"'")
    $owned=New-Object 'System.Collections.Generic.HashSet[int]'
    if (-not $backend -or $backend.ProcessId -le 0) { throw '无法确认 OSS 后端进程归属。' }
    [void]$owned.Add([int]$backend.ProcessId)
    do {
        $added=$false
        foreach ($process in $all) { if ($owned.Contains([int]$process.ParentProcessId) -and $owned.Add([int]$process.ProcessId)) { $added=$true } }
    } while ($added)
    foreach ($process in $all) {
        # The official installer uses taskkill by image name. Refuse other
        # installations/sessions rather than risking their active inference.
        if ($process.Name -match '^(ollama(?:[ ._-].*)?|llama-server)\.exe$' -and -not $owned.Contains([int]$process.ProcessId)) {
            throw '检测到不属于 OSS 后端的 Ollama 进程。请先退出官方桌面程序或其他实例，再升级；未停止任何进程。'
        }
    }
}

function Get-OllamaServiceModes {
    return @(foreach ($name in @($script:GatewayName,$script:BackendName)) {
        $key='Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\'+$name
        $values=Get-ItemProperty -LiteralPath $key
        $delay=0; if ($values.PSObject.Properties['DelayedAutoStart']) { $delay=[int]$values.DelayedAutoStart }
        [pscustomobject]@{name=$name;start=[int]$values.Start;delayed=$delay}
    })
}

function Restore-OllamaServiceModes($Transaction) {
    foreach ($entry in $Transaction.service_modes) {
        if ($entry.name -notin @($script:GatewayName,$script:BackendName) -or $entry.start -notin @(2,3,4)) { throw '服务启动方式备份无效。' }
        $mode=@{2='Automatic';3='Manual';4='Disabled'}[[int]$entry.start]
        Set-Service -Name $entry.name -StartupType $mode
        Set-ItemProperty -LiteralPath ('Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\'+$entry.name) -Name DelayedAutoStart -Value ([int]$entry.delayed)
    }
    if ($Transaction.watchdog_enabled) { Enable-ScheduledTask -TaskName $script:TaskName | Out-Null }
}

function Disable-OllamaOriginalStartup($Context,$Backup) {
    $saved=@(); $shell=New-Object -ComObject WScript.Shell
    try {
        foreach ($key in @("Registry::HKEY_USERS\$($Context.registration.caller_sid)\Software\Microsoft\Windows\CurrentVersion\Run",
                           'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run')) {
            if (-not (Test-Path -LiteralPath $key)) { continue }
            $registry=Get-Item -LiteralPath $key
            foreach ($name in $registry.GetValueNames()) {
                $value=$registry.GetValue($name,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                if ($name -match '^Ollama$' -or ([string]$value -match '(?i)[\\/]ollama(?: app)?\.exe(?:"|\s|$)')) {
                    $saved+=@{path=$key;name=$name;value=$value;kind=$registry.GetValueKind($name).ToString()}
                    Save-Json $saved (Join-Path $Backup 'startup-after-installer.json')
                    Remove-ItemProperty -LiteralPath $key -Name $name
                }
            }
        }
        foreach ($folder in @((Join-Path $Context.registration.caller_profile 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'),[Environment]::GetFolderPath('CommonStartup'))) {
            if (-not (Test-Path -LiteralPath $folder)) { continue }
            foreach ($link in @(Get-ChildItem -LiteralPath $folder -Filter '*.lnk')) {
                if ([IO.Path]::GetFileName($shell.CreateShortcut($link.FullName).TargetPath) -match '^ollama(?: app)?\.exe$') {
                    Copy-Item -LiteralPath $link.FullName -Destination (Join-Path $Backup ([guid]::NewGuid().ToString('N')+'.lnk'))
                    Remove-Item -LiteralPath $link.FullName
                }
            }
        }
    } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) }
}

function Invoke-OllamaOfficialInstaller($Context,[string]$Installer,[string]$Backup) {
    # Same Windows SID, explicit original directory; never uninstall Ollama.
    # Both services are disabled/stopped and admission stays closed throughout.
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$Installer
    $info.Arguments=(@('/VERYSILENT','/SUPPRESSMSGBOXES','/SP-','/NORESTART','/RESTARTEXITCODE=3010',
        ('/DIR='+$Context.source_dir),('/LOG='+(Join-Path $Backup 'official-installer.log'))) | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    # The installer's post-install app inherits a private loopback port, avoiding
    # the public gateway entry until we close that app and restore OSS ownership.
    $listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
    $listener.Start(); $port=$listener.LocalEndpoint.Port; $listener.Stop()
    $info.EnvironmentVariables['OLLAMA_HOST']='127.0.0.1:'+$port
    $process=[Diagnostics.Process]::Start($info)
    try { $process.WaitForExit(); return $process.ExitCode } finally { $process.Dispose() }
}

function Restore-OllamaOwnership($Context,$Transaction,[bool]$InstallerRan) {
    if ($InstallerRan) {
        Write-OllamaUpdateStatus 'restoring' '正在退出安装器启动的官方后台，恢复 OSS 接管…'
        # Only the selected installation is touched, and only after a verified
        # idle stop. Never kill all ollama.exe processes by global image name.
        for ($attempt=0;$attempt -lt 5;$attempt++) {
            foreach ($process in @(Get-OllamaSourceProcesses $Context)) { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop }
            Start-Sleep -Milliseconds 500
        }
        if (@(Get-OllamaSourceProcesses $Context).Count) { throw '官方后台尚未退出，暂不启动 OSS。' }
        Disable-OllamaOriginalStartup $Context $Transaction.backup
        Grant-Directory $Context.source_dir 'S-1-5-19' 'ReadAndExecute'
    }
    foreach ($file in $Transaction.protected_files) {
        if ((Get-FileHash -LiteralPath $file.path -Algorithm SHA256).Hash -ne $file.sha256) {
            throw ('升级期间配置发生变化，已保留备份并暂停接单，请核对：'+$file.path)
        }
    }
    # Start only while the admission marker remains present. Restore failure
    # recovery/start modes after verifying the backend and gateway.
    foreach ($name in @($script:BackendName,$script:GatewayName)) { Set-Service -Name $name -StartupType Manual }
    Start-ManagedServices
}

function Assert-OllamaUpdateRestored($Context,$Transaction,[string]$ExpectedVersion='',[int]$TimeoutSeconds=90) {
    $deadline=(Get-Date).AddSeconds($TimeoutSeconds); $last=''
    do {
        try {
            $status=Invoke-OllamaUpdateApi ($Context.gateway+'/status')
            $version=Invoke-OllamaUpdateApi ($Context.backend+'/api/version')
            $gatewayVersion=Invoke-OllamaUpdateApi ($Context.gateway+'/api/version')
            $null=ConvertTo-OllamaVersion $version.version
            if ($status.service -ne 'OllamaStatusService' -or -not $status.ollama_online -or
                -not $status.ollama_update.maintenance -or -not $status.ollama_update.safe_to_stop -or
                $version.version -ne $gatewayVersion.version) { throw '后端或网关尚未通过就绪检查。' }
            if ($ExpectedVersion -and $version.version -ne $ExpectedVersion) { throw ('实际 Ollama 版本为 '+$version.version+'，预期为 '+$ExpectedVersion) }
            $inventory=@(Get-OllamaInventory $Context)
            if (($inventory -join "`n") -cne (@($Transaction.inventory) -join "`n")) { throw '升级前后模型名称或摘要不一致，暂不恢复接单。' }
            return [string]$version.version
        } catch { $last=$_.Exception.Message; Start-Sleep -Seconds 2 }
    } while ((Get-Date) -lt $deadline)
    throw ('恢复验证未通过：'+$last)
}

function Invoke-OllamaManagedUpdate([string]$TargetVersion,[switch]$Recover) {
    $marker=Join-Path $script:StateRoot 'updater\maintenance.lock'
    $context=Get-OllamaUpdateContext
    $transaction=$null; $mutated=$false; $installerRan=$false; $gateOwned=$false; $rebootRequired=$false
    if (Test-Path -LiteralPath $marker) {
        if (-not $Recover) { throw '上次升级仍未恢复，请使用“恢复上次升级”。' }
        if ((Get-Item -LiteralPath $marker).Length -eq 0) {
            # An empty marker can only precede the first atomic transaction save.
            Remove-Item -LiteralPath $marker
            Write-OllamaUpdateStatus 'complete' '已取消未进入停服阶段的升级准备，服务与任务保持原状。' $false $true
            return
        }
        $transaction=Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($transaction.PSObject.Properties['kind'] -and $transaction.kind -eq 'oss-install') { throw 'OSS 安装未完成，请重新运行新版 Install.exe 继续安装。' }
        $expectedBackup=Join-Path $script:StateRoot ('updater\backups\ollama-update-'+$transaction.id)
        if ($transaction.id -notmatch '^[a-f0-9]{32}$' -or $transaction.backup -ne $expectedBackup) { throw '升级恢复记录无效，请检查升级日志。' }
        if (-not $transaction.services_touched) {
            Remove-Item -LiteralPath $marker
            Write-OllamaUpdateStatus 'complete' '已取消停服前的升级准备，服务与任务保持原状。' $false $true
            return
        }
        if ($transaction.reboot_required -and [string](Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o') -eq $transaction.boot_time) {
            throw '官方安装器要求重启 Windows，请重启后再恢复。当前不启动尚待替换的程序。'
        }
        # Do not race an installer surviving a killed coordinator or a reboot.
        if (@(Get-CimInstance Win32_Process -Filter "Name='OllamaSetup.exe'").Count) { throw '官方安装器仍在运行，请等待安装器结束后恢复。' }
        $gateOwned=$true; $mutated=$true; $installerRan=[bool]$transaction.installer_started
    } elseif ($Recover) { throw '没有需要恢复的升级。' }
    try {
        if (-not $Recover) {
            Assert-OllamaUpdateIdle $context
            Assert-OllamaProcessOwnership $context
            $release=Get-OllamaRelease $TargetVersion
            $current=Invoke-OllamaUpdateApi ($context.backend+'/api/version')
            if (-not (Test-NewOllamaVersion $release.version $current.version)) { throw ('当前 Ollama '+$current.version+' 已是相同或更新版本，未执行安装。') }
            $installer=Get-OllamaInstaller $release
            $sourceDrive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($context.source))
            if ($sourceDrive.AvailableFreeSpace -lt ($release.size*2+1GB)) { throw 'Ollama 安装磁盘可用空间不足，未停止服务。' }
            # Exclusive creation closes admission before the final idle snapshot.
            $handle=[IO.File]::Open($marker,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            $handle.Dispose(); $gateOwned=$true
            Write-OllamaUpdateStatus 'preflight' '已临时关闭新任务入口，正在确认没有运行、排队、下载和未知任务…'
            Assert-OllamaUpdateIdle $context $true
            $id=[guid]::NewGuid().ToString('N')
            $backup=Join-Path $script:StateRoot ('updater\backups\ollama-update-'+$id)
            New-Item -ItemType Directory -Path $backup | Out-Null
            $protectedPaths=@($script:RegistrationPath,$context.config_path,$context.backend_path,(Join-Path $script:InstallRoot ($script:GatewayName+'.xml')))
            $networkPath=Join-Path $script:InstallRoot 'download-network.json'
            if (Test-Path -LiteralPath $networkPath) { $protectedPaths += $networkPath }
            $protected=@(foreach ($path in $protectedPaths) {
                Copy-Item -LiteralPath $path -Destination $backup
                [pscustomobject]@{path=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash}
            })
            $task=Get-ScheduledTask -TaskName $script:TaskName
            $transaction=[pscustomobject]@{id=$id;backup=$backup;target=$release.version;previous=$current.version;installer_started=$false;
                services_touched=$false;reboot_required=$false;boot_time=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o');
                service_modes=@(Get-OllamaServiceModes);watchdog_enabled=[bool]$task.Settings.Enabled;protected_files=$protected;inventory=@(Get-OllamaInventory $context)}
            Save-OllamaTransaction $transaction $marker
            # Recheck after backup/network reads; the gate remains closed.
            Assert-OllamaUpdateIdle $context $true
            Assert-OllamaProcessOwnership $context
            Test-OllamaInstaller $installer $release
            Write-OllamaUpdateStatus 'stopping' '任务已全部结束，正在停止 OSS 网关和 Ollama 后台…'
            $transaction.services_touched=$true
            Save-OllamaTransaction $transaction $marker
            $mutated=$true
            Disable-ScheduledTask -TaskName $script:TaskName | Out-Null
            Stop-ScheduledTask -TaskName $script:TaskName
            foreach ($name in @($script:GatewayName,$script:BackendName)) { Set-Service -Name $name -StartupType Disabled }
            Stop-ManagedServices
            Assert-BackendStopped
            $transaction.installer_started=$true
            Save-OllamaTransaction $transaction $marker
            $installerRan=$true
            Write-OllamaUpdateStatus 'installing' ('正在安装官方 Ollama '+$release.version+'，保留原安装路径、模型和配置。请勿关机。')
            $exitCode=Invoke-OllamaOfficialInstaller $context $installer $backup
            if ($exitCode -eq 3010) {
                $rebootRequired=$true; $transaction.reboot_required=$true
                Save-OllamaTransaction $transaction $marker
                throw '官方安装器要求重启 Windows。接单入口保持关闭，请重启后使用“恢复上次升级”。'
            }
            if ($exitCode -ne 0) { throw ('官方安装器退出码 '+$exitCode+'；日志：'+(Join-Path $backup 'official-installer.log')) }
        }
        Restore-OllamaOwnership $context $transaction $installerRan
        Write-OllamaUpdateStatus 'verifying' '正在验证 Ollama 版本、后端与网关连接、原模型列表和配置…'
        $actual=Assert-OllamaUpdateRestored $context $transaction $(if ($Recover) { '' } else { $transaction.target })
        Restore-OllamaServiceModes $transaction
        Remove-Item -LiteralPath $marker
        $gateOwned=$false
        Write-OllamaUpdateStatus 'complete' ('已恢复 OSS 接管，Ollama 版本 '+$actual+'。原模型和配置验证通过。备份与日志：'+$transaction.backup) $false $true
    } catch {
        $failure=$_.Exception.Message
        if ($mutated -and $transaction -and -not $rebootRequired) {
            try {
                # Failed installs may still leave a usable old/new binary. Recover
                # service ownership, but never describe a failed upgrade as success.
                Restore-OllamaOwnership $context $transaction $installerRan
                $actual=Assert-OllamaUpdateRestored $context $transaction
                Restore-OllamaServiceModes $transaction
                Remove-Item -LiteralPath $marker
                $gateOwned=$false
                $failure+="`r`n已恢复 OSS 服务，当前 Ollama："+$actual+'。'
            } catch { $failure+="`r`n自动恢复未通过："+$_.Exception.Message+'。新任务入口保持关闭，请点击“恢复上次升级”；备份：'+$transaction.backup }
        }
        throw $failure
    } finally {
        if ($gateOwned -and -not $mutated) { Remove-Item -LiteralPath $marker -ErrorAction SilentlyContinue }
    }
}
