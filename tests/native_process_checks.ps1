param([string]$Root=(Split-Path -Parent $PSScriptRoot))
. (Join-Path $Root 'scripts\Common.ps1')
$python=Join-Path $Root 'runtime\python.exe'
$arguments=@('中文 & space','C:\trailing slash\','embedded "quote"','slash\"quote','','%PATH%','$literal;value')
$code='import ctypes,json,sys; print(json.dumps({"console":ctypes.windll.kernel32.GetConsoleWindow(),"args":sys.argv[1:]}))'
$output=Invoke-Native $python (@('-c',$code)+$arguments)
$result=$output | ConvertFrom-Json
if ($result.console -ne 0) { throw 'Native helper created a console' }
if ($result.args.Count -ne $arguments.Count) { throw 'Argument count mismatch' }
for ($i=0;$i -lt $arguments.Count;$i++) { if ($result.args[$i] -cne $arguments[$i]) { throw ('Argument quoting mismatch at index '+$i) } }
$failed=$false
try { Invoke-Native $python @('-c','import sys; sys.stderr.write("EXPECTED_CHILD_FAILURE"); sys.exit(7)') | Out-Null }
catch { if ($_.Exception.Message -like '*7*EXPECTED_CHILD_FAILURE*') { $failed=$true } else { throw } }
if (-not $failed) { throw 'Native child failure was not propagated' }
# Both pipes exceed their buffer; must finish without blocking one another.
$large=Invoke-Native $python @('-c','import sys; sys.stderr.write("e"*100000); sys.stdout.write("o"*100000)') 6>$null
if ($large.Length -ne 100000) { throw 'Large child output was truncated' }
Write-Output 'PASS: native commands have no console; Unicode/space/quote/empty arguments preserved; errors propagated; concurrent stdout/stderr drained.'
