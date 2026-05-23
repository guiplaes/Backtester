@echo off
REM Monitor cada 5 min — usa venv del projecte (amb psycopg + tots paquets correctes).
REM ABANS: utilitzava Python global sense psycopg → import vault.closer fallava
REM → VAULT_LIVE_ASSETS quedava buit → monitor NO saltava els bots gestionats per vault.
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 monitor.py >> logs\monitor_cron.log 2>&1
exit /b %ERRORLEVEL%
