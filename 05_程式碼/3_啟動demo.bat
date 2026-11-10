@echo off
chcp 65001 >nul
cd /d %~dp0
echo Starting backend... (first question may take ~30s while the model loads)
start "Backend - keep this window open" cmd /k "call .venv\Scripts\activate && cd app && uvicorn main:app --host 0.0.0.0 --port 8000"
timeout /t 8 >nul
start "" http://127.0.0.1:8000
echo Demo page opened in your browser: http://127.0.0.1:8000
echo Phone on the same Wi-Fi: http://<this PC IP>:8000  (run ipconfig to find IPv4)
echo To stop: close the Backend window.
pause
