@echo off
REM Atura xisco_mirror.py (mata nomes el pythonw que executa aquest script)
cd /d "%~dp0"

for /f "tokens=2" %%i in ('wmic process where "name='pythonw.exe' and commandline like '%%xisco_mirror.py%%'" get processid /value ^| findstr "="') do (
    taskkill /F /PID %%i >nul 2>&1
)
exit /b 0
