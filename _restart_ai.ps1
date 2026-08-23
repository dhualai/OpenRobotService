$conns = Get-NetTCPConnection -LocalPort 8401 -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Output "PORT_FREE"
    exit 0
}
$procIds = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $procIds) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    Write-Output ("Found PID={0} Name={1}" -f $procId, $proc.ProcessName)
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
$remain = Get-NetTCPConnection -LocalPort 8401 -State Listen -ErrorAction SilentlyContinue
if ($remain) { Write-Output "PORT_STILL_BUSY" } else { Write-Output "PORT_FREE" }
