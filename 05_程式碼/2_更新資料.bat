@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate
echo [1/2] Crawling websites (follows robots.txt, about 10-20 minutes)...
cd crawler
python crawler.py %*
cd ..\app
echo [2/2] Importing into the knowledge base...
python ingest.py
echo.
echo Done. Check:
echo   04_知識庫資料\原始資料\oia\report.md
echo   04_知識庫資料\整理後\待確認清單.csv
pause
