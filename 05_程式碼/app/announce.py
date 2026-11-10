"""公告發布：國際處寄給學生的 Email → 改寫成 LINE 訊息 → 承辦人確認 → 推播＋存進小幫手知識庫。

demo 版：信件用「貼上」的；還沒有正式 LINE 帳號金鑰，所以推播只在 demo 頁的模擬手機上顯示。
正式版：信件改成「密件副本給小幫手信箱」自動收；有 LINE 金鑰且 ANNOUNCE_LINE_PUSH=on 時才真的群發。
資料都在 Supabase 的 announcements 表，只能透過資料庫函式存取（和後台其他功能一樣由 admin key 把關）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import config
import sb

TOPICS = ["食", "衣", "住", "行", "育", "樂", "居留簽證", "工作", "保險醫療", "獎助學金", "緊急安全", "新生"]
AUDIENCES = ["全體境外生", "外籍學位生", "交換生", "僑生", "陸生"]

DRAFT_PROMPT = """你負責把中央大學國際事務處寄給境外生的 Email，改寫成 LINE 官方帳號的推播訊息。只回傳一個 JSON 物件，不要其他文字。
欄位：
- title：公告標題，30 字內，拿掉【國際處】這類前綴
- summary_zh：繁體中文，120 字內，第一句講學生要做什麼、什麼時候前；只能用信裡有的資訊
- summary_en：英文，60 words 內，意思和中文一樣
- notify_text：手機通知列的一行預覽，30 字內，格式像「【國際處公告】寒假留宿申請，12/15 截止」
- deadline：信裡的截止日或活動日期，格式 YYYY-MM-DD；信裡沒寫年份就用今天之後最近的那一天；沒有就填 null
- audience：發給誰，只能是 全體境外生、外籍學位生、交換生、僑生、陸生 其中一個
- topics：從 食、衣、住、行、育、樂、居留簽證、工作、保險醫療、獎助學金、緊急安全、新生 選 1 到 3 個
- legal：信件內容是法規、移民、居留、工作證、保險這類正式權益事項就填 true，否則 false
今天是 {today}。"""


# ---------------------------------------------------------------- AI 改寫
def draft(subject: str, raw: str) -> dict:
    """信件 → LINE 訊息草稿。AI 失敗時用簡單規則補，承辦人一定會再看過才推播。"""
    out = _fallback(subject, raw)
    try:
        text = _llm(DRAFT_PROMPT.replace("{today}", config.today().isoformat()),
                    f"<subject>{subject}</subject>\n<email>\n{raw[:6000]}\n</email>")
        m = re.search(r"\{.*\}", text, re.S)
        got = json.loads(m.group(0)) if m else {}
        for k in ("title", "summary_zh", "summary_en", "notify_text"):
            if isinstance(got.get(k), str) and got[k].strip():
                out[k] = got[k].strip()
        if _valid_date(got.get("deadline")):
            out["deadline"] = got["deadline"]
        elif got.get("deadline") is None and "deadline" in got:
            out["deadline"] = None
        if got.get("audience") in AUDIENCES:
            out["audience"] = got["audience"]
        topics = [t for t in (got.get("topics") or []) if t in TOPICS][:3]
        if topics:
            out["topics"] = topics
        out["legal"] = bool(got.get("legal"))
    except Exception as e:  # noqa: BLE001  AI 不能用時，草稿照樣產生
        out["checks"].append({"level": "warn", "text": f"AI 改寫失敗（{type(e).__name__}），以下是用規則抓的草稿，請手動修改"})
    out["checks"] = checks(raw, out) + [c for c in out["checks"] if c["level"] == "warn"]
    return out


def _llm(system: str, user: str) -> str:
    if config.LLM_PROVIDER != "claude":
        import rag
        return rag.call_llm(system, user, [])
    import anthropic
    msg = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=40, max_retries=1).messages.create(
        model=config.CLAUDE_MODEL, max_tokens=900, system=system, messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _valid_date(s) -> bool:
    try:
        dt.date.fromisoformat(str(s))
        return True
    except ValueError:
        return False


DATE_MD = re.compile(r"(\d{1,2})\s*[月/]\s*(\d{1,2})\s*日?")


def _fallback(subject: str, raw: str) -> dict:
    title = re.sub(r"^【[^】]*】\s*", "", subject).strip()[:30] or raw.strip().split("\n")[0][:30]
    body = re.sub(r"\s+", " ", raw).strip()
    deadline = None
    m = DATE_MD.search(raw)
    if m:
        today = config.today()
        try:
            d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
            deadline = (d if d >= today else d.replace(year=today.year + 1)).isoformat()
        except ValueError:
            pass
    return {"title": title, "summary_zh": body[:120], "summary_en": "", "deadline": deadline,
            "notify_text": f"【國際處公告】{title}"[:30], "audience": "全體境外生", "topics": [], "legal": False,
            "checks": []}


def checks(raw: str, d: dict) -> list[dict]:
    """推播前的自動檢查：日期對不對得上、有沒有英文版、正式事項提醒。"""
    out = []
    dl = d.get("deadline")
    if dl:
        day = dt.date.fromisoformat(dl)
        in_raw = re.search(rf"(?<!\d){day.month}\s*[月/]\s*{day.day}(?!\d)", raw)
        in_zh = re.search(rf"(?<!\d){day.month}\s*[月/]\s*{day.day}(?!\d)", d.get("summary_zh") or "")
        if day < config.today():
            out.append({"level": "warn", "text": f"截止日 {dl} 已經過了，確定還要推播嗎？"})
        elif in_raw and in_zh:
            out.append({"level": "ok", "text": f"原信和 LINE 訊息的日期一致（{day.month}/{day.day}）"})
        elif not in_raw:
            out.append({"level": "warn", "text": f"原信裡找不到 {day.month}/{day.day} 這個日期，請確認截止日"})
        else:
            out.append({"level": "warn", "text": "LINE 訊息裡沒有寫到截止日，建議補上"})
    else:
        out.append({"level": "info", "text": "信裡沒有找到截止日；沒有截止日的公告會一直留在知識庫，過時請手動停用"})
    if not (d.get("summary_en") or "").strip():
        out.append({"level": "warn", "text": "還沒有英文版"})
    if d.get("legal"):
        out.append({"level": "warn", "text": "這封是法規或權益類的正式通知，請逐字核對後再推播"})
    return out


# ---------------------------------------------------------------- 知識庫段落
def kb_rows(a: dict, base_url: str) -> list[dict]:
    """一則公告切成幾段存進知識庫。標題寫明是國際處公告（範例會標「範例」），小幫手引用時學生看得出來源。"""
    tag = "國際處公告（範例）" if a.get("is_sample") else "國際處公告"
    title = f"{tag}：{a.get('title') or a['subject']}"
    url = f"{base_url.rstrip('/')}/notice/{a['id']}"
    date = (a.get("published_at") or dt.datetime.now().isoformat())[:10]
    head = f"【{title}】\n發布：{date}" + (f"｜截止：{a['deadline']}" if a.get("deadline") else "") + \
           f"｜對象：{a.get('audience') or '全體境外生'}\n"
    texts = [(head + (a.get("summary_zh") or ""), "zh")]
    if a.get("summary_en"):
        texts.append((f"【{title}】\n{a['summary_en']}", "en"))
    for part in _split(a.get("raw_body") or ""):
        texts.append((head + part, "zh"))
    vecs = _embed([t for t, _ in texts])
    ident = a.get("audience") if a.get("audience") in AUDIENCES[1:] else "全體境外生"
    return [{"title": title, "url": url, "lang": lang, "body": t, "topics": a.get("topics") or [],
             "identities": [ident], "valid_until": a.get("deadline") or "", "embedding": sb.vec(v)}
            for (t, lang), v in zip(texts, vecs)]


def _split(text: str, size: int = 450) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{p}".strip()
    if cur:
        out.append(cur)
    return out[:12]


def _embed(texts: list[str]) -> list[list[float]]:
    """和爬蟲資料用同一種向量（e5、文件要加 passage: 前綴），才查得到。"""
    import embedder
    fn = getattr(embedder, "_onnx_embed", None)
    if fn:
        return fn([f"passage: {t}" for t in texts])
    return embedder.embed_passages(texts)


# ---------------------------------------------------------------- LINE
def push_line(a: dict, base_url: str) -> dict:
    """有正式 LINE 金鑰、而且 ANNOUNCE_LINE_PUSH=on 時才真的群發；否則只在 demo 頁模擬。"""
    if os.getenv("ANNOUNCE_LINE_PUSH", "off") != "on" or not (config.LINE_CHANNEL_ACCESS_TOKEN and config.LINE_CHANNEL_SECRET):
        return {"mode": "demo", "text": "demo 模式：還沒有正式 LINE 帳號金鑰，只在 demo 頁的手機畫面顯示"}
    from linebot.v3.messaging import ApiClient, BroadcastRequest, Configuration, FlexMessage, MessagingApi
    from linebot.v3.messaging import FlexContainer
    flex = FlexMessage(alt_text=(a.get("notify_text") or a.get("title") or "國際處公告")[:300],
                       contents=FlexContainer.from_dict(flex_card(a, base_url)))
    with ApiClient(Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)) as client:
        MessagingApi(client).broadcast(BroadcastRequest(messages=[flex]))
    return {"mode": "broadcast", "text": "已群發給所有好友"}


def send_email(a: dict, base_url: str) -> dict:
    """同一則公告寄 Email。還沒接國際處的寄信帳號，demo 只記錄「模擬寄出」。"""
    if os.getenv("ANNOUNCE_EMAIL", "off") != "on":
        return {"mode": "demo", "text": "demo 模式：還沒接國際處寄信帳號，沒有實際寄出",
                "subject": f"【國際處公告】{a.get('title') or a.get('subject')}"}
    raise RuntimeError("寄信帳號尚未設定")


TOPIC_WORDS = {"食": "餐|吃|飲食|美食|餐會", "住": "宿舍|住宿|租屋|留宿", "行": "交通|公車|機場|腳踏車",
               "育": "選課|課程|考試|圖書館|學期|註冊", "樂": "活動|社團|旅遊|節慶|晚會",
               "居留簽證": "居留|簽證|ARC", "保險醫療": "保險|健保|醫療|看病", "獎助學金": "獎學金|助學金"}


def guess_topics(text: str) -> list[str]:
    return [t for t, w in TOPIC_WORDS.items() if re.search(w, text)][:3]


def flex_card(a: dict, base_url: str) -> dict:
    """LINE 卡片訊息（Flex Message）：和 demo 頁手機上看到的版面一樣。"""
    body = [{"type": "text", "text": a.get("title") or a["subject"], "weight": "bold", "wrap": True},
            {"type": "text", "text": a.get("summary_zh") or "", "size": "sm", "wrap": True}]
    if a.get("summary_en"):
        body.append({"type": "text", "text": a["summary_en"], "size": "xs", "color": "#5d6a74", "wrap": True})
    if a.get("deadline"):
        body.append({"type": "text", "text": f"截止 Deadline：{a['deadline']}", "size": "sm", "color": "#9a6700"})
    return {"type": "bubble",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#0d7a6a",
                       "contents": [{"type": "text", "text": "國際處公告｜OIA Notice", "color": "#ffffff", "size": "sm"}]},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
            "footer": {"type": "box", "layout": "vertical", "contents": [
                {"type": "button", "action": {"type": "uri", "label": "查看全文 Full notice",
                                              "uri": f"{base_url.rstrip('/')}/notice/{a['id']}"}}]}}


# ---------------------------------------------------------------- 範例公告（demo 用）
def _sample(i: int) -> str:
    return f"5a0c1e00-0000-4000-8000-00000000000{i}"


SAMPLES = [
    {"id": _sample(1), "source": "email", "sender": "[住宿承辦人]", "status": "published", "days_ago": 6,
     "subject": "【國際處轉知】宿舍網路設備維護，12/6 凌晨暫停上網",
     "raw_body": "各位同學好：\n\n轉知學務處住宿服務組通知：12 月 6 日（六）凌晨 2 點至 6 點進行宿舍網路設備維護，"
                 "期間所有宿舍有線及無線網路暫停服務，請同學提前完成需要上網的作業。\n\n維護時間可能依現場狀況提早結束。"
                 "如維護後仍無法上網，請到所屬宿舍服務中心反映。\n\n國際事務處 敬上",
     "attachments": [],
     "title": "宿舍網路設備維護，12/6 凌晨暫停上網",
     "summary_zh": "12/6（六）凌晨 2 點到 6 點宿舍網路維護，有線和 Wi-Fi 都不能用，需要上網的作業請提前完成。維護後還是不能上網，請到宿舍服務中心反映。",
     "summary_en": "Dorm internet (wired and Wi-Fi) will be down on Sat Dec 6, 2–6 am for maintenance. "
                   "Finish any online work beforehand. If it still doesn't work afterwards, contact your dorm service center.",
     "notify_text": "【國際處公告】12/6 凌晨宿舍網路維護", "deadline": "2026-12-06", "audience": "全體境外生",
     "topics": ["住"]},
    {"id": _sample(2), "source": "email", "sender": "[活動承辦人]", "status": "published", "days_ago": 3,
     "email": {"mode": "error", "text": "範例：寄信服務沒有回應，可以按重試"},
     "subject": "【國際處】國際學生感恩節餐會報名",
     "raw_body": "Dear all,\n\n國際處將於 11 月 26 日（四）晚上 6 點在學生活動中心舉辦感恩節餐會，提供火雞大餐與素食餐點，歡迎境外生和本地生一起參加。\n\n"
                 "名額 120 人，請於 11 月 20 日前填寫報名表，額滿即止。\n\n國際事務處",
     "attachments": [],
     "title": "國際學生感恩節餐會報名",
     "summary_zh": "11/26（四）晚上 6 點在學生活動中心有感恩節餐會，有素食。名額 120 人，11/20 前報名，額滿為止。",
     "summary_en": "Thanksgiving dinner on Thu Nov 26, 6 pm, at the Student Activity Center (vegetarian meals available). "
                   "120 seats; sign up by Nov 20.",
     "notify_text": "【國際處公告】感恩節餐會 11/20 前報名", "deadline": "2026-11-20", "audience": "全體境外生",
     "topics": ["食", "樂"]},
    {"id": _sample(3), "source": "email", "sender": "[居留證承辦人]", "status": "pending", "days_ago": 0,
     "subject": "【國際處】居留證延期說明會",
     "raw_body": "各位同學好：\n\n居留證效期即將到期的同學，請留意延期申請時間。國際處將於 12 月 3 日中午 12 點在國際處會議室舉辦居留證延期說明會，"
                 "說明線上申請步驟與應備文件，現場可協助檢查資料。\n\n申請規定以內政部移民署最新公告為準。\n\n國際事務處 敬上",
     "attachments": [{"name": "居留證延期應備文件.pdf"}]},
    {"id": _sample(4), "source": "email", "sender": "[圖書館轉寄]", "status": "pending", "days_ago": 1,
     "subject": "【轉知】期末考週圖書館延長開放",
     "raw_body": "各位同學好：\n\n轉知圖書館公告：期末考週（1 月 4 日至 1 月 15 日）總圖書館延長開放至晚上 12 點，"
                 "自習室 24 小時開放，請攜帶學生證刷卡進入。\n\n國際事務處",
     "attachments": []},
    {"id": _sample(5), "source": "email", "sender": "[國際處]", "status": "skipped", "days_ago": 8,
     "subject": "【國際處】11 月份電子報",
     "raw_body": "本月電子報內容：國際處活動回顧、交換生心得分享、姊妹校介紹。", "attachments": [],
     "title": "11 月份電子報"},
]


def seed(key: str, base_url: str) -> dict:
    """放入範例公告（可以重複執行，會覆蓋同一批範例）。已推播的那幾則同時寫進知識庫。"""
    done = []
    now = dt.datetime.now(dt.timezone.utc)
    for s in SAMPLES:
        row = {k: v for k, v in s.items() if k not in ("status", "days_ago", "email")}
        row.update(is_sample=True, received_at=(now - dt.timedelta(days=s["days_ago"], hours=2)).isoformat())
        if s["status"] == "pending":  # 待確認的讓 AI 先改寫好，demo 時承辦人只要看過、按確認
            row.update({k: v for k, v in draft(s["subject"], s["raw_body"]).items() if k != "legal"})
        a = sb.rpc("admin_announce_save", {"p_key": key, "p_row": row})
        if s["status"] == "published":
            a["published_at"] = (now - dt.timedelta(days=s["days_ago"])).isoformat()
            line = {"mode": "demo", "text": "範例：demo 模式"}
            a = sb.rpc("admin_announce_publish", {"p_key": key, "p_id": a["id"], "p_rows": kb_rows(a, base_url),
                                                  "p_line": line, "p_channels": {
                                                      "line": line, "ig": {"mode": "paused", "text": "IG 暫緩"},
                                                      "email": s.get("email") or {"mode": "off", "text": "沒勾 Email"},
                                                      "kb": {"mode": "on"}},
                                                  "p_published_at": a["published_at"]})
        elif s["status"] == "skipped":
            a = sb.rpc("admin_announce_skip", {"p_key": key, "p_id": a["id"]})
        done.append({"id": s["id"], "subject": s["subject"], "status": a.get("status")})
    return {"seeded": done}
