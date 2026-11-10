@echo off
chcp 65001 >nul
cd /d %~dp0
call .venv\Scripts\activate
echo [1/2] Checking crawl coverage and chunking...
cd crawler
python check_crawl.py
cd ..\app
echo.
echo [2/2] Retrieval test with the question list (no Claude, free)...
python eval_questions.py
echo.
echo Reports:
echo   04_知識庫資料\整理後\爬蟲驗收報告.md
echo   04_知識庫資料\整理後\檢索測試報告.md
pause
