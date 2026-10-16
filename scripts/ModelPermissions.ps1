# Windows PowerShell 5.1. Loaded by Common.ps1.
function Get-ModelTree([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw ('模型目录中存在链接/重解析点，请选择实际目录并核对链接目标：' + $item.FullName)
    }
    $item
    if ($item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force -ErrorAction Stop)) {
            Get-ModelTree $child.FullName
        }
    }
}

function Grant-ModelPermissions([string]$Path, [string]$JournalPath) {
    # Check the whole tree before changing it. Never follow junctions into other data.
    $items=@(Get-ModelTree $Path)
    $sid=[Security.Principal.SecurityIdentifier]::new('S-1-5-19')
    $modify=[Security.AccessControl.FileSystemRights]::Modify
    $inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    foreach ($item in $items) {
        $acl=Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
        $covered=$false
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($rule.IdentityReference -eq $sid -and $rule.AccessControlType -eq 'Allow' -and
                ($rule.FileSystemRights -band $modify) -eq $modify -and
                -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
                (-not $item.PSIsContainer -or ($rule.InheritanceFlags -band $inherit) -eq $inherit)) {
                $covered=$true; break
            }
        }
        if ($covered) { continue }
        # Persist the previous rules BEFORE mutation, including a partial-install failure.
        $entry=@{path=$item.FullName; sddl=$acl.Sddl; directory=[bool]$item.PSIsContainer}
        $line=ConvertTo-Json -InputObject $entry -Compress
        [IO.File]::AppendAllText($JournalPath,$line+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
        $flags=if ($item.PSIsContainer) { $inherit } else { [Security.AccessControl.InheritanceFlags]::None }
        $rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,$modify,$flags,'None','Allow')
        [void]$acl.AddAccessRule($rule)
        try { Set-Acl -LiteralPath $item.FullName -AclObject $acl -ErrorAction Stop }
        catch { throw ('无法授予 LocalService 模型访问权限：'+$item.FullName+'；'+$_.Exception.Message) }
    }
}

function Restore-ModelPermissions($Registration) {
    if (-not $Registration.PSObject.Properties['model_acl_journal'] -or -not $Registration.model_acl_journal) { return }
    $journal=[IO.Path]::GetFullPath([string]$Registration.model_acl_journal)
    $backup=[IO.Path]::GetFullPath((Join-Path $script:StateRoot 'backup')).TrimEnd('\')+'\'
    if (-not $journal.StartsWith($backup,[StringComparison]::OrdinalIgnoreCase)) { throw '模型权限备份路径无效。' }
    if (-not (Test-Path -LiteralPath $journal)) { throw ('模型权限备份缺失：'+$journal) }
    $modelRoot=[IO.Path]::GetFullPath([string]$Registration.models).TrimEnd('\')
    $sid=[Security.Principal.SecurityIdentifier]::new('S-1-5-19')
    foreach ($line in [IO.File]::ReadLines($journal)) {
        if (-not $line.Trim()) { continue }
        $entry=$line | ConvertFrom-Json
        $path=[IO.Path]::GetFullPath([string]$entry.path)
        if ($path -ne $modelRoot -and -not $path.StartsWith($modelRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '模型权限备份包含目录外路径。' }
        if (-not (Test-Path -LiteralPath $path)) { continue } # A model may have been removed normally.
        # Refuse redirected paths, including a parent replaced after installation.
        $cursor=$path
        while ($cursor.Length -ge $modelRoot.Length) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ('模型权限恢复遇到链接：'+$cursor) }
            if ($cursor -eq $modelRoot) { break }
            $cursor=Split-Path -Parent $cursor
        }
        $acl=Get-Acl -LiteralPath $path
        $original=if ($entry.directory) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
        $original.SetSecurityDescriptorSddlForm([string]$entry.sddl)
        # Restore only this account's explicit rules, preserving unrelated user/ACL changes.
        $acl.PurgeAccessRules($sid)
        foreach ($rule in $original.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier])) {
            if ($rule.IdentityReference -eq $sid) { [void]$acl.AddAccessRule($rule) }
        }
        Set-Acl -LiteralPath $path -AclObject $acl
    }
}

function Test-ModelServiceAccess([string]$Path, [int]$TimeoutSeconds=300) {
    $id=[guid]::NewGuid().ToString('N')
    $taskName='OllamaStatusModelProbe-'+$id
    $resultPath=Join-Path $script:StateRoot ('temp\model-access-'+$id+'.json')
    $probe=Join-Path $script:InstallRoot 'scripts\Probe-ModelAccess.ps1'
    $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File '+(ConvertTo-NativeArgument $probe)+
        ' -Models '+(ConvertTo-NativeArgument $Path)+' -ResultPath '+(ConvertTo-NativeArgument $resultPath)
    $registered=$false
    try {
        $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments
        $principal=New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount
        $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds ($TimeoutSeconds+30)) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -ErrorAction Stop | Out-Null
        $registered=$true
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
        while (-not (Test-Path -LiteralPath $resultPath)) {
            if ((Get-Date) -ge $deadline) { throw ('LocalService 模型访问验证超时：'+$Path) }
            Start-Sleep -Milliseconds 250
        }
        $result=Get-Content -LiteralPath $resultPath -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($result.sid -ne 'S-1-5-19' -or -not $result.ok) {
            throw ('LocalService 无法访问模型目录：'+$result.path+'；'+$result.error)
        }
        return $result
    } finally {
        if ($registered) {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        }
        foreach ($file in @($resultPath,$resultPath+'.tmp')) {
            if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
        }
    }
}

function Assert-ModelInventory([int]$ManifestFiles) {
    $backend=Invoke-RestMethod 'http://127.0.0.1:11435/api/tags' -TimeoutSec 30
    $gateway=Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 30
    if (-not $backend.PSObject.Properties['models'] -or -not $gateway.PSObject.Properties['models']) { throw '模型列表接口返回格式错误。' }
    $backendNames=@($backend.models | ForEach-Object { $_.name } | Sort-Object)
    $gatewayNames=@($gateway.models | ForEach-Object { $_.name } | Sort-Object)
    if ($ManifestFiles -gt 0 -and $backendNames.Count -eq 0) {
        throw '模型目录中存在模型清单，但 Ollama 后端返回空模型列表。请检查后端日志；安装未通过验证。'
    }
    if (($backendNames -join "`n") -cne ($gatewayNames -join "`n")) { throw '网关与 Ollama 后端的模型列表不一致，安装未通过验证。' }
    Write-Host ('已验证模型列表，模型数：'+$backendNames.Count)
}
