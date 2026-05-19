@echo off
REM Reconcile Neon vs Pionex — 23:58 UTC. Usa venv local.
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 cloud\reconcile.py >> logs\reconcile.log 2>&1
exit /b %ERRORLEVEL%
