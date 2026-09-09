@echo off
REM ============================================================
REM  REVISAR COBRADOR - Bonos Bomberos CDELU
REM
REM  Muestra, para un cobrador y un mes, la cobranza por planilla
REM  con la REGLA VIEJA y con el CORTE DE PASE DE NUMEROS, las
REM  entregas del periodo y el saldo con cada cuenta.
REM
REM  Solo lectura: no cambia nada en la base.
REM
REM  Uso:  revisar_cobrador.bat MABEL 8 2026
REM ============================================================
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "DATABASE_URL="
for /f "usebackq delims=" %%A in (`findstr /b /c:"set " backup.bat ^| findstr /c:"DATABASE_URL="`) do call %%A
if not defined DATABASE_URL (
    echo  [!] No pude leer la URL desde backup.bat.
    pause
    exit /b 1
)

set "COB=%~1"
if "%COB%"=="" set "COB=MABEL"
set "MES=%~2"
set "ANIO=%~3"

py -3.12 revisar_cobrador.py %COB% %MES% %ANIO%
echo.
pause
