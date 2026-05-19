# Install ClaudeBrainWatchdog scheduled task.
# Run this ONCE in an elevated PowerShell (Run as Administrator).
# The task runs brain_watchdog.py single-shot every 60s as backup if the
# daemon started by ClaudeBrain.bat dies.

$action = New-ScheduledTaskAction `
    -Execute 'C:\Program Files\Python312\pythonw.exe' `
    -Argument '"C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO\brain_watchdog.py"' `
    -WorkingDirectory 'C:\Users\Administrator\Desktop\MT4 Claude\1_PROYECTO'

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Seconds 60)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName 'ClaudeBrainWatchdog' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Brain v3 watchdog — ensures trader_brain.py is alive and cleans ghost signal state. Runs every 60s.' `
    -Force

# Legacy watchdog: disabled (kept disabled)
Disable-ScheduledTask -TaskName 'ClaudeTradingWatchdog' -ErrorAction SilentlyContinue | Out-Null

Write-Host "ClaudeBrainWatchdog task installed and running every 60s."
Write-Host "Check:  Get-ScheduledTaskInfo -TaskName ClaudeBrainWatchdog"
