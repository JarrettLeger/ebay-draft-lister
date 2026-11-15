@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup-windows.cmd first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe launcher.py --browser
if errorlevel 1 pause
