@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate
cd app
python check_keys.py
echo.
pause
