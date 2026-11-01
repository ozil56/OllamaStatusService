# Real compiled processes and real Windows directory handles; no services/UAC.
param([string]$Root=(Split-Path -Parent $PSScriptRoot),[string]$BaselineExe='')
$ErrorActionPreference='Stop'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('oss-cwd-check-中文 & '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
function Assert($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Check-Worker([string]$Executable,[string]$Label,[bool]$ExpectLocked) {
    $caseRoot=Join-Path $fixture $Label
    $installed=Join-Path $caseRoot 'installed'
    $extracted=Join-Path $caseRoot 'empty-extracted-package'
    New-Item -ItemType Directory -Path (Join-Path $installed 'scripts'),$extracted -Force | Out-Null
    Copy-Item -LiteralPath $Executable -Destination (Join-Path $installed 'OllamaTray.exe')
    $body=@'
param([string]$Action)
$root=Split-Path -Parent $PSScriptRoot
@{cwd=[Environment]::CurrentDirectory;location=$PWD.Path} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'ready.json') -Encoding UTF8
$deadline=[datetime]::UtcNow.AddSeconds(15)
while(-not (Test-Path -LiteralPath (Join-Path $root 'release')) -and [datetime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 50}
'@
    [IO.File]::WriteAllText((Join-Path $installed 'scripts\Manage.ps1'),$body,[Text.UTF8Encoding]::new($true))
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $installed 'OllamaTray.exe'; $info.Arguments='--manage-worker Start'
    $info.WorkingDirectory=$extracted; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $process=[Diagnostics.Process]::Start($info)
    try {
        $deadline=[datetime]::UtcNow.AddSeconds(10)
        while(-not (Test-Path -LiteralPath (Join-Path $installed 'ready.json')) -and [datetime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 50}
        Assert (Test-Path -LiteralPath (Join-Path $installed 'ready.json')) 'Fixture worker did not start'
        Assert (-not $process.HasExited) 'Fixture exited before directory test'
        Assert (@(Get-ChildItem -LiteralPath $extracted -Force).Count -eq 0) 'Extracted fixture must be empty'
        $locked=$false
        try { [IO.Directory]::Delete($extracted) } catch [IO.IOException] { $locked=$true }
        Assert ($locked -eq $ExpectLocked) ('Unexpected directory lock: '+$Label)
        if (-not $ExpectLocked) {
            $result=Get-Content -LiteralPath (Join-Path $installed 'ready.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            Assert ($result.cwd.TrimEnd('\') -eq $installed) 'Worker retained inherited current directory'
            Assert ($result.location.TrimEnd('\') -eq $installed) 'PowerShell retained inherited location'
        }
        Write-Output ('PASS '+$Label+': empty package directory locked='+$locked+' while worker is alive')
    } finally {
        [IO.File]::WriteAllText((Join-Path $installed 'release'),'done')
        if (-not $process.WaitForExit(10000)) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
}
try {
    if ($BaselineExe) { Check-Worker $BaselineExe 'baseline-1.6.2' $true }
    Check-Worker (Join-Path $Root 'OllamaTray.exe') 'fixed' $false
    foreach ($entry in @(@('Install.exe','SetupHost'),@('Open-Tray.exe','TrayLauncher'))) {
        $assembly=[Reflection.Assembly]::LoadFile((Join-Path $Root $entry[0]))
        $method=$assembly.GetType($entry[1]).GetMethod('TrayStartInfo',[Reflection.BindingFlags]'NonPublic,Static')
        Assert ([bool]$method) ('Missing launcher factory: '+$entry[0])
        $target=Join-Path $fixture 'installed\OllamaTray.exe'
        $arguments=New-Object object[] 1
        $arguments[0]=[string]$target
        $info=$method.Invoke($null,$arguments)
        Assert ($info.FileName -eq $target -and $info.WorkingDirectory -eq (Split-Path -Parent $target)) ('Launcher inherited package directory: '+$entry[0])
        Write-Output ('PASS installed working directory: '+$entry[0])
    }
} finally {
    $resolved=[IO.Path]::GetFullPath($fixture)
    $base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $resolved.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.Path]::GetFileName($resolved).StartsWith('oss-cwd-check-')) { throw 'Unsafe cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
