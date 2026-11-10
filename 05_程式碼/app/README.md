# 國際生 LINE Bot（demo 版）

學生在 LINE 問問題，系統只從**還有效**的國際處資料找答案，用耐心、直接的語氣回覆，並附上來源和資料日期。

使用的開源套件：
- [line-bot-sdk-python](https://github.com/line/line-bot-sdk-python)：LINE 官方 SDK
- [FastAPI](https://github.com/fastapi/fastapi)：後端
- [Chroma](https://github.com/chroma-core/chroma)：向量資料庫
- [Streamlit](https://github.com/streamlit/streamlit)：測試頁
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers)＋multilingual-e5：中英文向量化
- [anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python)：呼叫 Claude

## 檔案

| 檔案 | 做什麼 |
| --- | --- |
| `main.py` | 後端主程式：LINE Webhook、測試頁 API |
| `router.py` | 分流：設定身分、高風險轉人工、選單、一般問答 |
| `rag.py` | 檢索 → 時效過濾 → Claude 生成 → 附來源與日期 |
| `ingest.py` | 把爬蟲結果和 FAQ 匯入資料庫，過期資料擋在這裡 |
| `db.py` | SQLite：使用者設定、對話紀錄、轉人工清單 |
| `test_ui.py` | 小組內部測試頁 |
| `setup_richmenu.py` | 建立 LINE 圖文選單（食衣住行育樂） |
| `../prompts/system_prompt.md` | 回答語氣與規則，要調整語氣就改這個檔 |

## 一、安裝（Windows，只做一次）

在 `05_程式碼\app` 資料夾按右鍵 →「在終端機中開啟」：

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env
```

在 `.env` 裡填好：
- LLM 二選一：**Gemini 免費額度**（aistudio.google.com → Get API key，填 `COMPAT_API_KEY`），或 **Claude**（console.anthropic.com，填 `ANTHROPIC_API_KEY` 並把 `LLM_PROVIDER` 改成 `claude`）
- `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`：做法見第四步
- `CONTACT_ZH`、`CONTACT_EN`、`SECURITY_PHONE`：**照學生手冊填正確的窗口和校安中心電話**。轉人工時會把這些資訊給學生，不能留空就上線

## 二、匯入資料

先照 `crawler\README.md` 執行爬蟲，然後：

```powershell
python ingest.py
```

會看到「可用 N 段；待確認 N 段、已過期 N 段」。被擋下來的資料列在 `04_知識庫資料\整理後\待確認清單.csv`，可以拿去請國際處確認。

**手動補 FAQ**：把 `04_知識庫資料\整理後\faq_範本.csv` 另存成 `faq.csv` 並填內容，再執行一次 `python ingest.py`。網站上沒有的資料，例如訪談聽到的腳踏車規定，可以用這個方式補進去。

第一次執行會下載向量化模型（約 470MB），要等幾分鐘。

## 三、展示用的聊天網頁（不需要 LINE）

雙擊 `05_程式碼\3_啟動demo.bat`，瀏覽器會打開 http://127.0.0.1:8000 ：

- 左邊是仿通訊軟體的聊天畫面：聊天泡泡、食衣住行育樂選單、身分快速回覆、輸入中動畫
- 右邊是「後台視角」：每則回答走了哪條路、用了哪些資料、資料是哪天確認的，demo 時可以指給評審看「過期資料不會被用到」
- 用手機打開同一個網址（同一個 Wi-Fi 下，把 127.0.0.1 換成電腦的 IP），會自動變成全螢幕手機版

## 四、小組測試頁（選用）

開兩個終端機，都要先執行 `.venv\Scripts\activate`：

```powershell
# 終端機 1：後端
uvicorn main:app --port 8000

# 終端機 2：測試頁（會自動開瀏覽器）
streamlit run test_ui.py
```

測試頁左側有訪談中的真實情境題目可以直接點。每則回答下方會顯示走了哪條路（RAG、選單、轉人工、查無資料）和用到哪些來源。

## 五、接上 LINE（之後再做）

1. **建立頻道**：LINE Developers Console → 你的 Provider → Create a new channel → Messaging API。
2. **拿金鑰**：Basic settings 頁複製 *Channel secret*；Messaging API 頁最下方的 *Channel access token* 按 Issue 後複製。兩個都貼進 `.env`，然後重開後端。
3. **讓 LINE 連得到你的電腦**：到 ngrok.com 註冊免費帳號並安裝，再開第三個終端機：
   ```powershell
   ngrok http 8000
   ```
   複製畫面上 `https://xxxx.ngrok-free.app` 這個網址。
4. **設定 Webhook**：Messaging API 頁 → Webhook URL 填 `https://xxxx.ngrok-free.app/callback` → 按 Verify，顯示 Success 就成功了 → 打開 *Use webhook*。
5. **關掉 LINE 內建的自動回覆**：同一頁點 *Auto-reply messages* 的 Edit，進到 LINE 官方帳號管理後台，把「自動回應訊息」關掉，不然學生會收到兩則回覆。
6. **建立圖文選單**：
   ```powershell
   python setup_richmenu.py
   ```
7. 用手機掃 Messaging API 頁的 QR code 加好友，就可以開始測試。

ngrok 免費版每次重開，網址都會變，要回到第 4 步重新填 Webhook URL。正式上線時要部署到雲端或學校主機。

## 六、雲端版（Vercel + Supabase）

本機版要開著電腦才能用；雲端版有固定網址，手機、評審直接打開就能聊，之後也可以當 LINE 的 Webhook（不用 ngrok）。

| 部分 | 放在哪 | 說明 |
| --- | --- | --- |
| 聊天網頁＋後端 | Vercel | 程式跟本機版同一份，靠 `STORE=supabase`、`EMBEDDER=e5_onnx` 切換 |
| 知識庫、對話紀錄 | Supabase（Postgres＋pgvector） | 資料表全部鎖住，只能透過資料庫函式存取 |
| 爬蟲 | 你的電腦 | 跟以前一樣跑 `2_更新資料.bat` |
| 上傳知識庫 | 你的電腦 | 跑 `4_上傳雲端.bat`（`cloud_ingest.py`） |

**向量模型**：雲端版用 e5 的 ONNX 量化版（不需要 PyTorch，Vercel 裝得下），上傳時在你電腦上算，不用任何 API、沒有額度限制。模型第一次會下載到 `使用者資料夾\.cache\ncu-rag-models`（約 140MB）。Vercel 閒置一陣子後第一次開網頁，要多等幾秒載入模型。

**更新雲端資料**：先 `2_更新資料.bat`，再 `4_上傳雲端.bat`。上傳時套用同一套時效規則，過期、待確認的資料不會上去；雲端上這次沒出現的舊資料會被刪掉。

**檢查有沒有正常**：打開 `網址/health`，`kb.active` 是雲端可用的段落數。

**金鑰放哪**：

| 金鑰 | 放在 | 用途 |
| --- | --- | --- |
| Claude 金鑰 | 你的 `.env`、Vercel | 回答 |
| `SUPABASE_KEY`（publishable） | 你的 `.env`、Vercel | 連 Supabase |
| `SUPABASE_APP_SECRET` | 只在 Vercel | 讀寫對話紀錄 |
| `SUPABASE_INGEST_SECRET` | 只在你的 `.env` | 上傳知識庫 |

## 七、demo 前檢查清單

- [ ] `.env` 的聯絡窗口和校安中心電話都已填正確
- [ ] 點選單「食、衣、住、行、育、樂」，每一格都有內容，或會誠實說「還沒有資料」
- [ ] 問「獎學金什麼時候截止」，不會回答已經過期的日期
- [ ] 打「我出車禍受傷了」，會直接給校安中心電話，不會讓 AI 自己回答
- [ ] 英文問題用英文回答，中文問題用中文回答
- [ ] 每則 RAG 回答最後都有「來源｜資料更新日期」

## 八、調整

| 想調整的 | 改哪裡 |
| --- | --- |
| 回答語氣、長度 | `../prompts/system_prompt.md` |
| 「太久以前」的標準 | `.env` 的 `STALE_MONTHS`（目前 12 個月）、`CURRENT_ACADEMIC_YEAR`（目前 115） |
| 轉人工的關鍵字 | `router.py` 的 `HIGH_RISK` |
| 回答太常說「查不到」 | `.env` 的 `MAX_DISTANCE` 調大一點（例如 0.35）；太常答非所問就調小 |
| 換 LLM | `.env` 的 `CLAUDE_MODEL`；要換別家模型就改 `rag.py` 的 `call_llm` |

## 九、隱私

- 不存學生姓名；LINE 使用者 ID 只存雜湊值。
- 對話紀錄：本機版存在 `store\app.db`，雲端版存在 Supabase，只用來找出答不好的題目。
- `.env` 裡有金鑰，**不要上傳到 GitHub**（`.gitignore` 已排除）。
