@echo off
REM XiscoMirror — copy-trader del canal "Senales Xisco Analisis"
REM Engega xisco_mirror.py en background (sense finestra) i surt.

cd /d "%~dp0"

REM Si ja hi ha una instancia corrent, no en llencem una altra
wmic process where "name='pythonw.exe' and commandline like '%%xisco_mirror.py%%'" get processid 2>nul | findstr /R "[0-9]" >nul
if %errorlevel%==0 (
    REM Ja n'hi ha una corrent — no fem res
    exit /b 0
)

start "" /B pythonw.exe xisco_mirror.py
exit /b 0
