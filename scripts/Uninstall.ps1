param([switch]$AsUpgrade)
# Confirmation belongs to the graphical host; this engine never opens windows.
if (-not (Get-Variable context -ErrorAction SilentlyContinue)) { throw '请双击 Uninstall.exe。' }
Set-Progress '正在卸载服务并恢复原始启动项…'
try {
    $registration=$null
    if (Test-Path -LiteralPath $script:RegistrationPath) { $registration=Get-Content -LiteralPath $script:RegistrationPath -Encoding UTF8 -Raw | ConvertFrom-Json }
    Remove-Registrations
    if ($registration) { Restore-OriginalSettings $registration }
    # Verify the absolute target before recursive deletion. Models must never be inside it.
    $target=[IO.Path]::GetFullPath($script:InstallRoot).TrimEnd('\')
    $expected=[IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'OllamaStatusService')).TrimEnd('\')
    if ($target -ne $expected -or $target -eq [IO.Path]::GetPathRoot($target)) { throw '安装目录校验失败，已停止删除。' }
    if ($registration -and ($registration.models -eq $target -or $registration.models.StartsWith($target + '\',[StringComparison]::OrdinalIgnoreCase))) { throw '模型位于安装目录中，已保留整个目录。' }
    # Retain files if an executable did not stop; never schedule a broad delayed deletion.
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    if ($AsUpgrade) { return }
    Complete-Setup $true "卸载完成。原 Ollama 启动项已恢复，下次登录时生效；也可立即从开始菜单启动原 Ollama。`r`n`r`n模型未删除。日志和记录保留在：$script:StateRoot"
    exit 0
} catch {
    if ($AsUpgrade) { throw }
    Complete-Setup $false ('卸载未完全完成：' + $_.Exception.Message + "`r`n请保留安装包，解决提示问题后可再次运行卸载。"); exit 1
}
