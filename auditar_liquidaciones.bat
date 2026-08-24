@echo off
REM ============================================================
REM  AUDITORIA DE LIQUIDACIONES - Bonos Bomberos CDELU
REM
REM  Compara los totales guardados de cada liquidacion contra las
REM  boletas que realmente tiene atadas. Sirve para cuando el
REM  historial muestra mas boletas o mas plata que el detalle.
REM
REM  Doble click y listo. Primero SOLO MUESTRA. No cambia nada
REM  hasta que vos escribas SI.
REM
REM  No hay que configurar nada: toma la URL de backup.bat.
REM ============================================================

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

REM ---- Tomar la URL desde backup.bat (una sola copia de la credencial) ----
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
    echo      Abrilo y fijate que tenga una linea asi:
    echo         set "DATABASE_URL=postgresql://...proxy.rlwy.net:PUERTO/railway"
    echo.
    pause
    exit /b 1
)

echo %DATABASE_URL% | findstr /C:"PEGAR_ACA" >nul
if not errorlevel 1 (
    echo.
    echo  [!] backup.bat todavia tiene la URL de ejemplo sin reemplazar.
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

REM ---- 1) Auditoria (solo lectura) ---------------------------
echo.
echo  Revisando las liquidaciones en Railway...
echo  ^(la primera consulta tarda unos segundos^)
echo.

REM El script imprime en pantalla Y guarda el informe en el .txt, asi que
REM aca NO se redirige: si no, la ventana queda en blanco hasta el final.
py -3.12 auditar_liquidaciones.py
set RESULTADO=%errorlevel%

if %RESULTADO% neq 0 (
    echo.
    echo  [!] Fallo la consulta. Si dice "No module named psycopg2", corre una vez:
    echo         py -3.12 -m pip install psycopg2-binary
    echo.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   El informe tambien quedo guardado en:
echo      auditoria_liquidaciones.txt
echo   ^(abrilo con el Bloc de notas si lo queres mandar^)
echo  ============================================================

findstr /C:" 0 desfasadas" auditoria_liquidaciones.txt | findstr /C:" 0 fantasma" >nul
if not errorlevel 1 (
    echo.
    echo   TODO EN ORDEN - no hay nada para corregir.
    echo.
    pause
    exit /b 0
)

REM ---- 2) Preguntar ------------------------------------------
echo.
echo  ============================================================
echo   Mira el resumen de arriba antes de seguir.
echo   Reparar reescribe los totales de cada liquidacion con lo
echo   que dicen sus boletas. Las boletas NO se tocan.
echo  ============================================================
echo.
set "SEGUIR="
set /p "SEGUIR=Corregir los totales? (escribi SI y Enter, o Enter para salir): "

if /i not "%SEGUIR%"=="SI" (
    echo.
    echo  No se cambio nada.
    echo.
    pause
    exit /b 0
)

REM ---- 3) Fantasmas ------------------------------------------
echo.
echo   Las FANTASMA son liquidaciones que quedaron con los numeros
echo   cargados pero sin ninguna boleta ^(la doble liquidacion^).
echo   Borrarlas saca del historial la plata duplicada.
echo   Solo se borran las que no tienen boletas ni cuotas extras
echo   ni numeros del pool CONTADO.
echo.
set "BORRAR="
set /p "BORRAR=Borrar tambien las fantasma? (SI / Enter para dejarlas): "

echo.
if /i "%BORRAR%"=="SI" (
    py -3.12 auditar_liquidaciones.py --reparar --borrar-fantasmas --yes
) else (
    py -3.12 auditar_liquidaciones.py --reparar --yes
)

echo.
echo  ============================================================
echo   Volve a hacer doble click aca para verificar que quedo
echo   en 0 desfasadas y 0 fantasma.
echo  ============================================================
echo.
pause
