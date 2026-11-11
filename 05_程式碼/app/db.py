"""使用者設定、對話紀錄、轉人工清單。
本機版存 SQLite（store/app.db）；雲端版（STORE=supabase）存 Supabase。
不存姓名；LINE 使用者 ID 只存雜湊值。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_hash TEXT PRIMARY KEY,
  identity  TEXT,
  lang      TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_hash TEXT, channel TEXT, question TEXT, answer TEXT,
  route TEXT, sources TEXT, latency_ms INTEGER, feedback INTEGER,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS handoffs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_hash TEXT, category TEXT, question TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  handled_at TEXT
);
"""


def hash_user(user_id: str) -> str:
    return hashlib.sha256(("ncu-rag:" + user_id).encode()).hexdigest()[:20]


@contextmanager
def conn():
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.SQLITE_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _cloud() -> bool:
    return config.STORE == "supabase"


def _rpc(name: str, **params):
    import sb
    return sb.rpc(name, {"p_app_secret": config.SUPABASE_APP_SECRET, **params})


def init() -> None:
    if _cloud():
        return
    with conn() as c:
        c.executescript(SCHEMA)


def get_user(user_hash: str) -> dict:
    if _cloud():
        rows = _rpc("get_user", p_user_hash=user_hash) or []
        return rows[0] if rows else {"user_hash": user_hash, "identity": None, "lang": None}
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_hash=?", (user_hash,)).fetchone()
    return dict(row) if row else {"user_hash": user_hash, "identity": None, "lang": None}


def set_user(user_hash: str, identity: str | None = None, lang: str | None = None) -> None:
    if _cloud():
        _rpc("set_user", p_user_hash=user_hash, p_identity=identity, p_lang=lang)
        return
    u = get_user(user_hash)
    with conn() as c:
        c.execute("INSERT INTO users(user_hash, identity, lang) VALUES(?,?,?) "
                  "ON CONFLICT(user_hash) DO UPDATE SET identity=excluded.identity, lang=excluded.lang",
                  (user_hash, identity or u.get("identity"), lang or u.get("lang")))


def log_message(user_hash, channel, question, answer, route, sources, latency_ms) -> None:
    if _cloud():
        try:  # 紀錄寫不進去不影響回答
            _rpc("log_message", p_user_hash=user_hash, p_channel=channel, p_question=question, p_answer=answer,
                 p_route=route, p_sources=sources, p_latency_ms=latency_ms)
        except Exception as e:  # noqa: BLE001
            print("log_message failed:", e)
        return
    with conn() as c:
        c.execute("INSERT INTO messages(user_hash, channel, question, answer, route, sources, latency_ms) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (user_hash, channel, question, answer, route,
                   json.dumps(sources, ensure_ascii=False), latency_ms))


FOLLOW_UP_ROUTES = ("rag", "menu", "no_data")
FOLLOW_UP_MINUTES = 30


def recent_turns(user_hash: str, n: int = 3) -> list[dict]:
    """追問用的前幾題。11/12 修正：只取 30 分鐘內的（隔天回來問「那 B5 呢？」不該接到昨天的主題），
    也包含點選單和查無資料的題目（學生點「Scholarships」後再問「How do I apply?」，主題在選單那一題）。"""
    if _cloud():
        try:
            return _rpc("recent_turns_v2", p_user_hash=user_hash, p_n=n, p_minutes=FOLLOW_UP_MINUTES) or []
        except Exception as e:  # noqa: BLE001  資料庫還沒有 v2 時退回舊版
            print("recent_turns_v2 failed:", e)
            return _rpc("recent_turns", p_user_hash=user_hash, p_n=n) or []
    with conn() as c:
        rows = c.execute(f"SELECT question, answer FROM messages WHERE user_hash=? "
                         f"AND route IN ({','.join('?' * len(FOLLOW_UP_ROUTES))}) "
                         f"AND created_at > datetime('now','localtime',?) ORDER BY id DESC LIMIT ?",
                         (user_hash, *FOLLOW_UP_ROUTES, f"-{FOLLOW_UP_MINUTES} minutes", n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def llm_allow(key: str) -> str:
    """雲端版呼叫 Claude 前先問：ok / off（AI 已關閉）/ user_limit / site_limit（今天次數用完）。
    本機版沒有限制。資料庫連不上時照常回答，不讓 demo 卡住。"""
    if not _cloud():
        return "ok"
    try:
        return _rpc("llm_allow", p_user_key=key) or "ok"
    except Exception as e:  # noqa: BLE001
        print("llm_allow failed:", e)
        return "ok"


def log_handoff(user_hash, category, question) -> None:
    if _cloud():
        try:
            _rpc("log_handoff", p_user_hash=user_hash, p_category=category, p_question=question)
        except Exception as e:  # noqa: BLE001
            print("log_handoff failed:", e)
        return
    with conn() as c:
        c.execute("INSERT INTO handoffs(user_hash, category, question) VALUES(?,?,?)",
                  (user_hash, category, question))
