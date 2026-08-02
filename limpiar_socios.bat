@echo off
REM ============================================================
REM  LIMPIAR SOCIOS FANTASMA - Bonos Bomberos CDELU
REM
REM  Borra los socios que quedaron sin ninguna boleta por los
REM  reintentos del "Error de conexion".
REM
REM  Primero LISTA lo que encontro. No borra nada sin que confirmes.
REM
REM  No hay que configurar nada: toma la URL de backup.bat.
REM ============================================================

chcp 65001 >nul 2>&1
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

REM Busca la linea que empieza con "set" y contiene DATABASE_URL, y la ejecuta.
REM Las lineas de ayuda que muestran ejemplos empiezan con "echo", asi que /b
REM (comienzo de linea) las descarta y no se confunde con ellas.
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

REM ---- 1) Listar ---------------------------------------------
echo.
echo  Buscando socios sin boleta...
echo.
py -3.12 limpiar_socios_huerfanos.py
if errorlevel 1 (
    echo.
    echo  [!] Fallo la consulta. Si dice "No module named psycopg2":
    echo         py -3.12 -m pip install psycopg2-binary
    echo.
    pause
    exit /b 1
)

REM ---- 2) Preguntar ------------------------------------------
echo.
echo  ============================================================
echo   Revisa la lista de arriba, sobre todo el grupo
echo   "REVISAR A MANO" si aparecio alguno.
echo  ============================================================
echo.
set "SEGUIR="
set /p "SEGUIR=Borrar los socios sin boleta? (escribi SI y Enter, o Enter para salir): "

if /i not "%SEGUIR%"=="SI" (
    echo.
    echo  No se borro nada.
    echo.
    pause
    exit /b 0
)

REM ---- 3) Borrar ---------------------------------------------
echo.
py -3.12 limpiar_socios_huerfanos.py --borrar
echo.
pause
