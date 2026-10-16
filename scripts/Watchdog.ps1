. (Join-Path $PSScriptRoot 'Common.ps1')
$service=Get-Service -Name $script:GatewayName -ErrorAction SilentlyContinue
if (-not $service -or $service.Status -ne 'Running') { exit 0 }
for ($attempt=0; $attempt -lt 3; $attempt++) {
    try { $health=Invoke-RestMethod 'http://127.0.0.1:11434/healthz' -TimeoutSec 4; if ($health.ok -and $health.service -eq 'OllamaStatusService') { exit 0 } } catch {}
    Start-Sleep -Seconds 2
}
# The data journal survives this restart. Never erase unknown requests here.
$service=Get-Service -Name $script:GatewayName -ErrorAction SilentlyContinue
if ($service -and $service.Status -eq 'Running') { Restart-Service -Name $script:GatewayName -Force }
