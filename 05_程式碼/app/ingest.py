"""把資料匯入向量資料庫，並執行「規則一：不回答過時內容」的第一道關卡。

資料來源：
  1. 爬蟲輸出：04_知識庫資料/原始資料/<網站>/chunks.jsonl
  2. 人工整理的 FAQ：04_知識庫資料/整理後/faq.csv（欄位見 README）

每段資料會被判定為：
  active        可以拿來回答
  needs_review  可能過時，不參與回答，列進 待確認清單.csv 請國際處確認
  expired       期限已過，不進資料庫

用法：python ingest.py
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
import sys

import config
import embedder
import knowledge_policy

COLLECTION = "campus_kb"
YEAR_RE = re.compile(r"(?<!\d)(1[01]\d)\s*學?年度?(?=[^\d]|$)")


def as_int_date(s: str | None) -> int:
    """'2026-09-24' 或 '2026-09-24T10:00:00' → 20260924；空值 → 0"""
    if not s:
        return 0
    try:
        return int(dt.date.fromisoformat(str(s)[:10]).strftime("%Y%m%d"))
    except ValueError:
        return 0


# 法規、介紹頁上的日期是「訂定或修正日期」，不是截止日，舊年份也不代表失效。
# 這類頁面不套用「截止日」和「舊年份」兩條，但「12 個月內要確認過」照樣適用。
REGULATION_RE = re.compile(r"辦法|要點|規定|規則|收費標準|簡介")
# 標題像「【轉知】補助要點說明會開放報名」的是活動公告，不算法規，截止日照樣要檢查
ANNOUNCE_RE = re.compile(r"說明會|報名|公告|轉知|活動|徵件|招募")


def is_regulation(rec: dict) -> bool:
    title = rec.get("title") or ""
    return bool(REGULATION_RE.search(title)) and not ANNOUNCE_RE.search(title)


def has_text(rec: dict) -> bool:
    """圖片型簡報或掃描檔抽不出文字，只剩標題和「投影片 1、2、3」，這種段落沒辦法回答問題。"""
    body = re.sub(r"^【[^】]*】", "", rec.get("text", ""))
    body = re.sub(r"(#+ *投影片 *\d+|\s)", "", body)
    return len(body) >= 40


# 已經改制或改名的機關、地名：文件還寫著這些，代表內容是很多年前的（地址、電話、規費多半已經變了）
OLD_NAMES = re.compile(r"桃園縣|台北縣|臺北縣|Taoyuan County|Taipei County|入出國及移民署|中央健康保險局|"
                       r"Foreign Affairs Service Center|外事服務站")


# 不是內容的頁面：網站上的測試頁、隱私權與個資表單、只有選單的首頁
NOISE_TITLE = re.compile(r"(?i:^test\d*$)|TEST$|保有及管理個人資料|隱私權|^選單$|^網站導覽$")


# 本校學生出國交換用的資料（姊妹校清單、出國甄選簡章）：境外生用不到，留著會把「交換生」的問題帶偏
OUTBOUND_TITLE = re.compile(r"Partner Universities|出國交換|^學生出國$|^簽約流程$|^姐妹校$|^姊妹校$|海外教育推廣|赴外|築夢|"
                            r"(?i:outbound|study abroad)")


def judge(rec: dict) -> tuple[str, str]:
    """回傳 (狀態, 原因)。"""
    title = (rec.get("title") or "").strip()
    policy_reason = knowledge_policy.review_reason(rec.get('text', ''), rec, config.today(), config.stale_before())
    if policy_reason:
        return 'needs_review', policy_reason
    if NOISE_TITLE.search(title):
        return "needs_review", "測試頁或非內容頁（標題看起來不是給學生看的內容）"
    if OUTBOUND_TITLE.search(title) and rec.get("source") != "manual":
        return "needs_review", "本校學生出國交換的資料，不是給境外生的"
    # 人工整理的資料（例如國際處提供的表格）本來就有短答案（「週一至週五 9:00-17:00」），不套用字數下限
    if not has_text(rec) and rec.get("source") != "manual":
        return "needs_review", "讀不到文字（可能是圖片或掃描檔），請整理成 FAQ"
    old = OLD_NAMES.search(rec.get("text", ""))
    if old and rec.get("source") != "manual":
        return "needs_review", f"提到已改制的名稱「{old.group(0)}」，內容可能是多年前的，請向承辦人確認"
    # 人工整理的資料有自己的確認日期；內文提到舊學年（「112 年起改為女生宿舍」）是在講沿革，不是過期
    if not is_regulation(rec) and rec.get("source") != "manual":
        status = judge_dates(rec)
        if status:
            return status
    verified = as_int_date(rec.get("last_verified_at") or rec.get("fetched_at"))
    if verified and verified < int(config.stale_before().strftime("%Y%m%d")):
        return "needs_review", f"超過 {config.STALE_MONTHS} 個月沒有確認"
    return "active", ""


EVENT_RE = re.compile(r"^【(活動|轉知|招募|徵件|講座|說明會)】|說明會|講座|教育展|工作坊|博覽會")
DATE_RE = re.compile(r"(20\d\d)\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})")


def event_dates(rec: dict) -> list[dt.date]:
    out = []
    for y, m, d in DATE_RE.findall(rec.get("text", "") + " " + (rec.get("title") or "")):
        try:
            out.append(dt.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    # 標題常只寫月/日（例如「11/5（四）說明會」），當成今年
    for m, d in re.findall(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", rec.get("title") or ""):
        try:
            out.append(dt.date(config.today().year, int(m), int(d)))
        except ValueError:
            pass
    return out


def judge_dates(rec: dict) -> tuple[str, str] | None:
    """一般公告用：截止日已過、活動已結束、或內容停在舊年份／舊學年度。"""
    if rec.get("deadline_passed"):
        return "expired", f"期限已過（{rec.get('latest_date')}）"
    if EVENT_RE.search(rec.get("title") or ""):
        dates = event_dates(rec)
        if dates and max(dates) < config.today():
            return "expired", f"活動已結束（{max(dates).isoformat()}）"
    if rec.get("possibly_outdated"):
        return "needs_review", f"內容提到的最近年份是 {rec.get('latest_year_mentioned')} 年"
    years = [int(y) for y in YEAR_RE.findall(rec.get("text", "")) if 90 <= int(y) <= 130]
    if years and max(years) < config.CURRENT_ACADEMIC_YEAR:
        return "needs_review", f"寫的是 {max(years)} 學年度，目前是 {config.CURRENT_ACADEMIC_YEAR} 學年"
    return None


# PDF 用「疊字」做粗體，抽出來會變成「護照號碼護照號碼護照號碼護照號碼」「男男 男男」，還原成一次。
# 只處理中間沒有換行的連續重複，避免把清單裡剛好相鄰的同名項目（例如代理人和下一位承辦人同名）合併掉。
DOUBLED = re.compile(r"([\u4e00-\u9fff]{2,8}?)\1+")
DOUBLED_CHAR = re.compile(r"([\u4e00-\u9fff])\1 \1\1")


def clean_pdf_text(t: str) -> str:
    t = DOUBLED_CHAR.sub(r"\1", t)
    prev = None
    while prev != t:
        prev, t = t, DOUBLED.sub(r"\1", t)
    return t


def load_crawled() -> list[dict]:
    out = []
    for path in sorted(config.RAW_DIR.glob("*/chunks.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["text"] = clean_pdf_text(r.get("text", ""))
                r["source"] = f"crawler:{path.parent.name}"
                out.append(r)
    return out


def split_list(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[、,，/|]", s or "") if x.strip()]


def load_faq() -> list[dict]:
    """人工 FAQ：國際處或小組在 Google Sheet 整理後匯出成 CSV。"""
    path = config.CURATED_DIR / "faq.csv"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            q, a = row.get("問題", "").strip(), row.get("回答", "").strip()
            if not q or not a:
                continue
            valid_until = row.get("有效期限", "").strip()
            out.append({
                "chunk_id": f"faq-{i}",
                "text": f"【常見問題】{q}\n{a}",
                "title": q,
                "url": row.get("來源網址", "").strip(),
                "unit": row.get("單位", "國際處").strip() or "國際處",
                "topics": split_list(row.get("主題", "")),
                "identities": split_list(row.get("適用身分", "")) or [config.ALL_STUDENTS],
                "lang": row.get("語言", "zh").strip() or "zh",
                "risk": "normal",
                "last_verified_at": row.get("最後確認日期", "").strip(),
                "latest_date": valid_until,
                "deadline_passed": bool(valid_until) and as_int_date(valid_until) < int(config.today().strftime("%Y%m%d")),
                "source": "faq",
            })
    return out


def to_metadata(r: dict) -> dict:
    """Chroma 的 metadata 只能放單一值，所以身分、主題各用一個 true/false 欄位，方便過濾。"""
    md = {
        "title": r.get("title", "")[:200],
        "url": r.get("url", ""),
        "unit": r.get("unit", ""),
        "lang": r.get("lang", "zh"),
        "risk": r.get("risk", "normal"),
        "source": r.get("source", ""),
        "status": "active",
        "valid_until": 0 if r.get("deadline_passed") or is_regulation(r) else as_int_date(r.get("latest_date")),
        "last_verified": as_int_date(r.get("last_verified_at") or r.get("fetched_at")),
        'policy_review_status': r.get('policy_review_status', ''),
        'policy_reviewed_at': str(r.get('policy_reviewed_at', '')),
        'policy_reviewed_sha256': r.get('policy_reviewed_sha256', ''),
        "topics": "、".join(r.get("topics", [])),
    }
    for ident in config.IDENTITIES + [config.ALL_STUDENTS]:
        md[f"id_{ident}"] = ident in r.get("identities", [])
    for t in r.get("topics", []):
        md[f"t_{t}"] = True
    return md


def main() -> int:
    keep, review = split_records()
    if not keep and not review:
        print(f"找不到資料。請先執行爬蟲，或在 {config.CURATED_DIR / 'faq.csv'} 放入 FAQ。")
        return 1

    import chromadb
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    try:  # 每次重建，確保過期或已刪除的資料不會殘留
        client.delete_collection(COLLECTION)
    except Exception:  # noqa: BLE001
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    batch = 64
    for i in range(0, len(keep), batch):
        part = keep[i:i + batch]
        col.add(ids=[str(r["chunk_id"]) for r in part],
                documents=[r["text"] for r in part],
                metadatas=[to_metadata(r) for r in part],
                embeddings=embedder.embed_passages([r["text"] for r in part]))
        print(f"  已寫入 {min(i + batch, len(keep))}/{len(keep)}")

    review_path = write_review(review)
    n_exp = sum(s == "expired" for s, _, _ in review)
    print(f"完成：可用 {len(keep)} 段；待確認 {len(review) - n_exp} 段、已過期 {n_exp} 段（不會被拿來回答）")
    print(f"待確認清單：{review_path}")
    return 0


MANUAL_DIR = config.CURATED_DIR / "人工整理"


def load_manual() -> list[dict]:
    """人工整理的 Markdown（例如整頁圖片的投影片、掃描檔，人工或 Claude 看圖整理後放這裡）。

    檔案開頭用 --- 包住的欄位：title、url、unit、topics、identities、lang、last_verified、note。
    內文依「## 標題」切段，每段開頭帶上文件標題和段落標題，檢索時才知道在講什麼。
    """
    out = []
    for path in sorted(MANUAL_DIR.glob("*.md")) if MANUAL_DIR.exists() else []:
        raw = path.read_text(encoding="utf-8-sig")
        meta, body = {}, raw
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = raw[m.end():]
        title = meta.get("title") or path.stem
        doc = "md" + hashlib.sha1(path.name.encode()).hexdigest()[:10]
        sections = re.split(r"(?m)^## +", body)
        intro, sections = sections[0].strip(), sections[1:]
        chunks = ([("", intro)] if intro else []) + [(s.split("\n", 1)[0].strip(), s.split("\n", 1)[1].strip() if "\n" in s else "")
                                                     for s in sections]
        for i, (head, text) in enumerate(c for c in chunks if c[1]):
            # 段落第一行是「網址：https://…」時，這一段的來源網址用它（例如每棟宿舍各有自己的頁面）
            url = meta.get("url", "")
            m2 = re.match(r"(?:網址|URL)[:：]\s*(\S+)\s*\n", text)
            if m2:
                url, text = m2.group(1), text[m2.end():]
            out.append({
                "chunk_id": f"{doc}-{i}",
                "text": f"【{title}｜{head}】\n{text}" if head else f"【{title}】\n{text}",
                # 每段用自己的標題：檢索時同一個標題最多取 2 段，整份用同一個標題會只剩 2 家餐廳
                "title": f"{title}｜{head}" if head else title, "url": url, "unit": meta.get("unit", "國際處"),
                "topics": split_list(meta.get("topics", "")),
                "identities": split_list(meta.get("identities", "")) or [config.ALL_STUDENTS],
                "lang": meta.get("lang", "zh"), "risk": "normal",
                "last_verified_at": meta.get("last_verified", ""), "latest_date": "", "deadline_passed": False,
                'policy_review_status': meta.get('policy_review_status', ''),
                'policy_reviewed_at': meta.get('policy_reviewed_at', ''),
                'policy_reviewed_sha256': meta.get('policy_reviewed_sha256', ''),
                "source": "manual",
            })
    return out


def split_records() -> tuple[list[dict], list[tuple]]:
    """讀入所有資料，分成可用的和被擋下來的（附原因）。本機版和雲端版共用。

    人工整理的檔案優先：同一個網址如果已經有人工整理版，爬下來的（通常是 OCR 或抽不出字的）就不用。
    """
    keep, review = [], []
    manual = load_manual()
    manual_urls = {r["url"] for r in manual if r["url"]}
    crawled = [r for r in load_crawled() if r.get("url") not in manual_urls]
    for r in crawled + load_faq() + manual:
        status, reason = judge(r)
        if status == "active":
            keep.append(r)
        else:
            review.append((status, reason, r))
    return keep, review


def write_review(review: list[tuple]):
    config.CURATED_DIR.mkdir(parents=True, exist_ok=True)
    review_path = config.CURATED_DIR / "待確認清單.csv"
    with open(review_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["狀態", "原因", "標題", "網址", "內容開頭"])
        seen = set()
        for status, reason, r in review:
            key = (r.get("url"), reason)
            if key in seen:
                continue
            seen.add(key)
            w.writerow([{"expired": "已過期", "needs_review": "待確認"}[status], reason,
                        r.get("title", ""), r.get("url", ""), r.get("text", "")[:80].replace("\n", " ")])
    return review_path


if __name__ == "__main__":
    sys.exit(main())
