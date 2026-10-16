param([switch]$AsUpgrade)
# Headless installation engine. Start through 安装.exe / SetupWorker.ps1.
if (-not (Get-Variable context -ErrorAction SilentlyContinue)) { throw '请双击安装.exe。' }
$CallerProfile=[string]$context.CallerProfile
$CallerSid=[string]$context.CallerSid
$sourceExe=[IO.Path]::GetFullPath([string]$context.SourceExe)
$models=[IO.Path]::GetFullPath([string]$context.Models)
$registration=$null
$mutated=$false
$transcript=$false
foreach ($name in @($script:BackendName, $script:GatewayName)) {
    if (Get-Service -Name $name -ErrorAction SilentlyContinue) { throw "发现已有服务 $name，请先卸载旧版；当前服务未修改。" }
}
try {
    if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf) -or [IO.Path]::GetFileName($sourceExe) -ne 'ollama.exe') { throw '请选择有效的 ollama.exe。' }
    if (-not (Test-Path -LiteralPath $models -PathType Container)) { throw '模型目录不存在，请选择已安装模型所在目录。' }
    if ($sourceExe.StartsWith($script:InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '请选择原始 Ollama 安装目录中的程序，不能选择本产品目录中的副本。' }
    if ($models.StartsWith($script:InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or $models -eq $script:InstallRoot) { throw '模型目录不能放在本产品安装目录内。' }
    if ([IO.Path]::GetPathRoot($models).TrimEnd('\') -eq $models.TrimEnd('\')) { throw '不能将整个磁盘根目录作为模型目录。' }
    if ($models.StartsWith('\\')) { throw '此版本使用 LocalService 账户，请使用服务器本地磁盘上的模型目录。' }
    $proxyPort=11436; $proxyHost='127.0.0.1'
    if ($AsUpgrade) {
        $upgradeConfig=Get-Content -LiteralPath $script:UpgradeState.ConfigBackup -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($upgradeConfig.PSObject.Properties['proxy_listen_port']) { $proxyPort=[int]$upgradeConfig.proxy_listen_port }
        if ($upgradeConfig.PSObject.Properties['proxy_listen_host']) { $proxyHost=[string]$upgradeConfig.proxy_listen_host }
    }
    foreach ($port in @(11435,$proxyPort)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "端口 $port 已被其他程序使用，请先释放端口。" }
    }
    $sourceDir = Split-Path -Parent $sourceExe
    if ($sourceDir.TrimEnd('\') -eq [IO.Path]::GetPathRoot($sourceDir).TrimEnd('\') -or $script:InstallRoot.StartsWith($sourceDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '请将 Ollama 放在独立程序目录中，不能使用磁盘根目录或本产品的上级目录。'
    }
    $listeners = @(Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if (-not $process.ExecutablePath -or -not $process.ExecutablePath.StartsWith($sourceDir + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '11434 被所选 Ollama 目录之外的进程占用，安装已停止。' }
    }
    # Check bundled payload before making any system changes.
    Write-SetupPhase '正在校验安装包…'
    $verifiedCount=Test-PackageIntegrity $packageRoot
    Write-SetupPhase ('校验通过，文件数：' + $verifiedCount)
    New-Item -ItemType Directory -Path $script:StateRoot -Force | Out-Null
    Start-Transcript -LiteralPath (Join-Path $script:StateRoot 'install.log') -Append | Out-Null; $transcript = $true
    Set-Progress '正在安装状态服务和自带 Python；直接使用现有 Ollama，不复制 GPU 库…'
    Write-Host '正在复制状态服务运行环境，请稍候……'
    New-Item -ItemType Directory -Path $script:InstallRoot -Force | Out-Null
    foreach ($entry in @('app','runtime','vendor','scripts','src','licenses','bin','examples','OllamaTray.exe','部署说明.html','部署说明.txt','AI接入说明.txt','config.example.json','requirements.lock.txt')) {
        Copy-Item -LiteralPath (Join-Path $packageRoot $entry) -Destination $script:InstallRoot -Recurse -Force
    }
    foreach ($directory in @('data','logs','temp','backup','backend-profile')) {
        New-Item -ItemType Directory -Path (Join-Path $script:StateRoot $directory) -Force | Out-Null
    }
    Grant-Directory $script:StateRoot 'S-1-5-19' 'ReadAndExecute'
    foreach ($directory in @('data','logs','temp','backend-profile')) {
        Grant-Directory (Join-Path $script:StateRoot $directory) 'S-1-5-19' 'Modify'
    }
    Grant-Directory $script:StateRoot 'S-1-5-32-545' 'ReadAndExecute'
    $registration = [ordered]@{ version='1.2.5'; caller_sid=$CallerSid; caller_profile=$CallerProfile; source_exe=$sourceExe; source_dir=$sourceDir; source_acl=(Get-Acl -LiteralPath $sourceDir).Sddl; models=$models; model_acl=(Get-Acl -LiteralPath $models).Sddl; startup_values=@(); startup_links=@(); installed_at=(Get-Date).ToString('o') }
    # Registry values are saved durably before removal, including when installation fails.
    foreach ($key in @("Registry::HKEY_USERS\$CallerSid\Software\Microsoft\Windows\CurrentVersion\Run", 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run')) {
        if (-not (Test-Path -LiteralPath $key)) { continue }
        $registry = Get-Item -LiteralPath $key
        foreach ($name in $registry.GetValueNames()) {
            $value = $registry.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            if ($name -match '^Ollama$' -or ([string]$value -match '(?i)[\\/]ollama(?: app)?\.exe(?:"|\s|$)')) {
                $registration.startup_values += [ordered]@{ path=$key; name=$name; value=$value; kind=$registry.GetValueKind($name).ToString() }
            }
        }
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @((Join-Path $CallerProfile 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'), [Environment]::GetFolderPath('CommonStartup'))) {
        if (-not (Test-Path -LiteralPath $folder)) { continue }
        foreach ($link in @(Get-ChildItem -LiteralPath $folder -Filter '*.lnk')) {
            $target = $shell.CreateShortcut($link.FullName).TargetPath
            if ([IO.Path]::GetFileName($target) -match '^ollama(?: app)?\.exe$') {
                $backup = Join-Path $script:StateRoot ('backup\' + [guid]::NewGuid().ToString() + '.lnk')
                Copy-Item -LiteralPath $link.FullName -Destination $backup
                $registration.startup_links += [ordered]@{ original=$link.FullName; backup=$backup }
            }
        }
    }
    $registration.model_acl_journal=Join-Path $script:StateRoot ('backup\model-acl-'+[guid]::NewGuid().ToString('N')+'.jsonl')
    [IO.File]::WriteAllText($registration.model_acl_journal,'',[Text.UTF8Encoding]::new($false))
    Save-Json $registration $script:RegistrationPath
    $mutated = $true
    Set-Progress '正在备份启动项、切换 Ollama 为系统服务…'
    foreach ($entry in $registration.startup_values) { Remove-ItemProperty -LiteralPath $entry.path -Name $entry.name }
    foreach ($entry in $registration.startup_links) { Remove-Item -LiteralPath $entry.original }
    Grant-Directory $sourceDir 'S-1-5-19' 'ReadAndExecute'
    # Stop only Ollama processes from the selected distribution, plus their descendants.
    $all = @(Get-CimInstance Win32_Process)
    $ids = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($p in $all) {
        if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($sourceDir + '\', [StringComparison]::OrdinalIgnoreCase) -and $p.Name -match '^ollama(?:[ ._-].*)?\.exe$') { [void]$ids.Add([int]$p.ProcessId) }
    }
    do { $added=$false; foreach ($p in $all) { if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $added=$true } } } while ($added)
    foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    $deadline=(Get-Date).AddSeconds(15)
    while ((Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    if (Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue) { throw '11434 未释放，不能接管原端口。' }
    Assert-BackendStopped
    Set-Progress '正在核实并修复 LocalService 对模型目录及文件的权限…'
    Grant-ModelPermissions $models $registration.model_acl_journal
    Set-Progress '正在以 LocalService 身份验证模型读取、写入和目录访问…'
    $modelAccess=Test-ModelServiceAccess $models
    $config = [ordered]@{ backend_url='http://127.0.0.1:11435'; listen_host='0.0.0.0'; listen_ipv6=$true; listen_port=11434; proxy_listen_host='127.0.0.1'; proxy_listen_port=11436; max_queue_size=64; max_queue_bytes=67108864; queue_timeout_seconds=15; max_queue_timeout_seconds=3600; data_dir=(Join-Path $script:StateRoot 'data'); max_concurrency=1; request_deadline_seconds=3600; upstream_read_timeout_seconds=600; health_interval_seconds=2; health_timeout_seconds=3; max_body_bytes=33554432; max_response_bytes=67108864; client_write_timeout_seconds=15; shutdown_grace_seconds=50 }
    if ($AsUpgrade) { Copy-Item -LiteralPath $script:UpgradeState.ConfigBackup -Destination (Join-Path $script:StateRoot 'config.json') -Force }
    else { Save-Json $config (Join-Path $script:StateRoot 'config.json') }
    Clear-ConfirmedTasks
    function Escape-Xml([string]$Value) { return [Security.SecurityElement]::Escape($Value) }
    $logs = Escape-Xml (Join-Path $script:StateRoot 'logs')
    $modelXml=Escape-Xml $models
    $sourceExeXml=Escape-Xml $sourceExe
    $sourceDirXml=Escape-Xml $sourceDir
    # Preserve the original caller's GPU/runtime tuning. Service-specific host/models override it.
    $backendEnvironment=@{}
    foreach ($environmentKey in @('Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment', "Registry::HKEY_USERS\$CallerSid\Environment")) {
        if (-not (Test-Path -LiteralPath $environmentKey)) { continue }
        $environmentRegistry=Get-Item -LiteralPath $environmentKey
        foreach ($environmentName in $environmentRegistry.GetValueNames()) {
            if (($environmentName -match '^OLLAMA_' -and $environmentName -notin @('OLLAMA_HOST','OLLAMA_MODELS','OLLAMA_API_KEY')) -or $environmentName -in @('CUDA_VISIBLE_DEVICES','HIP_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','GGML_VK_VISIBLE_DEVICES')) {
                $backendEnvironment[$environmentName]=[string]$environmentRegistry.GetValue($environmentName)
            }
        }
    }
    if ($AsUpgrade) {
        foreach ($environmentName in $script:UpgradeState.BackendEnvironment.Keys) {
            $backendEnvironment[$environmentName]=$script:UpgradeState.BackendEnvironment[$environmentName]
        }
    }
    $environmentXml=(@(foreach ($environmentName in $backendEnvironment.Keys) {
        '  <env name="{0}" value="{1}"/>' -f (Escape-Xml $environmentName), (Escape-Xml $backendEnvironment[$environmentName])
    }) -join "`r`n")
    $tempXml=Escape-Xml (Join-Path $script:StateRoot 'temp')
    $profileXml=Escape-Xml (Join-Path $script:StateRoot 'backend-profile')
    $configXml=Escape-Xml (Join-Path $script:StateRoot 'config.json')
    $commonXml=@"
  <startmode>Automatic</startmode><delayedAutoStart>true</delayedAutoStart>
  <serviceaccount><domain>NT AUTHORITY</domain><user>LocalService</user></serviceaccount>
  <onfailure action="restart" delay="10 sec"/><onfailure action="restart" delay="30 sec"/><onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure><stoptimeout>90 sec</stoptimeout>
  <logpath>$logs</logpath><log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
"@
    $backendXml=@"
<service><id>$script:BackendName</id><name>Ollama Status - Backend</name>
  <description>Managed local Ollama backend for Ollama Status Service.</description>
  <executable>$sourceExeXml</executable><arguments>serve</arguments><workingdirectory>$sourceDirXml</workingdirectory>
  <env name="OLLAMA_HOST" value="127.0.0.1:11435"/><env name="OLLAMA_MODELS" value="$modelXml"/>
  <env name="USERPROFILE" value="$profileXml"/><env name="TEMP" value="$tempXml"/><env name="TMP" value="$tempXml"/>
$environmentXml
$commonXml
</service>
"@
    $gatewayXml=@"
<service><id>$script:GatewayName</id><name>Ollama Status - Gateway</name>
  <description>Ollama status API with shared local-priority queue for direct and remote-only proxy listeners.</description>
  <executable>%BASE%\runtime\python.exe</executable><arguments>-u "%BASE%\app\run.py" --config "$configXml"</arguments><workingdirectory>%BASE%</workingdirectory>
  <depend>$script:BackendName</depend><env name="PYTHONUNBUFFERED" value="1"/>
$commonXml
</service>
"@
    foreach ($name in @($script:BackendName, $script:GatewayName)) { Copy-Item -LiteralPath (Join-Path $script:InstallRoot 'bin\WinSW.exe') -Destination (Join-Path $script:InstallRoot ($name + '.exe')) -Force }
    $backendXml | Set-Content -LiteralPath (Join-Path $script:InstallRoot ($script:BackendName + '.xml')) -Encoding UTF8
    $gatewayXml | Set-Content -LiteralPath (Join-Path $script:InstallRoot ($script:GatewayName + '.xml')) -Encoding UTF8
    foreach ($name in @($script:BackendName, $script:GatewayName)) { Invoke-Native (Join-Path $script:InstallRoot ($name + '.exe')) @('install') }
    Set-Progress '正在配置 11434 防火墙例外、开机托盘和健康检查…'
    Get-NetFirewallRule -Name $script:FirewallName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    # 1.2.5 intentionally applies the same all-source rule on install AND upgrade.
    # The previous rule is retained in the upgrade backup for inspection/restoration.
    $ruleSettings=@{RemoteAddress='Any';LocalAddress='Any';Profile='Any';Enabled='True'}
    New-NetFirewallRule -Name $script:FirewallName -DisplayName 'Ollama 状态服务 TCP 11434（IPv4/IPv6）' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11434 -Program (Join-Path $script:InstallRoot 'runtime\python.exe') @ruleSettings | Out-Null
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $script:InstallRoot 'scripts\Watchdog.ps1') + '"')
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $script:TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $taskSettings -Force | Out-Null
    foreach ($folder in @([Environment]::GetFolderPath('CommonStartup'), [Environment]::GetFolderPath('CommonPrograms'))) {
        if (-not $folder) { continue }
        $link = $shell.CreateShortcut((Join-Path $folder 'Ollama 状态服务.lnk'))
        $link.TargetPath=Join-Path $script:InstallRoot 'OllamaTray.exe'; $link.WorkingDirectory=$script:InstallRoot; $link.Description='查看 Ollama 空闲、忙碌及后台服务状态'; $link.Save()
    }
    Start-ManagedServices
    Set-Progress '正在等待 Ollama 和状态接口就绪…'
    $ok=$false
    for ($i=0; $i -lt 45; $i++) {
        try {
            $status=Invoke-RestMethod 'http://127.0.0.1:11434/status' -TimeoutSec 2
            if ($status.ollama_online -and $status.version -eq '1.2.5' -and $status.scheduling_policy -eq 'local_first_fifo_per_origin') {
                $probeHost=$proxyHost
                if ($probeHost -eq '0.0.0.0') { $probeHost='127.0.0.1' }
                if ($probeHost -eq '::') { $probeHost='::1' }
                if ($probeHost.Contains(':')) { $probeHost='['+$probeHost+']' }
                $proxyHealth=Invoke-RestMethod ('http://'+$probeHost+':'+$proxyPort+'/healthz') -TimeoutSec 2
                if ($proxyHealth.ok -and $proxyHealth.service -eq 'OllamaStatusService' -and $proxyHealth.version -eq '1.2.5' -and $proxyHealth.request_origin -eq 'remote') {
                    $ok=$true; break
                }
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (-not $ok) { throw '服务未能在规定时间内就绪，请查看 ProgramData\OllamaStatusService\logs。安装将回滚系统注册。' }
    Set-Progress '正在核对 Ollama 后端和网关的模型列表…'
    Assert-ModelInventory $modelAccess.manifests
    Set-Progress '安装完成。' $false
    Stop-Transcript | Out-Null; $transcript=$false
    if ($AsUpgrade) { return }
    Complete-Setup $true "安装完成。直连入口 11434；代理入口 127.0.0.1:11436 固定为 remote。`r`n托盘图标已启动；若未显示，请展开右下角 ^。`r`n原 Ollama 和模型均保留。"
    exit 0
} catch {
    $message=$_.Exception.Message
    Write-SetupPhase ('安装未完成：' + $message)
    if (Test-Path -LiteralPath $script:StateRoot) { Set-Progress '安装未完成，正在退出。' $false }
    if ($mutated -and $registration) {
        try { Remove-Registrations; Restore-OriginalSettings ([pscustomobject]$registration) } catch { $message += "`r`n回滚未完全完成：" + $_.Exception.Message }
    }
    if ($transcript) { Stop-Transcript | Out-Null }
    if ($AsUpgrade) { throw $message }
    Complete-Setup $false ("安装未完成：" + $message + "`r`n启动日志：" + $BootstrapLog)
    exit 1
}
