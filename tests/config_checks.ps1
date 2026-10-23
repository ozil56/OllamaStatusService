# Evaluate only the installer's XML string templates; no installation code is run.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
. (Join-Path $Root 'scripts\Common.ps1')
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('OllamaStatusXml-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot,(Join-Path $testRoot 'logs') -Force | Out-Null
$sourceExe='D:\工具 & Ollama\ollama.exe'
$sourceExeXml=[Security.SecurityElement]::Escape($sourceExe)
$sourceDirXml=[Security.SecurityElement]::Escape((Split-Path -Parent $sourceExe))
$environmentXml='<env name="OLLAMA_NUM_PARALLEL" value="1"/>'
$models='D:\模型 & Data\models'
$modelXml=[Security.SecurityElement]::Escape($models)
$logs=[Security.SecurityElement]::Escape((Join-Path $testRoot 'logs'))
$tempXml=[Security.SecurityElement]::Escape((Join-Path $testRoot 'temp'))
$profileXml=[Security.SecurityElement]::Escape((Join-Path $testRoot 'profile'))
$configXml=[Security.SecurityElement]::Escape((Join-Path $testRoot '配置 & config.json'))
$downloadConfigXml=[Security.SecurityElement]::Escape((Join-Path $testRoot 'download-network.json'))
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'scripts\Setup.ps1'),[ref]$tokens,[ref]$errors)
foreach($name in @('commonXml','backendXml','gatewayXml')) {
    $assignment=$ast.Find({param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and $node.Left.VariablePath.UserPath -eq $name},$true)
    if (-not $assignment) { throw ('Missing XML template: ' + $name) }
    Set-Variable -Name $name -Value (Invoke-Expression $assignment.Right.Extent.Text)
}
[xml]$backend=$backendXml
[xml]$gateway=$gatewayXml
if (($backend.service.env | Where-Object name -eq 'OLLAMA_MODELS').value -ne $models) { throw 'Model XML escaping failed' }
if ($backend.service.executable -ne $sourceExe) { throw 'Existing Ollama path was not preserved' }
if (($backend.service.env | Where-Object name -eq 'OLLAMA_HOST').value -ne '127.0.0.1:11435') { throw 'Backend must be loopback only' }
if ($gateway.service.depend -ne $script:BackendName) { throw 'Backend dependency is missing' }
if ($gateway.service.serviceaccount.user -ne 'LocalService') { throw 'Wrong service identity' }
if (-not $gateway.service.arguments.Contains('配置 & config.json')) { throw 'Config XML escaping failed' }
foreach($document in @($backend,$gateway)) {
    $document.service.id='OllamaStatusConfigProbe'+[guid]::NewGuid().ToString('N')
    $document.service.name=$document.service.id
    $document.Save((Join-Path $testRoot 'Probe.xml'))
    Copy-Item -LiteralPath (Join-Path $Root 'bin\WinSW.exe') -Destination (Join-Path $testRoot 'Probe.exe') -Force
    $result=& (Join-Path $testRoot 'Probe.exe') status
    if ($result -notcontains 'NonExistent') { throw ('WinSW failed to read generated configuration: ' + ($result -join ' ')) }
}
$aclFolder=Join-Path $testRoot 'acl-model-test'
New-Item -ItemType Directory -Path $aclFolder | Out-Null
$before=Get-Acl -LiteralPath $aclFolder
$beforeAccess=$before.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
Grant-Directory $aclFolder 'S-1-5-19' 'Modify'
$withGrant=Get-Acl -LiteralPath $aclFolder
if ($withGrant.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access) -eq $beforeAccess) { throw 'Service account ACL was not applied' }
$fake=[pscustomobject]@{startup_values=@();startup_links=@();models=$aclFolder;model_acl=$before.Sddl}
Restore-OriginalSettings $fake
$after=Get-Acl -LiteralPath $aclFolder
if ($after.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access) -ne $beforeAccess) { throw 'Model ACL restoration did not round-trip' }
Write-Output 'Backend/gateway XML, Unicode paths, WinSW config loading and temporary-folder ACL grant/restore passed.'
# Remove only this test's known temporary directory, after verifying its resolved boundary.
$resolved=[IO.Path]::GetFullPath($testRoot)
$base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
if ($resolved.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved).StartsWith('OllamaStatusXml-')) {
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
