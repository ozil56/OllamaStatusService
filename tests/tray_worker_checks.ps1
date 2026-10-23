# Executes the compiled GUI worker with harmless scripts in an isolated folder.
# No UAC, service operations, official installers or production changes.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('oss-tray-worker-中文 & '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $fixture 'scripts') -Force | Out-Null
try {
    $exe=Join-Path $fixture 'OllamaTray.exe'
    Copy-Item -LiteralPath (Join-Path $Root 'OllamaTray.exe') -Destination $exe
    $probe=@'
param([string]$Action='', [string]$TargetVersion='', [switch]$Recover)
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class TrayProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
$value=@{console=[TrayProbe]::GetConsoleWindow().ToInt64();action=$Action;target=$TargetVersion;recover=[bool]$Recover}
$value | ConvertTo-Json | Set-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'probe.json') -Encoding UTF8
'@
    foreach ($script in @('Manage.ps1','OllamaUpdate.ps1')) {
        [IO.File]::WriteAllText((Join-Path $fixture ('scripts\'+$script)),$probe,[Text.UTF8Encoding]::new($true))
    }
    foreach ($case in @(@('--manage-worker','Start'),@('--manage-worker','Stop'),@('--manage-worker','Recover'),@('--update-worker','0.49.0'),@('--update-worker','recover'))) {
        $info=New-Object Diagnostics.ProcessStartInfo
        $info.FileName=$exe; $info.Arguments=$case -join ' '
        $info.UseShellExecute=$false; $info.CreateNoWindow=$true
        $process=[Diagnostics.Process]::Start($info)
        try {
            if (-not $process.WaitForExit(20000)) { $process.Kill(); throw 'Fixture GUI worker timed out' }
            if ($process.ExitCode -ne 0) { throw 'Fixture GUI worker failed' }
        } finally { $process.Dispose() }
        $result=Get-Content -LiteralPath (Join-Path $fixture 'probe.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($result.console -ne 0) { throw 'PowerShell acquired a console window' }
        if ($case[0] -eq '--manage-worker' -and $result.action -ne $case[1]) { throw 'Manage action changed' }
        if ($case[0] -eq '--update-worker') {
            if ($case[1] -eq 'recover' -and -not $result.recover) { throw 'Recovery argument lost' }
            if ($case[1] -ne 'recover' -and $result.target -ne $case[1]) { throw 'Target version lost' }
        }
        Remove-Item -LiteralPath (Join-Path $fixture 'probe.json')
        Write-Output ('PASS GUI worker without console: '+($case -join ' '))
    }
} finally {
    $resolved=[IO.Path]::GetFullPath($fixture)
    $tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $resolved.StartsWith($tempRoot,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($resolved).StartsWith('oss-tray-worker-')) { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
