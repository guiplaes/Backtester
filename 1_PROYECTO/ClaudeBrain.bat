@echo off
REM Launch Brain v3 (trader_brain.py) + its watchdog.
REM The watchdog ensures the brain is always running and cleans ghost state.
title ClaudeBrain
cd /d "%~dp0"
set "PYTHONPATH=C:\Users\Administrator\PythonPackages"

REM Ensure TradingView is running with CDP port 9223 before starting the brain.
echo Ensuring TradingView CDP bridge...
"C:\Program Files\Python312\python.exe" "%~dp0diag.py" --force-restart
if errorlevel 1 (
  echo TradingView CDP is not ready. Brain not started.
  timeout /t 5 >nul
  exit /b 1
)

REM Kill any stale instances before launching
taskkill /F /FI "IMAGENAME eq pythonw.exe" /FI "WINDOWTITLE eq ClaudeBrain*" >nul 2>&1

REM Start the brain
start "" "C:\Program Files\Python312\pythonw.exe" "%~dp0trader_brain.py"

REM Start the dashboard server
start "" "C:\Program Files\Python312\pythonw.exe" "%~dp0brain_flow.py"

REM Start the watchdog in daemon mode (loops every 60s).
REM For extra resilience run install_brain_watchdog_task.ps1 as Administrator
REM to register a Windows scheduled task as backup.
start "" "C:\Program Files\Python312\pythonw.exe" "%~dp0brain_watchdog.py" --daemon

REM Start the web console (ttyd + Cloudflare tunnel).
REM URL appears in C:\Tools\webconsole\tunnel.log; show with show_url.bat.
call "C:\Tools\webconsole\start_console.bat"

echo Brain v3 launched. Dashboard: http://localhost:5858/
timeout /t 3 >nul
