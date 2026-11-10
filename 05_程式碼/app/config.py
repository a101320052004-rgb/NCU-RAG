"""所有設定集中在這裡，數值從 .env 讀取（複製 .env.example 成 .env 後填寫）。"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --- 外部服務
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
LINE_CHANNEL_SECRET = _get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = _get("LINE_CHANNEL_ACCESS_TOKEN")

# claude：Claude API
# openai_compat：任何 OpenAI 相容介面（Gemini 免費額度、本機 Ollama 等），填下面三個 COMPAT_ 設定
# fake：不呼叫 API，直接把找到的資料列出來（測試用）
LLM_PROVIDER = _get("LLM_PROVIDER", "claude")
COMPAT_BASE_URL = _get("COMPAT_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
COMPAT_API_KEY = _get("COMPAT_API_KEY")
COMPAT_MODEL = _get("COMPAT_MODEL", "gemini-3.6-flash")
# e5：multilingual-e5（在本機跑，中英文向量化）
# e5_onnx：同一個 e5 模型的 ONNX 量化版，不需要 PyTorch，雲端版用（Vercel 裝得下）
# gemini：Gemini embedding API（備用）
# hash：簡易雜湊向量（只供測試）
EMBEDDER = _get("EMBEDDER", "e5")
E5_MODEL = _get("E5_MODEL", "intfloat/multilingual-e5-small")
E5_ONNX_REPO = _get("E5_ONNX_REPO", "Xenova/multilingual-e5-small")
E5_ONNX_FILE = _get("E5_ONNX_FILE", "onnx/model_quantized.onnx")
# 雲端版：上傳知識庫和回答問題都用這個向量模型，兩邊必須一樣
CLOUD_EMBEDDER = _get("CLOUD_EMBEDDER", "e5_onnx")
GEMINI_API_KEY = _get("GEMINI_API_KEY") or COMPAT_API_KEY
GEMINI_EMBED_MODEL = _get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
GEMINI_EMBED_DIMS = int(_get("GEMINI_EMBED_DIMS", "768"))

# --- 資料存哪裡
# local：本機 Chroma + SQLite（3_啟動demo.bat 用）
# supabase：雲端 Supabase（Vercel 版用）
STORE = _get("STORE", "local")
SUPABASE_URL = _get("SUPABASE_URL").rstrip("/")
SUPABASE_KEY = _get("SUPABASE_KEY")                       # publishable key
SUPABASE_APP_SECRET = _get("SUPABASE_APP_SECRET")         # 只放在 Vercel：讀寫對話紀錄用
SUPABASE_INGEST_SECRET = _get("SUPABASE_INGEST_SECRET")   # 只放在你電腦：上傳知識庫用

# --- 路徑（預設對應專案資料夾結構）
PROJECT_DIR = Path(_get("PROJECT_DIR", str(APP_DIR.parent.parent)))
RAW_DIR = PROJECT_DIR / "04_知識庫資料" / "原始資料"
CURATED_DIR = PROJECT_DIR / "04_知識庫資料" / "整理後"
STORE_DIR = Path(_get("STORE_DIR", str(APP_DIR / "store")))
CHROMA_DIR = STORE_DIR / "chroma"
# 模型檔放哪：Vercel 只有 /tmp 可以寫
# 本機放在使用者資料夾的 .cache，避免約 140MB 的模型檔被 OneDrive 同步
MODEL_DIR = Path(_get("MODEL_DIR", "/tmp/models" if os.getenv("VERCEL")
                      else str(Path.home() / ".cache" / "ncu-rag-models")))
SQLITE_PATH = STORE_DIR / "app.db"
SYSTEM_PROMPT_PATH = next((p for p in (APP_DIR / "prompts" / "system_prompt.md",
                                        APP_DIR.parent / "prompts" / "system_prompt.md") if p.exists()),
                          APP_DIR.parent / "prompts" / "system_prompt.md")

# --- 時效規則（規則一：不回答過時的內容）
STALE_MONTHS = int(_get("STALE_MONTHS", "12"))          # 超過幾個月沒確認過就不用
CURRENT_ACADEMIC_YEAR = int(_get("CURRENT_ACADEMIC_YEAR", "115"))


def today() -> dt.date:
    return dt.date.today()


def stale_before() -> dt.date:
    t = today()
    m = t.month - STALE_MONTHS
    y = t.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return dt.date(y, m, min(t.day, 28))


# --- 檢索
TOP_K = int(_get("TOP_K", "5"))
MAX_DISTANCE = float(_get("MAX_DISTANCE", "0.30"))   # 餘弦距離，越小越相近；超過就視為不相關

# --- 轉人工時給學生的聯絡資訊（請依學生手冊填寫，不要留空上線）
CONTACT_ZH = _get("CONTACT_ZH", "國際事務處（請在 .env 填入承辦窗口、分機與 Email）")
CONTACT_EN = _get("CONTACT_EN", "Office of International Affairs (fill in contact window, extension and email in .env)")
SECURITY_PHONE = _get("SECURITY_PHONE", "（請在 .env 填入學生手冊上的校安中心 24 小時電話）")

IDENTITIES = ["外籍學位生", "交換生", "僑生", "陸生", "訪問研修生"]
ALL_STUDENTS = "全體境外生"
MENU_TOPICS = ["食", "衣", "住", "行", "育", "樂"]
