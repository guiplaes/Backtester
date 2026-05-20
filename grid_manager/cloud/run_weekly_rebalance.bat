@echo off
REM Weekly rebalance — cada dilluns 00:05 UTC. Reinverteix gridProfit + DCA configurat.
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 cloud\weekly_rebalance.py >> logs\weekly_rebalance.log 2>&1
exit /b %ERRORLEVEL%
