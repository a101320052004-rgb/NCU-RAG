@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate
echo [1/3] Crawling campus life pages (ncu_life, follows robots.txt)...
cd crawler
python crawler.py --site ncu_life
cd ..\app
echo [2/3] Comparing with the existing knowledge base...
python compare_new.py ncu_life
echo [3/3] Rebuilding the local knowledge base...
python ingest.py
echo.
echo Done. Check:
echo   04_知識庫資料\原始資料\ncu_life\report.md
echo   04_知識庫資料\整理後\新舊資料比對_ncu_life.md
echo If the comparison looks right, run 4_上傳雲端.bat to update the cloud version.
pause
