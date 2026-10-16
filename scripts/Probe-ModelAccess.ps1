param([Parameter(Mandatory=$true)][string]$Models, [Parameter(Mandatory=$true)][string]$ResultPath)
# Run by a short-lived scheduled task under the actual LocalService account.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'ModelPermissions.ps1')
$result=[ordered]@{ok=$false;sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;path=$Models;error='';files=0;directories=0;manifests=0}
$scratch=$null; $renamed=$null
try {
    if ($result.sid -ne 'S-1-5-19') { throw '验证必须以 LocalService 身份执行。' }
    $manifestRoot=[IO.Path]::GetFullPath((Join-Path $Models 'manifests')).TrimEnd('\')+'\'
    foreach ($required in @('blobs','manifests')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Models $required) -PathType Container)) { throw ('缺少或无法读取目录：'+(Join-Path $Models $required)) }
    }
    foreach ($item in (Get-ModelTree $Models)) {
        $result.path=$item.FullName
        if ($item.PSIsContainer) {
            $result.directories++
            $scratch=Join-Path $item.FullName ('.oss-access-'+[guid]::NewGuid().ToString('N'))
            $renamed=$scratch+'.renamed'
            $stream=[IO.File]::Open($scratch,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
            try { $stream.WriteByte(79); $stream.Position=0; if ($stream.ReadByte() -ne 79) { throw '读写验证失败。' } }
            finally { $stream.Dispose() }
            [IO.File]::Move($scratch,$renamed)
            [IO.File]::Delete($renamed)
            $scratch=$null; $renamed=$null
        } else {
            # Open existing data without modifying or reading full model blobs.
            $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
            try { [void]$stream.ReadByte() } finally { $stream.Dispose() }
            $result.files++
            if ($item.FullName.StartsWith($manifestRoot,[StringComparison]::OrdinalIgnoreCase)) { $result.manifests++ }
        }
    }
    $result.ok=$true
} catch { $result.error=$_.Exception.Message }
finally {
    foreach ($file in @($scratch,$renamed)) {
        if ($file -and [IO.File]::Exists($file)) { try { [IO.File]::Delete($file) } catch {} }
    }
    $json=ConvertTo-Json -InputObject $result -Compress
    [IO.File]::WriteAllText($ResultPath+'.tmp',$json,[Text.UTF8Encoding]::new($false))
    [IO.File]::Move($ResultPath+'.tmp',$ResultPath)
}
if (-not $result.ok) { exit 1 }
