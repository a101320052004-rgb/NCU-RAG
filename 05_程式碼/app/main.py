"""後端主程式（FastAPI）。

  GET  /           聊天 demo 網頁（仿通訊軟體畫面）
  POST /callback   LINE Webhook
  POST /api/chat   測試頁、demo 網頁用的問答 API
  GET  /health     健康檢查（雲端版會顯示知識庫有幾段資料）
  GET  /notice/{id} 公告全文頁（LINE 卡片「查看全文」打開的頁面）
  /api/admin/kb-import 匯入國際處提供的資料表（已轉成段落）
  /api/admin/announce  公告發布：貼上 Email → AI 改寫 → 確認推播（demo 模擬 LINE）＋寫進知識庫
  GET  /admin      管理後台全螢幕版（需要管理者金鑰；資料來自 Supabase）；demo 頁右側也有同一套後台

本機啟動：uvicorn main:app --port 8000
雲端版：部署到 Vercel，入口是 api/index.py，資料在 Supabase（STORE=supabase）
LINE 處理方式參考官方範例 github.com/line/line-bot-sdk-python（v3 API）。
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
import db
import router

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="NCU 國際生 LINE Bot")
db.init()

# ---------------------------------------------------------------- LINE
line_ready = bool(config.LINE_CHANNEL_SECRET and config.LINE_CHANNEL_ACCESS_TOKEN)
if line_ready:
    from linebot.v3 import WebhookParser
    from linebot.v3.exceptions import InvalidSignatureError
    from linebot.v3.messaging import (ApiClient, Configuration, MessageAction, MessagingApi,
                                      QuickReply, QuickReplyItem, ReplyMessageRequest,
                                      ShowLoadingAnimationRequest, TextMessage, URIAction)
    from linebot.v3.webhooks import MessageEvent, TextMessageContent

    parser = WebhookParser(config.LINE_CHANNEL_SECRET)
    line_conf = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)


def reply_line(event) -> None:
    """在背景處理：先顯示「輸入中」動畫，再查詢並回覆。"""
    user_id = event.source.user_id
    with ApiClient(line_conf) as api_client:
        api = MessagingApi(api_client)
        try:
            api.show_loading_animation(ShowLoadingAnimationRequest(chat_id=user_id, loading_seconds=20))
        except Exception as e:  # noqa: BLE001  動畫失敗不影響回答
            log.warning("loading animation failed: %s", e)
        try:
            result = router.handle(user_id, event.message.text, channel="line")
        except Exception as e:  # noqa: BLE001
            import rag
            rag.log_error("line", e)
            result = {"answer": "系統暫時有問題，請稍後再試，或直接聯絡國際處。\n"
                                "Something went wrong. Please try again later or contact the Office of International Affairs.",
                      "quick_replies": []}
        msg = TextMessage(text=result["answer"][:4900])  # LINE 單則上限 5000 字
        items = [QuickReplyItem(action=URIAction(label=c["label"][:20], uri=c["tel"]))  # 一鍵撥號
                 for c in result.get("calls", [])]
        items += [QuickReplyItem(action=MessageAction(label=q.split("：")[-1][:20], text=q))
                  for q in result.get("quick_replies", [])]
        if items:
            msg.quick_reply = QuickReply(items=items[:13])
        try:
            api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[msg]))
        except Exception as e:  # noqa: BLE001  例如 reply token 過期（約 1 分鐘）或 token 設錯
            log.error("reply failed: %s", e)


@app.post("/callback")
async def callback(request: Request, background: BackgroundTasks,
                   x_line_signature: str = Header(default="")):
    if not line_ready:
        raise HTTPException(503, "LINE 尚未設定：請在 .env 填入 LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN")
    body = (await request.body()).decode("utf-8")
    try:
        events = parser.parse(body, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(400, "Invalid signature")
    for ev in events:
        if isinstance(ev, MessageEvent) and isinstance(ev.message, TextMessageContent):
            background.add_task(reply_line, ev)  # 先回 200 給 LINE，再慢慢處理
    return "OK"


# ---------------------------------------------------------------- 測試頁 API
class ChatIn(BaseModel):
    user_id: str = "web-tester"
    message: str
    identity: str | None = None


def visitor_key(request: Request) -> str:
    """每日次數上限用：網頁訪客用 IP 計算（只存雜湊值），換瀏覽器或清快取也算同一人。"""
    ip = (request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
          or (request.client.host if request.client else "unknown")).split(",")[0].strip()
    return "ip:" + hashlib.sha256(("ncu-rag:" + ip).encode()).hexdigest()[:20]


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    try:
        return router.handle(body.user_id[:40], body.message[:1000], channel="web", identity_override=body.identity,
                             limit_key=visitor_key(request))
    except Exception as e:  # noqa: BLE001  任何錯誤都回一句好好說的話，不讓網頁出現紅字
        import rag
        rag.log_error("chat", e)
        return {"answer": "系統暫時有問題，請稍後再試，或直接聯絡國際處：" + config.CONTACT_ZH +
                          "\nSomething went wrong. Please try again later or contact: " + config.CONTACT_EN,
                "route": "error", "sources": [], "quick_replies": []}


@app.get("/api/warmup")
def warmup():
    """網頁打開時呼叫：先把向量模型載入，第一個問題就不用等下載。"""
    import embedder
    t = time.perf_counter()
    embedder.warm_up()
    return {"ok": True, "ms": round((time.perf_counter() - t) * 1000)}


@app.get("/api/selftest")
def selftest(q: str = "宿舍怎麼申請？", topic: str | None = None):
    """部署後檢查用：只測向量化和資料庫，不呼叫 Claude（避免被拿來刷費用）。"""
    import embedder
    import rag
    out, t = {"q": q}, time.perf_counter()
    try:
        embedder.embed_query(q)
        out["embed_ms"] = round((time.perf_counter() - t) * 1000)
        if topic in config.MENU_TOPICS:
            q = router.MENU_SEARCH[topic]
        hits = rag.retrieve(q, None, rag.detect_lang(q), topic if topic in config.MENU_TOPICS else None)
        out["retrieve_ms"] = round((time.perf_counter() - t) * 1000)
        ctx = rag.expand_context(hits, None, topic if topic in config.MENU_TOPICS else None)
        out["hits"] = [{"title": h["meta"]["title"], "distance": round(h["distance"], 3),
                        "chars": len(h["text"]), "with_context": len(c["text"])} for h, c in zip(hits, ctx)]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------- 管理儀表板
# 金鑰由資料庫檢查（private.settings 的 admin_key），後端不存；網址用 /admin#key=...，金鑰不會出現在伺服器紀錄裡
def _admin_key(x_admin_key: str) -> str:
    if config.STORE != "supabase":
        raise HTTPException(503, "儀表板只在雲端版（Supabase）提供")
    # 金鑰由資料庫判斷：admin_open=on（demo 期間）時不需要金鑰，off 時才檢查
    return x_admin_key or ""


def _admin_rpc(name: str, params: dict):
    import sb
    try:
        return sb.rpc(name, params)
    except RuntimeError as e:
        if "invalid admin key" in str(e):
            raise HTTPException(401, "管理者金鑰不對")
        raise HTTPException(502, f"資料庫拒絕這次操作：{str(e)[:200]}")


def _check(fn) -> dict:
    t = time.perf_counter()
    try:
        detail = fn()
        return {"ok": True, "ms": round((time.perf_counter() - t) * 1000), "detail": detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "ms": round((time.perf_counter() - t) * 1000), "detail": f"{type(e).__name__}: {str(e)[:160]}"}


def _claude_ok():
    """只查模型資訊，不產生回答，不花 token。"""
    if config.LLM_PROVIDER != "claude":
        return f"目前用 {config.LLM_PROVIDER}"
    import anthropic
    m = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=8, max_retries=0).models.retrieve(config.CLAUDE_MODEL)
    return m.id


@app.get("/admin")
def admin_page():
    return FileResponse(config.APP_DIR / "web" / "admin.html")


@app.get("/api/admin/stats")
def admin_stats(days: int = 14, x_admin_key: str = Header(default="")):
    key = _admin_key(x_admin_key)
    stats = _admin_rpc("admin_stats", {"p_key": key, "p_days": days})  # 金鑰錯誤會在這裡擋下

    def retrieval():
        import rag
        hits = rag.retrieve("宿舍怎麼申請", None, "zh")
        if not hits:
            raise RuntimeError("查不到任何段落")
        return f"{len(hits)} 段"

    import embedder
    stats["checks"] = {
        "database": _check(lambda: f"{stats['kb']['active']} 段可用"),
        "embedder": _check(lambda: f"{len(embedder.embed_query('health check'))} 維"),
        "retrieval": _check(retrieval),
        "claude": _check(_claude_ok),
        "line": {"ok": line_ready, "ms": 0, "detail": "已設定" if line_ready else "尚未設定 LINE 金鑰"},
    }
    return stats


@app.get("/api/admin/messages")
def admin_messages(days: int = 7, route: str = "", q: str = "", limit: int = 50, offset: int = 0,
                   x_admin_key: str = Header(default="")):
    """對話紀錄：問題、回答、來源。轉真人的內容不顯示（資料庫函式裡就遮掉了）。"""
    return _admin_rpc("admin_messages", {"p_key": _admin_key(x_admin_key), "p_days": days, "p_route": route,
                                         "p_q": q[:50], "p_limit": limit, "p_offset": offset})


# ---------------------------------------------------------------- 公告發布（Email → LINE＋知識庫）
def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


class AnnounceIn(BaseModel):
    subject: str
    raw_body: str
    source: str = "paste"


class AnnounceEdit(BaseModel):
    title: str | None = None
    summary_zh: str | None = None
    summary_en: str | None = None
    notify_text: str | None = None
    deadline: str | None = None
    audience: str | None = None
    channels: dict | None = None  # {"line": bool, "email": bool, "kb": bool, "ig": bool, "ig_card": {...}}


class AnnounceCompose(BaseModel):
    title: str
    summary_zh: str
    summary_en: str = ""
    deadline: str = ""
    audience: str = "全體境外生"


class AssistIn(BaseModel):
    title: str = ""
    summary_zh: str


@app.get("/api/admin/announce")
def announce_list(x_admin_key: str = Header(default="")):
    return _admin_rpc("admin_announce_list", {"p_key": _admin_key(x_admin_key)})


@app.post("/api/admin/announce")
def announce_create(body: AnnounceIn, x_admin_key: str = Header(default="")):
    """貼上一封信：AI 改寫成 LINE 訊息草稿，存成「待確認」。"""
    import announce
    key = _admin_key(x_admin_key)
    if not body.raw_body.strip():
        raise HTTPException(400, "信件內容是空的")
    d = announce.draft(body.subject, body.raw_body)
    d.pop("legal", None)
    row = {**d, "subject": body.subject.strip() or d["title"], "raw_body": body.raw_body,
           "source": "email" if body.source == "email" else "paste"}
    return _admin_rpc("admin_announce_save", {"p_key": key, "p_row": row})


@app.post("/api/admin/announce/compose")
def announce_compose(body: AnnounceCompose, x_admin_key: str = Header(default="")):
    """直接在後台寫 LINE 公告（不經過 Email）：存成草稿，接著呼叫 publish。"""
    import announce
    key = _admin_key(x_admin_key)
    title, zh = body.title.strip(), body.summary_zh.strip()
    if not title or not zh:
        raise HTTPException(400, "標題和中文內容都要填")
    row = {"source": "line", "sender": "國際處承辦人", "subject": title,
           "raw_body": zh + (f"\n\n{body.summary_en.strip()}" if body.summary_en.strip() else ""),
           "title": title, "summary_zh": zh, "summary_en": body.summary_en.strip(),
           "notify_text": f"【國際處公告】{title}"[:30], "deadline": body.deadline,
           "audience": body.audience if body.audience in announce.AUDIENCES else "全體境外生",
           "topics": announce.guess_topics(title + zh)}
    return _admin_rpc("admin_announce_save", {"p_key": key, "p_row": row})


@app.post("/api/admin/announce/assist")
def announce_assist(body: AssistIn, x_admin_key: str = Header(default="")):
    """「AI 幫我翻」：從中文內容產生英文版、通知列文字和截止日建議。不存檔。"""
    import announce
    _admin_key(x_admin_key)
    if not body.summary_zh.strip():
        raise HTTPException(400, "先寫中文內容")
    d = announce.draft(body.title, body.summary_zh)
    return {k: d.get(k) for k in ("summary_en", "notify_text", "deadline", "topics")}


def _send_email(announce, key: str, a: dict, base: str) -> dict:
    """寄信＋每日上限。失敗只記在 Email 這個管道，不影響 LINE 和知識庫。"""
    cfg = announce.email_config()
    if cfg["on"]:
        done = announce.sent_today(_admin_rpc("admin_announce_list", {"p_key": key}))
        if done >= cfg["cap"]:
            return {"mode": "error", "text": f"今天已經寄了 {done} 則，達到每日上限 {cfg['cap']} 則，明天再按重試"}
    try:
        res = announce.send_email(a, base)
    except RuntimeError as e:
        res = {"mode": "error", "text": f"寄信失敗：{e}"}
    except Exception as e:  # noqa: BLE001
        res = {"mode": "error", "text": f"寄信失敗：{type(e).__name__}"}
    res["at"] = datetime.now(timezone.utc).isoformat()
    return res


@app.post("/api/admin/mail/fetch")
def mail_fetch(x_admin_key: str = Header(default="")):
    """檢查小幫手信箱：把密件副本寄來的新信帶進「待處理」，每封先由 AI 改寫好（一次最多 3 封）。"""
    import announce
    import inbox
    key = _admin_key(x_admin_key)
    known = {a.get("id") for a in (_admin_rpc("admin_announce_list", {"p_key": key}) or [])}
    try:
        r = inbox.fetch_inbox(known)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    saved = []
    for m in r["new"]:
        if not m["raw_body"].strip():
            continue
        d = announce.draft(m["subject"], m["raw_body"])
        d.pop("legal", None)
        row = {**d, "id": m["id"], "subject": m["subject"] or d["title"], "raw_body": m["raw_body"],
               "source": "email", "sender": m["sender"], "received_at": m["received_at"]}
        saved.append(_admin_rpc("admin_announce_save", {"p_key": key, "p_row": row}))
    return {"inbox": announce.mask(r["alias"]), "matched": r["matched"], "imported": len(saved),
            "skipped": r["skipped"], "more": r["more"], "subjects": [x.get("subject") for x in saved]}


@app.get("/api/admin/email-config")
def email_config_view(x_admin_key: str = Header(default="")):
    """後台 Email 勾選說明和預覽用：只回遮住的信箱，不回密碼。"""
    import announce
    import inbox
    _admin_key(x_admin_key)
    cfg = announce.email_config()
    return {"on": cfg["on"], "ready": cfg["ready"], "from": announce.mask(cfg["user"]) if cfg["user"] else "",
            "to": [announce.mask(x) for x in cfg["to"][:3]], "to_count": len(cfg["to"]), "cap": cfg["cap"],
            "inbox": announce.mask(inbox.inbox_address())}


@app.post("/api/admin/announce/{aid}/publish")
def announce_publish(aid: str, body: AnnounceEdit, request: Request, x_admin_key: str = Header(default="")):
    """確認發布：LINE 預設會發（demo 為模擬）；勾了 Email 就寄（demo 為模擬）；勾了知識庫就寫進去。"""
    import announce
    key = _admin_key(x_admin_key)
    edits = {k: v for k, v in body.model_dump().items() if v is not None and k != "channels"}
    if "deadline" in edits and not edits["deadline"]:
        edits["deadline"] = ""
    want = {"line": True, "email": True, "kb": True, "ig": False, **(body.channels or {})}
    card = announce.ig_card(want.pop("ig_card", None))
    a = _admin_rpc("admin_announce_save", {"p_key": key, "p_row": {"id": aid, "subject": "", "raw_body": "", **edits}})
    base = _base_url(request)
    a["checks"] = announce.checks(a["raw_body"], a)
    if not want.get("line"):
        line = {"mode": "off", "text": "沒勾 LINE"}
    else:
        try:
            line = announce.push_line(a, base)
        except Exception as e:  # noqa: BLE001  一個管道失敗不影響其他管道
            line = {"mode": "error", "text": f"LINE 推播失敗：{type(e).__name__}"}
    channels = {"line": line,
                "ig": {"mode": "manual", "text": "圖卡已產生；IG API 還沒串接，請下載圖卡後手動發文", "card": card}
                if want.get("ig") else {"mode": "off", "text": "沒勾 IG"}}
    channels["email"] = _send_email(announce, key, a, base) if want.get("email") else {"mode": "off", "text": "沒勾 Email"}
    channels["kb"] = {"mode": "on" if want.get("kb") else "off"}
    rows = announce.kb_rows(a, base) if want.get("kb") else []
    a = _admin_rpc("admin_announce_publish", {"p_key": key, "p_id": aid, "p_rows": rows, "p_line": line,
                                              "p_channels": channels})
    a["card"] = announce.flex_card(a, base)
    return a


@app.post("/api/admin/announce/{aid}/retry-email")
def announce_retry_email(aid: str, request: Request, x_admin_key: str = Header(default="")):
    import announce
    key = _admin_key(x_admin_key)
    a = next((x for x in _admin_rpc("admin_announce_list", {"p_key": key}) if x["id"] == aid), None)
    if not a:
        raise HTTPException(404)
    res = _send_email(announce, key, a, _base_url(request))
    return _admin_rpc("admin_announce_channel", {"p_key": key, "p_id": aid, "p_name": "email", "p_val": res})


@app.post("/api/admin/announce/{aid}/kb-remove")
def announce_kb_remove(aid: str, x_admin_key: str = Header(default="")):
    """提早從知識庫下架：小幫手不再用這則回答（LINE 已送出的收不回）。"""
    return _admin_rpc("admin_announce_kb_remove", {"p_key": _admin_key(x_admin_key), "p_id": aid})


@app.post("/api/admin/announce/{aid}/skip")
def announce_skip(aid: str, x_admin_key: str = Header(default="")):
    return _admin_rpc("admin_announce_skip", {"p_key": _admin_key(x_admin_key), "p_id": aid})


@app.post("/api/admin/announce-samples")
def announce_seed(request: Request, x_admin_key: str = Header(default="")):
    import announce
    return announce.seed(_admin_key(x_admin_key), _base_url(request))


@app.delete("/api/admin/announce-samples")
def announce_clear(x_admin_key: str = Header(default="")):
    return {"removed": _admin_rpc("admin_announce_clear_samples", {"p_key": _admin_key(x_admin_key)})}


# ---------------------------------------------------------------- 匯入整理好的資料表
class KbRow(BaseModel):
    id: str
    title: str
    body: str
    url: str = ""
    topics: list[str] = []
    identities: list[str] = []
    lang: str = "zh"
    last_verified: str = ""


class KbImport(BaseModel):
    run_id: str
    rows: list[KbRow]
    replace: bool = False


@app.post("/api/admin/kb-import")
def kb_import(body: KbImport, x_admin_key: str = Header(default="")):
    """匯入國際處提供的資料表（例如食衣住行的 Excel，先轉成段落）：後端算向量後寫進知識庫，一次最多 40 段。

    run_id 以 sheet- 開頭；這些段落在知識庫裡排在爬蟲網頁前面。之後在自己電腦重新上傳整個知識庫時，
    同樣的內容會從「人工整理」資料夾的 Markdown 上傳，這一批會自動被取代。
    """
    import announce
    import sb
    key = _admin_key(x_admin_key)
    if not body.rows or len(body.rows) > 40:
        raise HTTPException(400, "一次要 1 到 40 段")
    vecs = announce._embed([r.body for r in body.rows])
    rows = [{**r.model_dump(), "embedding": sb.vec(v)} for r, v in zip(body.rows, vecs)]
    return _admin_rpc("admin_kb_import", {"p_key": key, "p_run_id": body.run_id, "p_rows": rows,
                                          "p_replace": body.replace})


@app.get("/notice/{aid}")
def notice_page(aid: str):
    """學生點 LINE 卡片的「查看全文」打開的頁面。只顯示已推播的公告。"""
    import html
    import re as _re
    from fastapi.responses import HTMLResponse
    if not _re.fullmatch(r"[0-9a-f-]{36}", aid):
        raise HTTPException(404)
    import sb
    n = sb.rpc("get_notice", {"p_id": aid})
    if not n:
        return HTMLResponse(NOTICE_HTML.replace("{body}", "<p>找不到這則公告，可能已經下架。</p>"), status_code=404)
    e = lambda s: html.escape(str(s or ""))  # noqa: E731
    files = "".join(f'<li>{e(f.get("name"))}<span>（demo 範例沒有實際檔案）</span></li>' if n.get("is_sample")
                    else f'<li>{e(f.get("name"))}</li>' for f in n.get("attachments") or [])
    body = (('<p class="sample">這是 demo 用的範例公告，不是真的通知。</p>' if n.get("is_sample") else "") +
            f'<h1>{e(n.get("title") or n.get("subject"))}</h1>' +
            (f'<p class="due">截止 Deadline：{e(n["deadline"])}</p>' if n.get("deadline") else "") +
            f'<section lang="zh-Hant"><h2>重點</h2><p>{e(n.get("summary_zh"))}</p></section>' +
            (f'<section lang="en"><h2>In English</h2><p>{e(n["summary_en"])}</p></section>' if n.get("summary_en") else "") +
            (f'<section><h2>原始通知全文</h2><div class="raw">{e(n.get("raw_body"))}</div></section>'
             if n.get("source") != "line" else "") +
            (f'<section><h2>附件</h2><ul class="files">{files}</ul></section>' if files else "") +
            f'<p class="foot">發布：{e((n.get("published_at") or "")[:10])}｜國際事務處發布，有問題請洽國際處。</p>')
    return HTMLResponse(NOTICE_HTML.replace("{body}", body))


NOTICE_HTML = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>國際處公告</title>
<style>
:root{--fg:#1d2730;--muted:#5d6a74;--line:#dde3e7;--accent:#0d7a6a;--bg:#ffffff}
@media (prefers-color-scheme:dark){:root{--fg:#e3ebee;--muted:#9aacb3;--line:#2a3a41;--accent:#4cc3ad;--bg:#172126}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 "Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif}
header{background:#0d7a6a;color:#fff;padding:14px 18px;font-size:13px}
main{max-width:640px;margin:0 auto;padding:16px 18px 32px}
h1{font-size:21px;line-height:1.4;margin:6px 0 10px} h2{font-size:14px;color:var(--muted);margin:18px 0 4px;font-weight:500}
p{margin:0} .due{background:#fff8e6;color:#7a5200;border:1px solid #f0d9a0;border-radius:6px;padding:6px 10px;font-weight:700;display:inline-block}
.raw{white-space:pre-wrap;border-left:3px solid var(--line);padding-left:10px}
.files{padding-left:1.2em;margin:0}.files span{color:var(--muted);font-size:13px}
.sample{background:#fbefdc;color:#7a4a00;border-radius:6px;padding:6px 10px;font-size:13px;margin-bottom:8px}
.foot{margin-top:22px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
</style></head><body><header>國際處公告｜OIA Notice</header><main>{body}</main></body></html>"""


WEB_ASSETS = {"backoffice.js": "application/javascript", "backoffice.css": "text/css",
              "ig-logo.jpg": "image/jpeg", "ig-badge.jpg": "image/jpeg"}


@app.get("/web/{name}")
def web_asset(name: str):
    if name not in WEB_ASSETS:
        raise HTTPException(404)
    return FileResponse(config.APP_DIR / "web" / name, media_type=WEB_ASSETS[name])


class AdminSet(BaseModel):
    name: str
    value: str


@app.post("/api/admin/setting")
def admin_setting(body: AdminSet, x_admin_key: str = Header(default="")):
    return _admin_rpc("admin_set", {"p_key": _admin_key(x_admin_key), "p_name": body.name, "p_value": body.value})


@app.get("/")
def demo_page():
    return FileResponse(config.APP_DIR / "web" / "index.html")


@app.get("/health")
def health():
    info = {"ok": True, "line_ready": line_ready, "llm": config.LLM_PROVIDER, "embedder": config.EMBEDDER,
            "store": config.STORE,
            "stale_before": config.stale_before().isoformat()}
    if config.STORE == "supabase":
        try:
            import sb
            info["kb"] = (sb.rpc("kb_stats", {}) or [{}])[0]
        except Exception as e:  # noqa: BLE001
            info["kb_error"] = str(e)[:200]
    return info
