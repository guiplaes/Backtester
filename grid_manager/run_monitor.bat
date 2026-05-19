@echo off
cd /d "C:\Users\Administrator\Desktop\MT4 Claude\grid_manager"
"C:\Program Files\Python312\pythonw.exe" monitor.py >> logs\scheduler_stdout.log 2>> logs\scheduler_stderr.log
exit /b %ERRORLEVEL%
