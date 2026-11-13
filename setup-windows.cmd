@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 or newer from python.org with the Python launcher enabled.
  pause
  exit /b 1
)
where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo Install Node.js 22 LTS or newer, then reopen this window.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 goto failed
pushd frontend
call npm.cmd install
if errorlevel 1 (
  popd
  goto failed
)
call npm.cmd run build
if errorlevel 1 (
  popd
  goto failed
)
popd
if not exist .env copy .env.example .env >nul
echo Setup complete. Edit .env for optional API connections, then run launch-windows.cmd.
pause
exit /b 0
:failed
echo Setup failed. See the error above.
pause
exit /b 1
