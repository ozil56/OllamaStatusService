# Windows PowerShell 5.1 compatible. This file is shipped as UTF-8 with BOM.
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
$script:Product = 'OllamaStatusService'
$script:InstallRoot = Join-Path $env:ProgramFiles $script:Product
$script:StateRoot = Join-Path $env:ProgramData $script:Product
$script:BackendName = 'OllamaStatusBackend'
$script:GatewayName = 'OllamaStatusGateway'
$script:FirewallName = 'OllamaStatusService-11434'
$script:TaskName = 'OllamaStatusServiceWatchdog'
$script:RegistrationPath = Join-Path $script:StateRoot 'installation.json'
. (Join-Path $PSScriptRoot 'ModelPermissions.ps1')
. (Join-Path $PSScriptRoot 'Localization.ps1')

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Initialize-OllamaUpdateDirectory {
    # ProgramData's default ACL permits Users to create children. Update control
    # files and elevated executable downloads need an explicitly protected root.
    $directory=Join-Path $script:StateRoot 'updater'
    $existed=Test-Path -LiteralPath $directory
    if (-not $existed) { New-Item -ItemType Directory -Path $directory | Out-Null }
    $item=Get-Item -LiteralPath $directory -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '更新控制目录不能是符号链接或目录联接。' }
    if ($existed) {
        $owner=(Get-Acl -LiteralPath $directory).GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($owner -notin @('S-1-5-32-544','S-1-5-18')) { throw '更新控制目录的所有者不是管理员或 SYSTEM，请管理员检查该目录后重试。' }
    }
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach($sid in @('S-1-5-32-544','S-1-5-18','S-1-5-19','S-1-5-32-545')) {
        $rights=if($sid -in @('S-1-5-32-544','S-1-5-18')){'FullControl'}else{'ReadAndExecute'}
        $rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),$rights,'ContainerInherit,ObjectInherit','None','Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $directory -AclObject $acl
}

function Save-Json($Value, [string]$Path) {
    $json=$Value | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($Path,$json,[Text.UTF8Encoding]::new($true))
}

function Test-PackageIntegrity([string]$Root) {
    $resolvedRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
    # Explicit UTF-8 is required: Windows PowerShell 5.1 treats BOM-less text as ANSI.
    $decoded=Get-Content -LiteralPath (Join-Path $resolvedRoot 'manifest.sha256.json') -Encoding UTF8 -Raw | ConvertFrom-Json
    $manifest=@($decoded)
    if (-not $manifest.Count) { throw '安装清单为空。' }
    foreach ($item in $manifest) {
        if (-not $item.path -or $item.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw '安装清单格式无效。' }
        $file=[IO.Path]::GetFullPath((Join-Path $resolvedRoot $item.path))
        if (-not $file.StartsWith($resolvedRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '清单包含无效路径。' }
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw ('安装文件缺失：' + $file) }
        $actual=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
        if ($actual -ne $item.sha256) { throw ('安装文件哈希不一致：' + $file + "`r`n期望：" + $item.sha256 + "`r`n实际：" + $actual) }
    }
    return $manifest.Count
}

function ConvertTo-NativeArgument([string]$Value) {
    # Escape backslashes before quotes and before the closing quote (Windows CRT).
    return '"' + [regex]::Replace([regex]::Replace($Value,'(\\*)"','$1$1\"'),'(\\+)$','$1$1') + '"'
}

function Invoke-Native([string]$File, [string[]]$Arguments) {
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$File
    $info.Arguments=(@($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $info.EnvironmentVariables['PYTHONIOENCODING']='utf-8'
    $info.StandardOutputEncoding=[Text.Encoding]::UTF8
    $info.StandardErrorEncoding=[Text.Encoding]::UTF8
    $process=New-Object Diagnostics.Process
    $process.StartInfo=$info
    try {
        if (-not $process.Start()) { throw ('无法启动命令：' + $File) }
        $stdout=$process.StandardOutput.ReadToEndAsync()
        $stderr=$process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $output=$stdout.GetAwaiter().GetResult()
        $errors=$stderr.GetAwaiter().GetResult()
        if ($output) { Write-Output $output.TrimEnd() }
        if ($errors) { Write-Host $errors.TrimEnd() }
        if ($process.ExitCode -ne 0) { throw "命令失败（$($process.ExitCode)）：$File`r`n$errors" }
    } finally { $process.Dispose() }
}

function Grant-Directory([string]$Path, [string]$Sid, [string]$Rights) {
    $acl = Get-Acl -LiteralPath $Path
    $identity = New-Object Security.Principal.SecurityIdentifier($Sid)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, $Rights, 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Wait-ServiceState([string]$Name, [string]$Desired, [int]$Seconds = 100) {
    $service = Get-Service -Name $Name -ErrorAction Stop
    try { $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::$Desired, [TimeSpan]::FromSeconds($Seconds)) }
    finally { $service.Dispose() }
}

function Stop-ManagedServices {
    foreach ($name in @($script:GatewayName, $script:BackendName)) {
        $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
        try {
            if ($svc -and $svc.Status -ne 'Stopped') {
                Stop-Service -Name $name -Force
                Wait-ServiceState $name 'Stopped'
            }
        } finally { if ($svc) { $svc.Dispose() } }
    }
}

function Assert-BackendStopped {
    $listener = @(Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue)
    if ($listener.Count -gt 0) { throw '11435 后端仍在监听，不能清除未知任务。请先停止后端。' }
    if (Test-Path -LiteralPath $script:RegistrationPath) {
        $registration=Get-Content -LiteralPath $script:RegistrationPath -Encoding UTF8 -Raw | ConvertFrom-Json
        $sourceDir=Split-Path -Parent $registration.source_exe
        $owned = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($sourceDir + '\', [StringComparison]::OrdinalIgnoreCase)
        })
        if ($owned.Count -gt 0) { throw '仍有 Ollama 或其子进程，暂不清除任务记录。' }
    }
}

function Start-ManagedServices {
    Start-Service -Name $script:BackendName
    Wait-ServiceState $script:BackendName 'Running'
    Start-Service -Name $script:GatewayName
    Wait-ServiceState $script:GatewayName 'Running'
}

function Clear-ConfirmedTasks {
    Assert-BackendStopped
    $python = Join-Path $script:InstallRoot 'runtime\python.exe'
    if (Test-Path -LiteralPath $python) {
        Invoke-Native $python @((Join-Path $script:InstallRoot 'app\maintenance.py'), '--config', (Join-Path $script:StateRoot 'config.json'), '--backend-stopped')
    }
}

function Stop-ProductTray {
    $expected = Join-Path $script:InstallRoot 'OllamaTray.exe'
    Get-CimInstance Win32_Process -Filter "Name='OllamaTray.exe'" | Where-Object {
        $_.ExecutablePath -eq $expected
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Remove-Registrations {
    Stop-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $script:TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Stop-ManagedServices
    Stop-ProductTray
    foreach ($name in @($script:GatewayName, $script:BackendName)) {
        if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
            $wrapper = Join-Path $script:InstallRoot ($name + '.exe')
            if (Test-Path -LiteralPath $wrapper) { Invoke-Native $wrapper @('uninstall') }
            else { Invoke-Native "$env:SystemRoot\System32\sc.exe" @('delete', $name) }
        }
    }
    foreach ($ruleName in @($script:FirewallName, 'OllamaStatusService-11435')) { Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule }
    foreach ($folder in @([Environment]::GetFolderPath('CommonStartup'), [Environment]::GetFolderPath('CommonPrograms'))) {
        if (-not $folder) { continue }
        # Remove both the current shortcut and the pre-1.5 localized shortcut.
        foreach ($name in @('Ollama Status Service.lnk', 'Ollama 状态服务.lnk')) {
            $shortcut = Join-Path $folder $name
            if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
        }
    }
}

function Restore-OriginalSettings($Registration) {
    Restore-ModelPermissions $Registration
    foreach ($entry in @($Registration.startup_values)) {
        if (-not $entry) { continue }
        if (-not (Test-Path -LiteralPath $entry.path)) { New-Item -Path $entry.path -Force | Out-Null }
        $current = Get-ItemProperty -LiteralPath $entry.path -Name $entry.name -ErrorAction SilentlyContinue
        if (-not $current) { New-ItemProperty -LiteralPath $entry.path -Name $entry.name -Value $entry.value -PropertyType $entry.kind -Force | Out-Null }
    }
    foreach ($entry in @($Registration.startup_links)) {
        if (-not $entry) { continue }
        if ((Test-Path -LiteralPath $entry.backup) -and -not (Test-Path -LiteralPath $entry.original)) {
            Copy-Item -LiteralPath $entry.backup -Destination $entry.original
        }
    }
    $aclTargets=@(@{path=$Registration.models; sddl=$Registration.model_acl})
    if ($Registration.PSObject.Properties.Name -contains 'source_acl') {
        $aclTargets+=@{path=$Registration.source_dir; sddl=$Registration.source_acl}
    }
    foreach ($entry in $aclTargets) {
        if (-not $entry.sddl -or -not (Test-Path -LiteralPath $entry.path)) { continue }
        $acl = Get-Acl -LiteralPath $entry.path
        $original=New-Object Security.AccessControl.DirectorySecurity
        $original.SetSecurityDescriptorSddlForm($entry.sddl)
        $identity=New-Object Security.Principal.SecurityIdentifier('S-1-5-19')
        $acl.PurgeAccessRules($identity)
        foreach ($rule in $original.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier])) {
            if ($rule.IdentityReference.Value -eq 'S-1-5-19') { $acl.AddAccessRule($rule) }
        }
        Set-Acl -LiteralPath $entry.path -AclObject $acl
    }
}

function Show-Message([string]$Text, [string]$Title = 'Ollama 状态服务') {
    $Text=Get-UiText $Text; $Title=Get-UiText $Title
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($Text, $Title, 'OK', 'Information') | Out-Null
}
