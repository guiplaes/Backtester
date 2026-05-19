@echo off
REM ===========================================
REM AUTO-SETUP per PC nou amb MT5 ja instal-lat
REM ===========================================
REM Pre-requisits:
REM  1) MT5 VTMarkets installat i compte demo logejat
REM  2) Python 3.10+ instal-lat
REM  3) Claude Code instal-lat
REM  4) Aquest repositori clonat (te files: tests/, EA/, scripts py)
REM ===========================================

echo === DualGrid Backtest Auto-Setup ===
echo.

REM 1) Localitzar MT5 install
set "MT5_INSTALL=C:\Program Files\VT Markets (Pty) MT5 Terminal"
if not exist "%MT5_INSTALL%\terminal64.exe" (
    set "MT5_INSTALL=C:\Program Files\VTMarkets MT5 Terminal"
)
if not exist "%MT5_INSTALL%\terminal64.exe" (
    echo ERROR: MT5 no trobat. Instal-la primer des de https://vtmarkets.com
    exit /b 1
)
echo OK: MT5 trobat a "%MT5_INSTALL%"

REM 2) Localitzar AppData hash del MT5
for /f "tokens=*" %%a in ('dir "%APPDATA%\MetaQuotes\Terminal" /b /ad ^| findstr /v "Common Community Help"') do (
    if exist "%APPDATA%\MetaQuotes\Terminal\%%a\origin.txt" (
        type "%APPDATA%\MetaQuotes\Terminal\%%a\origin.txt" | findstr /c:"VT Markets" >nul && set MT5_DATA=%APPDATA%\MetaQuotes\Terminal\%%a
    )
)
echo OK: MT5 data folder: %MT5_DATA%

REM 3) Crear 6 slots paral-lels (mes potent que aqui)
echo.
echo Creant 6 slots de backtest paral-lel (sera lent un cop, ~5 min)...
for %%s in (MT5_Tester MT5_Tester2 MT5_Tester3 MT5_Tester4 MT5_Tester5 MT5_Tester6) do (
    if not exist "C:\%%s\terminal64.exe" (
        echo - Copiant a C:\%%s ...
        robocopy "%MT5_INSTALL%" "C:\%%s" /MIR /MT:16 /NFL /NDL /NJH /NJS /NC /NS /R:1 /W:1 >nul
        echo - Copiant accounts.dat...
        copy "%MT5_DATA%\config\accounts.dat" "C:\%%s\config\accounts.dat" >nul
        copy "%MT5_DATA%\config\servers.dat" "C:\%%s\config\servers.dat" >nul 2>nul
    ) else (
        echo - C:\%%s ja existeix, salta
    )
)

REM 4) Sincronitzar EA a cada slot
echo.
echo Sincronitzant EA a slots...
for %%s in (MT5_Tester MT5_Tester2 MT5_Tester3 MT5_Tester4 MT5_Tester5 MT5_Tester6) do (
    if exist ".\MT5\MQL5\Experts\DualGridEA_v2_Reset.ex5" (
        copy ".\MT5\MQL5\Experts\DualGridEA_v2_Reset.ex5" "C:\%%s\MQL5\Experts\" >nul
    )
)

REM 5) Adaptar run_batch_parallel.py per 6 slots
echo.
echo Adaptant run_batch_parallel.py per 6 slots...
python -c "
import re
with open('1_PROYECTO/backtest_dualgrid_v2/run_batch_parallel.py','r',encoding='utf-8') as f: t=f.read()
new_slots = '''SLOTS = [
    Path(r\"C:\\\\MT5_Tester\"),
    Path(r\"C:\\\\MT5_Tester2\"),
    Path(r\"C:\\\\MT5_Tester3\"),
    Path(r\"C:\\\\MT5_Tester4\"),
    Path(r\"C:\\\\MT5_Tester5\"),
    Path(r\"C:\\\\MT5_Tester6\"),
]'''
t = re.sub(r'SLOTS = \[[^\]]+\]', new_slots, t, flags=re.DOTALL)
with open('1_PROYECTO/backtest_dualgrid_v2/run_batch_parallel.py','w',encoding='utf-8') as f: f.write(t)
print('OK')
"

echo.
echo === SETUP COMPLET ===
echo.
echo Per llancar backtest:
echo   cd 1_PROYECTO\backtest_dualgrid_v2
echo   python run_batch_parallel.py "TEST_PATTERN*.ini"
echo.
echo Per parlar amb Claude i continuar:
echo   claude
echo.
pause
