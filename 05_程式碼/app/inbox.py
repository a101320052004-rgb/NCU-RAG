"""小幫手收信：承辦人寄群發信時密件副本到小幫手地址，後台按「檢查信箱」用 IMAP 唯讀帶進來。
只搜尋寄到小幫手地址的信，不改成已讀、不讀其他信；寄件人信箱只存遮罩後的樣子。"""
from __future__ import annotations

import datetime as dt
import email
import html
import imaplib
import os
import re
import uuid
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from announce import mask


def inbox_address() -> str:
    """小幫手收信地址：承辦人寄群發信時密件副本到這裡。沒設定 ANNOUNCE_INBOX 時用寄件 Gmail 加上 +oia。"""
    a = os.getenv("ANNOUNCE_INBOX", "").strip()
    if a:
        return a
    user = os.getenv("SMTP_USER", "").strip()
    if "@" not in user:
        return ""
    local, dom = user.split("@", 1)
    return f"{local.split('+')[0]}+oia@{dom}"


MAIL_NS = uuid.UUID("6b1f0c52-6f1a-4a0e-9d1c-2c0e5f3a7a10")


def mail_id(message_id: str) -> str:
    """同一封信每次算出同一個公告 id，重複檢查信箱不會帶入兩次。"""
    return str(uuid.uuid5(MAIL_NS, message_id.strip()))


def _dh(v) -> str:
    try:
        return str(make_header(decode_header(v or ""))).strip()
    except Exception:  # noqa: BLE001
        return (v or "").strip()


def mail_text(msg) -> str:
    """取信件內文：優先純文字，沒有才把 HTML 去掉標籤；附件不讀。"""
    plain = html_body = None
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        try:
            text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if ct == "text/plain" and plain is None:
            plain = text
        elif ct == "text/html" and html_body is None:
            html_body = text
    if plain is None and html_body:
        t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html_body)
        t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", t)
        plain = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = (plain or "").replace("\r", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _sent_to(msg, alias: str) -> bool:
    """只收寄到（或密件副本到）小幫手地址的信；Gmail 的 Delivered-To 會記下密件副本的收件地址。"""
    heads = " ".join(str(v) for k in ("Delivered-To", "X-Original-To", "To", "Cc")
                     for v in (msg.get_all(k) or []))
    return alias.lower() in heads.lower()


def fetch_inbox(known_ids: set[str], days: int = 14, limit: int = 3) -> dict:
    """用 IMAP 唯讀查 Gmail 收件匣：只搜尋寄到小幫手地址、近 days 天的信，不會改成已讀，也不讀其他信。"""
    user, pw, alias = os.getenv("SMTP_USER", "").strip(), os.getenv("SMTP_PASSWORD", ""), inbox_address()
    miss = [n for n, v in (("SMTP_USER", user), ("SMTP_PASSWORD", pw)) if not v]
    if miss or not alias:
        raise RuntimeError("收信設定不完整，缺少：" + "、".join(miss or ["ANNOUNCE_INBOX"]))
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
    except OSError as e:
        raise RuntimeError(f"連不到 Gmail 收信伺服器（{e}）") from e
    try:
        try:
            m.login(user, pw.replace(" ", ""))
        except imaplib.IMAP4.error as e:
            raise RuntimeError("Gmail 拒絕登入：請確認應用程式密碼正確") from e
        m.select("INBOX", readonly=True)
        typ, data = m.search(None, "X-GM-RAW", f'"deliveredto:{alias} newer_than:{days}d"')
        if typ != "OK":
            since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = m.search(None, "SINCE", since, "TO", f'"{alias}"')
        nums = (data[0] or b"").split()[::-1][:30]  # 新的先
        new, matched, skipped = [], 0, 0
        for num in nums:
            typ, parts = m.fetch(num, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            if not _sent_to(msg, alias):
                continue
            matched += 1
            mid = (msg.get("Message-ID") or f"{msg.get('Date')}|{msg.get('Subject')}").strip()
            aid = mail_id(mid)
            if aid in known_ids:
                skipped += 1
                continue
            if len(new) >= limit:
                continue
            name, addr = parseaddr(_dh(msg.get("From")))
            try:
                when = parsedate_to_datetime(msg.get("Date")).isoformat()
            except Exception:  # noqa: BLE001
                when = dt.datetime.now(dt.timezone.utc).isoformat()
            new.append({"id": aid, "subject": _dh(msg.get("Subject"))[:300], "raw_body": mail_text(msg)[:20000],
                        "sender": f"{name} <{mask(addr)}>" if name else mask(addr), "received_at": when})
        return {"alias": alias, "matched": matched, "skipped": skipped, "new": new,
                "more": max(0, matched - skipped - len(new))}
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass
