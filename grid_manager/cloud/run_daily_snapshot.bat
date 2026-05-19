@echo off
REM Daily snapshot a Neon — 23:55 UTC. Usa venv local (independent de UAC).
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 cloud\daily_snapshot.py >> logs\daily_snapshot.log 2>&1
exit /b %ERRORLEVEL%
