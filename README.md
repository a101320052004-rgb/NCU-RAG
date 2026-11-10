# NCU 國際生 LINE Bot（校園 RAG 專案）

中央大學國際處的國際生問答助理：學生在 LINE 或網頁上提問，系統從國際處、住宿組、移民署等官方資料裡找答案，附上來源與更新日期；查不到或屬於高風險情況時轉給承辦人。

| 項目 | 內容 |
|---|---|
| 正式網址 | https://ncu-intl-assistant.vercel.app |
| 後端 | FastAPI，部署在 Vercel |
| 資料庫 | Supabase（PostgreSQL＋pgvector） |
| 向量模型 | multilingual-e5-small（ONNX 量化版） |
| 生成模型 | Claude Haiku 4.5 |

## 資料夾

| 路徑 | 內容 |
|---|---|
| `05_程式碼/app/` | 問答主程式：分流（router.py）、檢索與生成（rag.py）、網頁與 LINE 入口（main.py）、公告（announce.py）、知識庫上傳（ingest.py、cloud_ingest.py） |
| `05_程式碼/crawler/` | 官網爬蟲與資料分類 |
| `05_程式碼/prompts/` | 給 Claude 的 system prompt |
| `05_程式碼/*.bat` | Windows 一鍵腳本：安裝、更新資料、啟動 demo、上傳雲端 |
| `03_技術架構/` | 系統架構圖 |

訪談紀錄、原始爬蟲資料、國際處提供的檔案、成果交付物**不在這個 repo**，在小組的 OneDrive 資料夾。

## 第一次在自己電腦上跑

1. 安裝 Python 3.11。
2. 雙擊 `05_程式碼/1_安裝環境.bat`。
3. 把 `05_程式碼/app/.env.example` 複製成 `.env`，向組長拿金鑰填進去。**`.env` 不能上傳**，`.gitignore` 已經擋掉。
4. 雙擊 `3_啟動demo.bat`。

## 協作規則

- 改之前先在 GitHub Desktop 按 **Fetch origin／Pull**，改完寫一句說明再 **Commit → Push**。
- 金鑰、學生資料、國際處給的原始檔不要放進來。
- 正式網址的部署由組長處理，push 到 GitHub 不會自動更新正式網址。
