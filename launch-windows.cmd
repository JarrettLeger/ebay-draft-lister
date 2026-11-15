@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe goto setup
if not exist frontend\dist\index.html goto setup
start "" "%SystemRoot%\System32\wscript.exe" "%~dp0eBay Draft Lister.vbs"
exit /b
:setup
echo Run setup-windows.cmd first.
pause
exit /b 1
