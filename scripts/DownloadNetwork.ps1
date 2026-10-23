# Installer helpers only. Existing services are never restarted by this module.
function Select-DownloadRelayPort([int[]]$Excluded) {
    $listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    try {
        $listener.Start()
        $port=$listener.LocalEndpoint.Port
        if ($port -in $Excluded) { throw '自动下载转发端口与现有服务冲突，请重试安装。' }
        return $port
    } finally { $listener.Stop() }
}

function New-DownloadNetworkConfig([string]$CallerSid,[int[]]$Excluded,$PreviousConfig=$null,[string]$PreviousProxy='') {
    $proxies=@()
    if ($PreviousConfig -and $PreviousConfig.PSObject.Properties['preserved_proxies']) {
        $proxies=@($PreviousConfig.preserved_proxies)
    } elseif ($PreviousProxy) { $proxies=@($PreviousProxy) }
    return [ordered]@{schema=1;port=(Select-DownloadRelayPort $Excluded);caller_sid=$CallerSid;preserved_proxies=$proxies}
}
