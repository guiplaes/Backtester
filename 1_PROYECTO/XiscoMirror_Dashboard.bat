@echo off
REM Engega el dashboard visual de XiscoMirror (tkinter, finestra Windows nativa)
cd /d "%~dp0"
start "" pythonw.exe xisco_dashboard.py
exit /b 0
