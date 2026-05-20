@echo off
REM Sync health → Neon (cada 5 min). Wallet, monitor heartbeat, log tail, prices.
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 cloud\sync_health.py >> logs\sync_health.log 2>&1
exit /b %ERRORLEVEL%
