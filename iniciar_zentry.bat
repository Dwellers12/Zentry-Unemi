@echo off
rem Zentry: levanta todo en Windows con doble clic (servidor web y camaras).
rem La primera vez crea el entorno .venv e instala las dependencias (tarda varios minutos).
setlocal
title Zentry
cd /d "%~dp0"
rem Python en UTF-8: asi las tildes de los mensajes salen bien.
set "PYTHONUTF8=1"
set "URL=http://127.0.0.1:8000/"

rem 1) Buscar Python 3.12 o superior (primero el lanzador "py", despues "python").
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY goto sin_python

rem 2) Entorno virtual con las dependencias: solo la primera vez o si cambia requirements.txt.
if exist ".venv\Scripts\python.exe" goto revisar_dependencias
echo Creando el entorno virtual .venv ...
%PY% -m venv .venv
if errorlevel 1 goto error

:revisar_dependencias
".venv\Scripts\python.exe" -c "import pathlib, sys; a = pathlib.Path('requirements.txt'); b = pathlib.Path('.venv/requisitos_instalados.txt'); sys.exit(0 if b.is_file() and b.read_bytes() == a.read_bytes() else 1)"
if not errorlevel 1 goto base
echo.
echo Instalando las dependencias. La primera vez tarda varios minutos: no cierres esta ventana.
echo.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
copy /y "requirements.txt" ".venv\requisitos_instalados.txt" >nul

rem 3) Base de datos: migraciones, datos de demostracion si esta vacia y token del agente.
rem    Si existe el archivo .env con la base de AWS (RDS), se usa esa; si no, SQLite.
:base
echo Preparando la base de datos...
".venv\Scripts\python.exe" Zentry\manage.py migrate --noinput -v 0
if errorlevel 1 goto error
".venv\Scripts\python.exe" Zentry\manage.py datos_demo --si-vacia
if not exist ".agente_token" ".venv\Scripts\python.exe" Zentry\manage.py crear_agente >nul

rem 4) Servidor web en su propia ventana; esperar a que responda (hasta 60 s).
echo Arrancando el servidor web...
start "Zentry - Servidor web" cmd /k .venv\Scripts\python.exe Zentry\manage.py runserver 127.0.0.1:8000
set /a INTENTOS=0
:esperar_web
".venv\Scripts\python.exe" -c "import urllib.request; urllib.request.urlopen('%URL%', timeout=2)" >nul 2>&1
if not errorlevel 1 goto camaras
set /a INTENTOS+=1
if %INTENTOS% geq 60 goto web_no_arranco
ping -n 2 127.0.0.1 >nul
goto esperar_web

rem 5) Camaras: el simulador (carpeta simulador\); si no esta, el agente para las camaras de /admin/.
:camaras
if defined ZENTRY_SIMULADOR goto con_simulador
if exist "simulador\parqueadero-cctv.html" goto con_simulador
if exist "..\Entorno virtual\parqueadero-cctv.html" goto con_simulador
echo No encontre el simulador en simulador\: arranco el agente para las camaras de /admin/ (IP Webcam).
start "Zentry - Agente" cmd /k .venv\Scripts\python.exe -m agente
goto abrir
:con_simulador
echo Conectando las camaras virtuales del simulador...
start "Zentry - Camaras virtuales" cmd /k .venv\Scripts\python.exe camaras_virtuales.py

:abrir
start "" "%URL%"
echo.
echo   Zentry esta listo en %URL%
echo   Usuarios de demostracion: admin / Zentry2026 y seguridad / Zentry2026
echo   Para detenerlo, cierra las ventanas "Zentry - ...".
echo.
pause
exit /b 0

:web_no_arranco
echo.
echo El servidor web no respondio en 60 s: revisa la ventana "Zentry - Servidor web".
echo Si dice que el puerto 8000 esta en uso, Zentry ya estaba abierto o hay otro programa usandolo.
pause
exit /b 1

:sin_python
echo.
echo No encontre Python 3.12 o superior.
echo Instalalo desde https://www.python.org/downloads/ y marca "Add python.exe to PATH".
echo Despues vuelve a abrir este archivo.
echo.
pause
exit /b 1

:error
echo.
echo Algo fallo: revisa los mensajes de arriba.
pause
exit /b 1
