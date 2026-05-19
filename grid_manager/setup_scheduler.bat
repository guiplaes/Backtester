@echo off
REM Setup Windows Task Scheduler for Grid Manager
REM Run this AS ADMINISTRATOR

set BASE=C:\Users\Administrator\Desktop\MT4 Claude\grid_manager
set PYTHON=python.exe

echo Setting up scheduled tasks...

REM Boundary monitor: every 5 minutes
schtasks /Create /F /TN "GridManager_Monitor" /TR "%PYTHON% \"%BASE%\monitor.py\"" ^
  /SC MINUTE /MO 5 /RL HIGHEST /RU SYSTEM

REM Daily check: 22:00 UTC (= 23:00 / 00:00 CET depending on DST)
REM Adjust local time as needed
schtasks /Create /F /TN "GridManager_Daily" /TR "%PYTHON% \"%BASE%\daily_check.py\"" ^
  /SC DAILY /ST 23:00 /RL HIGHEST /RU SYSTEM

echo.
echo Tasks created. To view:
echo   schtasks /Query /TN "GridManager_Monitor"
echo   schtasks /Query /TN "GridManager_Daily"
echo.
echo To remove later:
echo   schtasks /Delete /TN "GridManager_Monitor" /F
echo   schtasks /Delete /TN "GridManager_Daily" /F
echo.
pause
