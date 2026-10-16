# Reuse the ordinary uninstall/install engines, without separate windows or UAC.
if (-not (Get-Variable context -ErrorAction SilentlyContinue)) { throw '请双击新包中的安装.exe。' }
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
$gateway=Get-Service -Name $script:GatewayName -ErrorAction SilentlyContinue
try {
    if ($gateway -and $gateway.Status -eq 'Running') {
        $status=Invoke-RestMethod 'http://127.0.0.1:11434/status' -TimeoutSec 3
        if ($status.service -ne 'OllamaStatusService' -or $status.state -ne 'idle') {
            throw '请暂停本机和远端提交，等待所有任务完成后再升级；unknown 请先通过托盘恢复。当前安装未修改。'
        }
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
try {
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
    Complete-Setup $true "已升级到 1.2.4：修复并发模型下载超时，pull/push 不再占用推理队列。`r`n原配置和模型路径已保留；请把 FRP 等代理回源端口从 11434 改为 11436（自定义配置按实际值）。`r`n设置备份：$upgradeBackup"
} catch {
    Write-SetupPhase $_.ScriptStackTrace
    throw ("升级未完成：" + $_.Exception.Message + "`r`n模型、日志和任务记录未删除。设置备份：" + $upgradeBackup +
        "`r`n旧版卸载后不会自动回装。请保留安装包并查看日志，修复原因后重新运行；设置可从上述备份恢复。")
} finally { Remove-Variable UpgradeState -Scope Script -ErrorAction SilentlyContinue }
