@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title ENTRIX - De la venta al acceso

rem ------------------------------------------------------------
rem ENTRIX: prepara un entorno Python local sin depender de una
rem ruta fija de otro usuario de Windows.
rem ------------------------------------------------------------
set "PY="

rem 1) Buscar un Python funcional disponible en PATH.
for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY (
        "%%P" -c "import sys; print(sys.executable)" >nul 2>&1
        if not errorlevel 1 set "PY=%%P"
    )
)

rem 2) Buscar instalaciones normales por usuario (incluye Python 3.11+).
if not defined PY for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
    if not defined PY if exist "%%D\python.exe" (
        "%%D\python.exe" -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=%%D\python.exe"
    )
)

rem 3) Buscar instalaciones normales para todos los usuarios.
if not defined PY for /d %%D in ("%ProgramFiles%\Python*") do (
    if not defined PY if exist "%%D\python.exe" (
        "%%D\python.exe" -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=%%D\python.exe"
    )
)

if not defined PY goto no_python

echo Python detectado en:
echo %PY%

rem Si el .venv fue creado con una ruta de Python que ya no existe,
rem se elimina y se crea nuevamente con el Python detectado arriba.
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo El entorno virtual anterior no es compatible con esta PC.
        echo Creando un entorno nuevo...
        rmdir /s /q .venv
    )
)

if not exist .venv\Scripts\python.exe (
    echo Creando entorno virtual ENTRIX...
    "%PY%" -m venv .venv
)
if not exist .venv\Scripts\python.exe goto error

.venv\Scripts\python.exe -c "import flask, waitress" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias de ENTRIX. Esto puede tardar unos minutos...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -c "import flask, waitress" >nul 2>&1
if errorlevel 1 goto dependencies_error

start "" /b .venv\Scripts\pythonw.exe "%~dp0abrir_navegador.py"
.venv\Scripts\python.exe "%~dp0server.py"
pause
exit /b

:no_python
echo.
echo ERROR: No se encontro una instalacion funcional de Python.
echo.
echo Instala Python 3.11 o superior y marca "Add Python to PATH".
echo Luego vuelve a ejecutar INICIAR.bat.
echo.
pause
exit /b 1

:dependencies_error
echo.
echo ERROR: No se pudieron instalar las dependencias de ENTRIX.
echo Revisa tu conexion a Internet y vuelve a ejecutar INICIAR.bat.
echo.
pause
exit /b 1

:error
echo.
echo ERROR: No se pudo crear el entorno Python de ENTRIX.
echo Revisa la instalacion de Python y vuelve a iniciar.
echo.
pause
exit /b 1
