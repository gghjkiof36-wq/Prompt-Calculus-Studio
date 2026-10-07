@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo PCS needs its local Python environment.
  echo Follow docs\guide\getting-started.md to install Python 3.12 and create .venv.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0run.py" --v0.8.6-alpha
if not "%errorlevel%"=="0" (
  echo PCS could not start or stopped with an error. See the message above.
  pause
  exit /b 1
)
exit /b 0
