@echo off
cd /d "%~dp0"
echo Installing CareerPilot...
where python >nul 2>nul || (echo Python was not found. Install Python 3.11+ from python.org and tick "Add to PATH". & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo Dependency install failed. & pause & exit /b 1)
python -m playwright install chromium
echo.
echo Done. Double-click start_careerpilot.bat to launch.
pause
