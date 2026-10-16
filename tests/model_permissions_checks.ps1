# Real NTFS ACLs and a real LocalService token; all files/tasks are isolated fixtures.
# Requires elevation. Does not touch installed Ollama services, configuration or models.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
. (Join-Path $Root 'scripts\Common.ps1')
if (-not (Test-Administrator)) { throw 'Run this test elevated.' }
$AclOnly=$false
function Assert([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message } }
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('OllamaModelPermissions-'+[guid]::NewGuid().ToString('N'))
$script:InstallRoot=Join-Path $fixture 'installed'
$script:StateRoot=Join-Path $fixture 'state'
$models=Join-Path $fixture '用户 中文 & Test\.ollama\models'
$manifestDir=Join-Path $models 'manifests\registry.ollama.ai\library\fixture'
$blobs=Join-Path $models 'blobs'
$journal=Join-Path $script:StateRoot 'backup\acl.jsonl'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$local=[Security.Principal.SecurityIdentifier]::new('S-1-5-19')
function Protect-Item([string]$Path) {
    $item=Get-Item -LiteralPath $Path
    $acl=if ($item.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
    $acl.SetAccessRuleProtection($true,$false)
    $flags=if ($item.PSIsContainer) { 'ContainerInherit,ObjectInherit' } else { 'None' }
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl',$flags,'None','Allow'))
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Local-Rules([string]$Path) {
    return @((Get-Acl -LiteralPath $Path).GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]) | Where-Object IdentityReference -eq $local)
}
try {
    New-Item -ItemType Directory -Path $manifestDir,$blobs,(Join-Path $script:InstallRoot 'scripts'),(Join-Path $script:StateRoot 'temp'),(Join-Path $script:StateRoot 'backup') -Force | Out-Null
    foreach ($name in @('ModelPermissions.ps1','Probe-ModelAccess.ps1')) { Copy-Item -LiteralPath (Join-Path $Root ('scripts\'+$name)) -Destination (Join-Path $script:InstallRoot 'scripts') }
    Grant-Directory $fixture 'S-1-5-19' 'ReadAndExecute'
    Grant-Directory (Join-Path $script:StateRoot 'temp') 'S-1-5-19' 'Modify'
    $manifest=Join-Path $manifestDir 'latest'
    $blob=Join-Path $blobs 'sha256-fixture'
    [IO.File]::WriteAllText($manifest,'{"schemaVersion":2}')
    [IO.File]::WriteAllText($blob,'keep-this-model-data')
    Protect-Item $models
    Protect-Item (Join-Path $models 'manifests')
    Protect-Item $blobs
    Protect-Item $manifest
    Protect-Item $blob
    # Preserve a pre-existing, narrower service-account rule through uninstall.
    $acl=Get-Acl -LiteralPath $blob
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($local,'Read','Allow'))
    Set-Acl -LiteralPath $blob -AclObject $acl
    $manifestHash=(Get-FileHash -LiteralPath $manifest).Hash
    $blobHash=(Get-FileHash -LiteralPath $blob).Hash
    $before=@{}
    foreach ($item in @(Get-ModelTree $models)) { $before[$item.FullName]=(Get-Acl -LiteralPath $item.FullName).Sddl }
    # Reproduce the old installer's behavior: the root grant cannot cross protected ACLs.
    Grant-Directory $models 'S-1-5-19' 'Modify'
    Assert (@(Local-Rules $blob | Where-Object { ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Modify) -eq [Security.AccessControl.FileSystemRights]::Modify }).Count -eq 0) 'Fixture unexpectedly inherited root grant'
    if (-not $AclOnly) {
        $failed=$false
        try { Test-ModelServiceAccess $models -TimeoutSeconds 30 | Out-Null } catch { $failed=$true; Write-Output ('Expected old-installer failure: '+$_.Exception.Message) }
        Assert $failed 'Old root-only grant unexpectedly worked'
    }
    $registration=[pscustomobject]@{models=$models;model_acl=$before[$models];startup_values=@();startup_links=@()}
    Restore-OriginalSettings $registration
    [IO.File]::WriteAllText($journal,'')
    $registration | Add-Member NoteProperty model_acl_journal $journal
    Grant-ModelPermissions $models $journal
    foreach ($path in @($models,$blobs,$manifest,$blob)) {
        Assert (@(Local-Rules $path | Where-Object { ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Modify) -eq [Security.AccessControl.FileSystemRights]::Modify }).Count -gt 0) ('Missing Modify: '+$path)
    }
    Assert ((Get-Acl -LiteralPath $manifest).AreAccessRulesProtected) 'Changed inheritance protection'
    $journalBefore=[IO.File]::ReadAllText($journal)
    Grant-ModelPermissions $models $journal
    Assert ([IO.File]::ReadAllText($journal) -eq $journalBefore) 'Repeated grant was not idempotent'
    Write-Output 'PASS root, protected directories/files, inheritance and idempotent ACL repair'
    if (-not $AclOnly) {
        $probe=Test-ModelServiceAccess $models -TimeoutSeconds 30
        Assert ($probe.ok -and $probe.sid -eq 'S-1-5-19' -and $probe.files -eq 2 -and $probe.manifests -eq 1) 'Incomplete real-account probe'
        Write-Output 'PASS real LocalService read/write/create/rename/delete probe'
        $acl=Get-Acl -LiteralPath $blob
        $deny=[Security.AccessControl.FileSystemAccessRule]::new($local,'ReadData','Deny')
        $acl.AddAccessRule($deny); Set-Acl -LiteralPath $blob -AclObject $acl
        $failed=$false
        try { Test-ModelServiceAccess $models -TimeoutSeconds 30 | Out-Null } catch { $failed=$_.Exception.Message.Contains($blob) }
        Assert $failed 'Explicit deny did not report the inaccessible file'
        $acl=Get-Acl -LiteralPath $blob; $acl.RemoveAccessRuleSpecific($deny); Set-Acl -LiteralPath $blob -AclObject $acl
        Write-Output 'PASS explicit deny fails with the affected path'
    }
    Assert ((Get-FileHash -LiteralPath $blob).Hash -eq $blobHash -and (Get-FileHash -LiteralPath $manifest).Hash -eq $manifestHash) 'Existing model data changed'
    Assert (@(Get-ChildItem -LiteralPath $models -Force -Recurse -Filter '.oss-access-*').Count -eq 0) 'Probe files left behind'
    # An unrelated ACL edit made after installation must survive uninstall.
    $otherRule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'Read','Allow')
    $acl=Get-Acl -LiteralPath $blob; $acl.AddAccessRule($otherRule); Set-Acl -LiteralPath $blob -AclObject $acl
    $expected=[Security.AccessControl.FileSecurity]::new()
    $expected.SetSecurityDescriptorSddlForm($before[$blob]); $expected.AddAccessRule($otherRule)
    $before[$blob]=$expected.Sddl
    Restore-OriginalSettings $registration
    foreach ($path in $before.Keys) { Assert ((Get-Acl -LiteralPath $path).Sddl -eq $before[$path]) ('ACL rollback mismatch: '+$path) }
    Write-Output 'PASS original LocalService rules, unrelated ACL edits and unchanged model bytes'
    # A failed ACL mutation must still have a durable rollback record.
    [IO.File]::WriteAllText($journal,'')
    function Set-Acl { throw 'fixture ACL write failure' }
    $failed=$false
    try { Grant-ModelPermissions $models $journal } catch { $failed=$true }
    Remove-Item Function:\Set-Acl
    Assert ($failed -and [IO.File]::ReadAllText($journal).Contains('sddl')) 'Failed mutation not journaled'
    Restore-OriginalSettings $registration
    Write-Output 'PASS failure journal written before ACL mutation'
    if (-not $AclOnly) {
        $junction=Join-Path $models 'linked'
        New-Item -ItemType Junction -Path $junction -Target (Join-Path $script:StateRoot 'temp') | Out-Null
        $failed=$false
        try { Grant-ModelPermissions $models $journal } catch { $failed=$_.Exception.Message.Contains($junction) }
        Remove-Item -LiteralPath $junction -Force
        Assert $failed 'Followed model junction outside the model root'
        Write-Output 'PASS junction boundary protection'
    }
} finally {
    $resolved=[IO.Path]::GetFullPath($fixture)
    $tempBase=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $resolved.StartsWith($tempBase,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($resolved).StartsWith('OllamaModelPermissions-')) { throw 'Unsafe fixture cleanup path' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
