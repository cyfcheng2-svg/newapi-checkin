@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
  python local_app.py
  goto :done
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 local_app.py
  goto :done
)

echo.
echo Failed to start: Python was not found.
echo Install Python, then run: pip install -r requirements.txt
pause
exit /b 1

:done
if errorlevel 1 (
  echo.
  echo Failed to start. If dependencies are missing, run:
  echo pip install -r requirements.txt
  pause
)
