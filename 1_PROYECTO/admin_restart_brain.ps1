$ErrorActionPreference = "Stop"

$BaseDir = "C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO"
$LogsDir = Join-Path $BaseDir "logs"
$LogFile = Join-Path $LogsDir "admin_restart.log"
$PythonExe = "C:\Program Files\Python312\python.exe"
$BatchFile = Join-Path $BaseDir "ClaudeBrain.bat"
$PidFile = Join-Path $LogsDir "trader_brain.pid"

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

function Write-AdminLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "$ts $Message"
}

Write-AdminLog "=== elevated restart start ==="

$targets = @("trader_brain.py", "brain_flow.py", "brain_watchdog.py")
$targetRegex = [string]::Join("|", ($targets | ForEach-Object { [regex]::Escape($_) }))

$procs = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^python(w)?\.exe$') -and
    ($_.CommandLine) -and
    ($_.CommandLine -match $targetRegex)
}

foreach ($proc in $procs) {
    try {
        Write-AdminLog "Stopping PID=$($proc.ProcessId) $($proc.Name) :: $($proc.CommandLine)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-AdminLog "Stop failed PID=$($proc.ProcessId): $($_.Exception.Message)"
    }
}

Start-Sleep -Seconds 3

try {
    $listeners = Get-NetTCPConnection -LocalPort 5858 -State Listen -ErrorAction Stop
    foreach ($listener in $listeners) {
        try {
            Write-AdminLog "Stopping dashboard listener PID=$($listener.OwningProcess)"
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
        } catch {
            Write-AdminLog "Dashboard stop failed PID=$($listener.OwningProcess): $($_.Exception.Message)"
        }
    }
} catch {
    Write-AdminLog "No dashboard listener on 5858"
}

Remove-Item -Force -ErrorAction SilentlyContinue $PidFile

Write-AdminLog "Launching ClaudeBrain.bat"
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$BatchFile`"" -WorkingDirectory $BaseDir -WindowStyle Hidden

Start-Sleep -Seconds 12

Write-AdminLog "Post-launch process snapshot:"
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^python(w)?\.exe$') -and
    ($_.CommandLine) -and
    ($_.CommandLine -match $targetRegex)
} | ForEach-Object {
    Write-AdminLog "Alive PID=$($_.ProcessId) $($_.Name) :: $($_.CommandLine)"
}

Write-AdminLog "=== elevated restart end ==="
