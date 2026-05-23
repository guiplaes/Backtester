@echo off
REM Profit Harvester diari — extreu profits dels grids al vault USDT. 22:00 UTC.
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 -m vault.profit_harvester >> logs\vault_harvester.log 2>&1
exit /b %ERRORLEVEL%
