@echo off
setlocal
cd /d "%~dp0"
if not exist "vendor\ShareX\ShareX.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-sharex.ps1"
  if errorlevel 1 goto :failed
)
if exist "vendor\python\python.exe" (
  "vendor\python\python.exe" "%~dp0app.py" %*
  if errorlevel 1 goto :failed
  exit /b 0
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; sys.exit(sys.version_info[:2] < (3,10))" >nul 2>nul
  if not errorlevel 1 goto :python
)
py -3 -c "import sys; sys.exit(sys.version_info[:2] < (3,10))" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ is required. https://www.python.org/downloads/windows/
  goto :failed
)
py -3 "%~dp0app.py" %*
if errorlevel 1 goto :failed
exit /b 0
:python
python "%~dp0app.py" %*
if errorlevel 1 goto :failed
exit /b 0
:failed
pause
exit /b 1
