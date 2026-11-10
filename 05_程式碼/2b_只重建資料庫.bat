@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate
echo Rebuilding the local knowledge base from the last crawl (no crawling, about 1-3 minutes).
echo.
cd app
python ingest.py
echo.
pause
