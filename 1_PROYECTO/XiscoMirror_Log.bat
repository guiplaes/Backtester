@echo off
REM Obre el log de XiscoMirror en directe (Get-Content -Wait)
cd /d "%~dp0"
title XiscoMirror Log
powershell -NoExit -Command "Get-Content '%~dp0logs\xisco_mirror.log' -Wait -Encoding UTF8 -Tail 30"
