@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate
echo Uploading the knowledge base to Supabase (the cloud version on Vercel uses it).
echo Run 2_更新資料.bat first. Vectors are computed on this PC with e5 - no API key needed.
echo First run downloads the e5 model (about 140MB).
echo.
cd app
python cloud_ingest.py
echo.
pause
