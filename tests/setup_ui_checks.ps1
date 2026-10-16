# Runs the real GUI, worker and package verifier; no service/firewall mutations or UAC.
param([string]$Root=(Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
$evidence=Join-Path ([IO.Path]::GetTempPath()) ('OllamaStatusUI-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class SetupVisibleWindows {
    delegate bool Visitor(IntPtr h, IntPtr p);
    [DllImport("user32.dll")] static extern bool EnumWindows(Visitor cb, IntPtr p);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder s,int n);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
    public static long[] Consoles() {
        var result=new List<long>();
        EnumWindows(delegate(IntPtr h,IntPtr p) {
            if(IsWindowVisible(h)) {
                var name=new StringBuilder(256); GetClassName(h,name,name.Capacity);
                if(name.ToString()=="ConsoleWindowClass" || name.ToString()=="CASCADIA_HOSTING_WINDOW_CLASS") result.Add(h.ToInt64());
            }
            return true;
        },IntPtr.Zero); return result.ToArray();
    }
    public static int ForProcess(int id) {
        int n=0;
        EnumWindows(delegate(IntPtr h,IntPtr p) { uint pid; GetWindowThreadProcessId(h,out pid); if(pid==id && IsWindowVisible(h)) n++; return true; },IntPtr.Zero);
        return n;
    }
}
'@
function Invoke-GuiCheck([string]$Directory,[string]$Name,[string]$Mode,[string]$Label,[int]$ExpectedCode) {
    $json=Join-Path $evidence ($Label+'.json')
    $baseline=@([SetupVisibleWindows]::Consoles())
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $Directory $Name
    $info.Arguments=$Mode+' "'+$json+'"'
    $info.UseShellExecute=$true
    $p=[Diagnostics.Process]::Start($info)
    $null=$p.Handle
    $maxWindows=0; $samples=0; $newConsoles=@()
    $deadline=(Get-Date).AddSeconds(40)
    while (-not $p.HasExited -and (Get-Date) -lt $deadline) {
        $maxWindows=[Math]::Max($maxWindows,[SetupVisibleWindows]::ForProcess($p.Id))
        $newConsoles+=@([SetupVisibleWindows]::Consoles() | Where-Object { $_ -notin $baseline })
        $samples++
        Start-Sleep -Milliseconds 20
    }
    if (-not $p.HasExited) { $p.Kill(); throw "GUI test timed out: $Label; evidence: $evidence" }
    $p.WaitForExit()
    if ($p.ExitCode -ne $ExpectedCode) { throw "Unexpected exit code $($p.ExitCode): $Label; evidence: $evidence" }
    $p.Dispose()
    $result=Get-Content -LiteralPath $json -Encoding UTF8 -Raw | ConvertFrom-Json
    if (-not $result.visible -or -not $result.same_window -or $result.console -ne 0) { throw "GUI visibility/console/window identity failed: $Label" }
    if ($maxWindows -ne 1 -or $newConsoles.Count) { throw "Visible window count=$maxWindows, unexpected console samples=$($newConsoles.Count): $Label" }
    if ($Mode -eq '--test-verify') {
        if (-not (Select-String -LiteralPath (Join-Path $result.log_directory 'setup.log') -Pattern '^.*WORKER_CONSOLE_HANDLE=0$' -Quiet)) { throw "PowerShell worker had a console: $Label" }
        $outcome=Get-Content -LiteralPath (Join-Path $result.log_directory 'result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($outcome.success -ne ($ExpectedCode -eq 0)) { throw "Worker outcome did not reach UI: $Label" }
    }
    Write-Output ("PASS {0}: one visible GUI, no console, same window; {1} external samples." -f $Label,$samples)
}
Invoke-GuiCheck $Root '安装.exe' '--test-selection' 'install-selection' 0
$selection=Get-Content -LiteralPath (Join-Path $evidence 'install-selection.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$hasInstall=(Test-Path 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\OllamaStatusGateway') -or (Test-Path 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\OllamaStatusBackend')
if ($selection.upgrade_mode -ne $hasInstall) { throw 'Existing installation detection failed' }
if ($hasInstall -and ($selection.primary_text -ne '升级到 1.2.3' -or -not $selection.primary_enabled)) { throw 'Upgrade action unavailable for existing installation' }
if ($hasInstall) {
    [xml]$installedXml=Get-Content -LiteralPath (Join-Path $env:ProgramFiles 'OllamaStatusService\OllamaStatusBackend.xml') -Encoding UTF8 -Raw
    $expectedModels=($installedXml.service.env | Where-Object name -eq 'OLLAMA_MODELS').value
    if ($selection.model_path -ne $expectedModels -or $selection.model_source -notlike '*现有服务配置*') { throw 'GUI did not show the active model path and its source' }
}
Invoke-GuiCheck $Root '卸载.exe' '--test-selection' 'uninstall-selection' 0
Invoke-GuiCheck $Root '安装.exe' '--test-verify' 'verify-success' 0
# A separate incomplete copy exercises the real error path without changing the package.
$bad=Join-Path $evidence '损坏包 & 空格'
New-Item -ItemType Directory -Path $bad,(Join-Path $bad 'scripts') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Root '安装.exe') -Destination $bad
foreach ($name in @('Common.ps1','SetupWorker.ps1')) { Copy-Item -LiteralPath (Join-Path $Root ('scripts\'+$name)) -Destination (Join-Path $bad 'scripts') }
[IO.File]::WriteAllText((Join-Path $bad 'manifest.sha256.json'),'[{"path":"缺失文件.cmd","sha256":"'+('0'*64)+'"}]',[Text.UTF8Encoding]::new($true))
Invoke-GuiCheck $bad '安装.exe' '--test-verify' 'verify-failure' 1
$failure=Get-Content -LiteralPath (Join-Path $evidence 'verify-failure.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($failure.message -notlike '*缺失文件.cmd*') { throw 'Chinese error text was not preserved' }
Write-Output ('Evidence and rendered GUI images: ' + $evidence)
