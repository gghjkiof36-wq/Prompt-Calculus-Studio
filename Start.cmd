@echo off
setlocal
start "" "%SystemRoot%\System32\wscript.exe" //nologo "%~dp0Start.vbs" %*
exit /b 0
