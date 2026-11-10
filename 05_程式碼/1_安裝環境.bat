@echo off
chcp 65001 >nul
cd /d %~dp0
echo [1/3] Checking Python...
py -3.11 --version >nul 2>nul && (set PY=py -3.11) || (set PY=py -3)
%PY% --version || (echo Python not found. Please install Python 3.11 from python.org and check "Add to PATH". & pause & exit /b 1)
echo [2/3] Creating virtual environment (.venv)...
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
echo [3/3] Installing packages (first time takes 5-15 minutes)...
pip install -r crawler\requirements.txt -r app\requirements.txt
if not exist app\.env copy app\.env.example app\.env
echo.
echo Done. Next:
echo   1. Edit crawler\config.yaml  - replace your-email
echo   2. Edit app\.env             - API keys, contact info, campus security phone
pause
