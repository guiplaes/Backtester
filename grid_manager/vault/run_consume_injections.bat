@echo off
REM Consume injection_queue → vault_inventory. Cron cada 60s via Task Scheduler.
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 -m vault.consume_injections >> logs\vault_consume.log 2>&1
exit /b %ERRORLEVEL%
