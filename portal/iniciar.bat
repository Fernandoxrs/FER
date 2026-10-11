@echo off
rem Inicia el portal en Windows. La primera vez instala las dependencias
rem y pide crear la cuenta de administrador.
cd /d "%~dp0"

where python >nul 2>&1 || (
  echo Python no esta instalado. Descargalo de https://www.python.org/downloads/
  echo y marca "Add python.exe to PATH" durante la instalacion.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparando el entorno por primera vez...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :error
)

if not exist "datos\portal.db" (
  echo.
  echo === Crea la cuenta de administrador ===
  ".venv\Scripts\python.exe" app.py crear-admin || goto :error
)

".venv\Scripts\python.exe" app.py
pause
exit /b 0

:error
echo.
echo Ocurrio un error. Revisa los mensajes de arriba.
pause
exit /b 1
