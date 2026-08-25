@echo off
REM ============================================================
REM  ASIGNAR BOLETAS HUERFANAS - Bonos Bomberos CDELU
REM
REM  Mete en la liquidacion de su vendedor las boletas que tienen
REM  socio cargado pero nunca pasaron por ninguna liquidacion.
REM
REM  Doble click y listo. Primero SOLO MUESTRA el plan.
REM  No cambia nada hasta que vos escribas SI.
REM
REM  No hay que configurar nada: toma la URL de backup.bat.
REM ============================================================

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

if not exist "backup.bat" (
    echo.
    echo  [!] No encuentro backup.bat en esta carpeta.
    echo      Configuralo primero: ahi va la URL de la base.
    echo.
    pause
    exit /b 1
)

set "DATABASE_URL="
for /f "usebackq delims=" %%A in (`findstr /b /c:"set " backup.bat ^| findstr /c:"DATABASE_URL="`) do call %%A

if not defined DATABASE_URL (
    echo.
    echo  [!] No pude leer la URL desde backup.bat.
    echo.
    pause
    exit /b 1
)

echo %DATABASE_URL% | findstr /C:"railway.internal" >nul
if not errorlevel 1 (
    echo.
    echo  [!] backup.bat tiene la URL INTERNA, que no funciona desde esta PC.
    echo      Necesitas DATABASE_PUBLIC_URL ^(host *.proxy.rlwy.net^).
    echo.
    pause
    exit /b 1
)

where py >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [!] No encuentro Python ^(comando "py"^). Instalalo desde python.org
    echo.
    pause
    exit /b 1
)

REM ---- 1) Mostrar el plan (no escribe nada) ------------------
echo.
echo  Buscando boletas con socio pero sin liquidacion...
echo.

py -3.12 asignar_huerfanas.py
if errorlevel 1 (
    echo.
    echo  [!] Fallo la consulta. Si dice "No module named psycopg2", corre una vez:
    echo         py -3.12 -m pip install psycopg2-binary
    echo.
    pause
    exit /b 1
)

findstr /C:"0 a asignar" asignar_huerfanas.txt >nul
if not errorlevel 1 (
    echo.
    echo   No hay nada para asignar.
    echo.
    pause
    exit /b 0
)

REM ---- 2) Preguntar ------------------------------------------
echo.
echo  ============================================================
echo   Mira el plan de arriba: a que liquidacion va cada numero
echo   y cuanto se le acredita a cada vendedor.
echo.
echo   NO se toca la condicion: las que estan en cobranza siguen
echo   en cobranza. Tampoco se tocan las cuotas ni el cobrador.
echo  ============================================================
echo.
set "SEGUIR="
set /p "SEGUIR=Asignarlas? (escribi SI y Enter, o Enter para salir): "

if /i not "%SEGUIR%"=="SI" (
    echo.
    echo  No se cambio nada.
    echo.
    pause
    exit /b 0
)

echo.
py -3.12 asignar_huerfanas.py --aplicar --yes

echo.
echo  ============================================================
echo   Ahora corre auditar_liquidaciones.bat para verificar que
echo   las liquidaciones sigan cuadrando.
echo  ============================================================
echo.
pause
