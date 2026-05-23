@echo off
REM Vault Closer — scan breakouts cada 5 min. SHADOW per defecte.
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -X utf8 -m vault.closer >> logs\vault_closer.log 2>&1
exit /b %ERRORLEVEL%
