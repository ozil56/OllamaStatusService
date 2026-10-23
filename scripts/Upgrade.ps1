# Reuse the ordinary uninstall/install engines, without separate windows or UAC.
if (-not (Get-Variable context -ErrorAction SilentlyContinue)) { throw '请双击新包中的 Install.exe。' }
Set-Progress '正在校验安装包；升级将先卸载旧版，再安装新版…'
$null=Test-PackageIntegrity $packageRoot
$previous=Get-Content -LiteralPath $script:RegistrationPath -Encoding UTF8 -Raw | ConvertFrom-Json
$backendConfigPath=Join-Path $script:InstallRoot ($script:BackendName+'.xml')
[xml]$oldBackend=Get-Content -LiteralPath $backendConfigPath -Raw -Encoding UTF8
$modelEntry=$oldBackend.SelectSingleNode('/service/env[@name="OLLAMA_MODELS"]/@value')
if ($modelEntry -and -not [string]::IsNullOrWhiteSpace($modelEntry.Value)) {
    $configuredModels=$modelEntry.Value.Trim().Trim('"',"'")
    $profileEntry=$oldBackend.SelectSingleNode('/service/env[@name="USERPROFILE"]/@value')
    $modelProfile=if ($profileEntry) { $profileEntry.Value } else { $previous.caller_profile }
    $configuredModels=[regex]::Replace($configuredModels,'(?i)%USERPROFILE%',{param($match) $modelProfile})
    $configuredModels=[Environment]::ExpandEnvironmentVariables($configuredModels)
    if ($configuredModels -notmatch '^[A-Za-z]:[\\/]') { throw '现有服务的模型路径不是本机绝对路径，请先核对后端配置。当前安装未修改。' }
    $previous.models=[IO.Path]::GetFullPath($configuredModels)
}
$configPath=Join-Path $script:StateRoot 'config.json'
$existingConfig=Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw | ConvertFrom-Json
if ([string]$existingConfig.data_dir -notmatch '^(?:[A-Za-z]:[\\/]|\\\\)') {
    throw '旧配置 data_dir 必须是绝对路径，防止卸载时误删相对路径中的任务记录。当前安装未修改。'
}
$installBase=[IO.Path]::GetFullPath($script:InstallRoot).TrimEnd('\')
foreach ($protected in @($packageRoot,$previous.source_exe,$previous.models,$existingConfig.data_dir)) {
    $absolute=[IO.Path]::GetFullPath([string]$protected)
    if ($absolute -eq $installBase -or $absolute.StartsWith($installBase+'\',[StringComparison]::OrdinalIgnoreCase)) {
        throw '新安装包、原 Ollama、模型和任务数据均须位于状态服务程序目录之外。当前安装未修改。'
    }
}
if (-not (Test-Path -LiteralPath $previous.source_exe -PathType Leaf) -or -not (Test-Path -LiteralPath $previous.models -PathType Container)) {
    throw '原 Ollama 程序或模型目录不存在，当前安装未修改。请先恢复原路径。'
}
foreach ($name in @($script:BackendName,$script:GatewayName)) {
    $registered=Get-CimInstance Win32_Service -Filter ("Name='"+$name+"'")
    if ($registered -and $registered.PathName.Trim('"') -ne (Join-Path $script:InstallRoot ($name+'.exe'))) {
        throw ('服务路径与旧安装记录不一致，已停止升级：' + $name)
    }
}
function Assert-OssUpgradeIdle($Status,[bool]$Gated=$false) {
    if ($Status.service -ne 'OllamaStatusService' -or $Status.state -notin @('idle','maintenance')) {
        throw '请等待所有任务完成，unknown 请先通过托盘恢复。当前安装未修改。'
    }
    if ($Status.PSObject.Properties['ollama_update'] -and $Status.ollama_update.protocol -eq 1) {
        if (-not $Status.ollama_online -or $Status.inflight_requests -ne 0 -or $Status.queued_requests -ne 0 -or
            $Status.uncertain_requests -ne 0 -or $Status.inflight_model_transfers -ne 0 -or $Status.model_jobs.active -ne 0 -or
            $Status.ollama_update.inflight_http_operations -ne 0 -or $Status.ollama_update.unknown_model_jobs -ne 0 -or
            $Status.ollama_update.model_workers -ne 0 -or
            ($Status.PSObject.Properties['download_network'] -and $Status.download_network.active_tunnels -gt 0)) {
            throw '存在运行、排队、传输或未知任务，未停止服务，请任务结束后重试。'
        }
        if ($Gated -and (-not $Status.ollama_update.safe_to_stop -or -not $Status.ollama_update.maintenance -or $Status.accepting_requests)) {
            throw '新任务入口尚未可靠关闭，未停止服务。'
        }
    } elseif ($Gated) { throw '无法确认安全停服状态，未停止服务。' }
}
$supportsUpgradeGate=$false
$upgradePort=11434
if ($existingConfig.PSObject.Properties['listen_port']) { $upgradePort=[int]$existingConfig.listen_port }
$upgradeStatusUrl='http://127.0.0.1:'+$upgradePort+'/status'
$gateway=Get-Service -Name $script:GatewayName -ErrorAction SilentlyContinue
try {
    if ($gateway -and $gateway.Status -eq 'Running') {
        $status=Invoke-RestMethod $upgradeStatusUrl -TimeoutSec 3
        Assert-OssUpgradeIdle $status
        $supportsUpgradeGate=$status.PSObject.Properties['ollama_update'] -and $status.ollama_update.protocol -eq 1
    }
} finally { if ($gateway) { $gateway.Dispose() } }
Invoke-Native (Join-Path $packageRoot 'runtime\python.exe') @((Join-Path $packageRoot 'app\validate_upgrade.py'),'--config',$configPath,'--check-idle') | Out-Null
# Refuse a new proxy-port conflict BEFORE uninstalling the working service.
$proxyPort=11436
if ($existingConfig.PSObject.Properties['proxy_listen_port']) { $proxyPort=[int]$existingConfig.proxy_listen_port }
$directPort=11434
if ($existingConfig.PSObject.Properties['listen_port']) { $directPort=[int]$existingConfig.listen_port }
$directOwners=@(Get-NetTCPConnection -LocalPort $directPort -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)
foreach ($listener in @(Get-NetTCPConnection -LocalPort $proxyPort -State Listen -ErrorAction SilentlyContinue)) {
    if ($listener.OwningProcess -notin $directOwners) { throw "代理入口端口 $proxyPort 已被其他程序占用。当前安装未修改，请先释放端口或调整 proxy_listen_port。" }
}
$upgradeBackup=Join-Path $script:StateRoot ('backup\upgrade-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $upgradeBackup -Force | Out-Null
foreach ($path in @($script:RegistrationPath,$configPath,(Join-Path $script:InstallRoot ($script:BackendName+'.xml')))) {
    Copy-Item -LiteralPath $path -Destination $upgradeBackup
}
$script:UpgradeState=@{ConfigBackup=(Join-Path $upgradeBackup 'config.json');BackendEnvironment=@{};BackupRoot=$upgradeBackup}
foreach ($entry in $oldBackend.service.env) {
    if (($entry.name -match '^OLLAMA_' -and $entry.name -notin @('OLLAMA_HOST','OLLAMA_MODELS','OLLAMA_API_KEY')) -or
        $entry.name -in @('CUDA_VISIBLE_DEVICES','HIP_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','GGML_VK_VISIBLE_DEVICES')) {
        $script:UpgradeState.BackendEnvironment[$entry.name]=[string]$entry.value
    }
}
$oldNetworkPath=Join-Path $script:InstallRoot 'download-network.json'
if (Test-Path -LiteralPath $oldNetworkPath) {
    Copy-Item -LiteralPath $oldNetworkPath -Destination $upgradeBackup
    $script:UpgradeState.DownloadNetwork=Get-Content -LiteralPath $oldNetworkPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    $proxyEntry=$oldBackend.SelectSingleNode('/service/env[@name="HTTPS_PROXY"]/@value')
    if ($proxyEntry) { $script:UpgradeState.DownloadProxy=$proxyEntry.Value }
}
$firewall=Get-NetFirewallRule -Name $script:FirewallName -ErrorAction SilentlyContinue
if ($firewall) {
    $address=$firewall | Get-NetFirewallAddressFilter
    $script:UpgradeState.Firewall=@{RemoteAddress=@($address.RemoteAddress);LocalAddress=@($address.LocalAddress);
        Profile=[string]$firewall.Profile;Enabled=[string]$firewall.Enabled}
}
Save-Json $script:UpgradeState (Join-Path $upgradeBackup 'upgrade-settings.json')
$context.SourceExe=$previous.source_exe
$context.Models=$previous.models
$context.CallerProfile=$previous.caller_profile
$context.CallerSid=$previous.caller_sid
$ossGate=Join-Path $script:StateRoot 'updater\maintenance.lock'
$ossGateOwned=$false; $upgradeStarted=$false
try {
    if ($supportsUpgradeGate) {
        Initialize-OllamaUpdateDirectory
        $handle=[IO.File]::Open($ossGate,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
        $ossGateOwned=$true
        try {
            $bytes=[Text.Encoding]::UTF8.GetBytes('{"kind":"oss-install"}')
            $handle.Write($bytes,0,$bytes.Length)
        } finally { $handle.Dispose() }
        # Close admission before the final snapshot, including slow uploads and
        # download tunnels. Busy work continues and the gate is removed below.
        Assert-OssUpgradeIdle (Invoke-RestMethod $upgradeStatusUrl -TimeoutSec 3) $true
    }
    $upgradeStarted=$true
    Set-Progress '第 1/2 步：卸载旧版服务，保留原 Ollama、模型和数据…'
    . (Join-Path $PSScriptRoot 'Uninstall.ps1') -AsUpgrade
    # Close transient ServiceController handles before registering the same names again.
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    $removed=$false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        $remaining=@(Get-Service -Name $script:BackendName,$script:GatewayName -ErrorAction SilentlyContinue)
        if (-not $remaining.Count) { $removed=$true; break }
        foreach ($service in $remaining) { $service.Dispose() }
        Start-Sleep -Milliseconds 500
    }
    if (-not $removed) { throw 'Windows 尚未释放旧服务，请关闭 services.msc 等服务管理窗口后重试。' }
    Set-Progress '第 2/2 步：安装新版服务，沿用原配置并启用共享队列的代理专用入口…'
    . (Join-Path $PSScriptRoot 'Setup.ps1') -AsUpgrade
    Complete-Setup $true "已升级到 1.6.1：右键托盘可检查更新／升级 Ollama，有新版时自动提示；有任务时不停止服务。`r`n原配置和模型路径已保留，/manage 网页管理与现有认证继续使用。`r`n设置备份：$upgradeBackup"
} catch {
    Write-SetupPhase $_.ScriptStackTrace
    if (-not $upgradeStarted) { throw ("升级未开始，当前服务与任务保持原状："+$_.Exception.Message) }
    throw ("升级未完成：" + $_.Exception.Message + "`r`n模型、日志和任务记录未删除。设置备份：" + $upgradeBackup +
        "`r`n旧版卸载后不会自动回装。请保留安装包并查看日志，修复原因后重新运行；设置可从上述备份恢复。")
} finally {
    if ($ossGateOwned) { Remove-Item -LiteralPath $ossGate -ErrorAction SilentlyContinue }
    Remove-Variable UpgradeState -Scope Script -ErrorAction SilentlyContinue
}
