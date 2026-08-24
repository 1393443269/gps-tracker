# Kill existing processes on port 8080
$conns = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
if ($conns) {
    $pids = ($conns | Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique)
    foreach ($p in $pids) {
        Write-Host "Killing PID $p"
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# Start backend via Python launcher script (avoids Unicode path issues with Start-Process)
$launchScript = @"
import subprocess, sys
proc = subprocess.Popen(
    [r'C:\Python314\python.exe', 'app.py'],
    cwd=r'C:\Users\admin\Agent工作区\dome\server',
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
)
print(f'Started PID {proc.pid}')
"@

& "C:\Python314\python.exe" -c $launchScript
Write-Host "Backend started."
