@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0release\PromptStudio\PromptStudio.exe" (
  start "" "%~dp0release\PromptStudio\PromptStudio.exe"
  exit /b
)
set "STUDIO_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if exist "%STUDIO_PY%" (
  start "" "%STUDIO_PY%" "%~dp0run.py"
  exit /b
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0run.py"
  exit /b
)
echo Python 3.12+ is required. See README.md for setup.
pause
