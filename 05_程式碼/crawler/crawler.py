"""校園 RAG 專案：國際處網站爬蟲 agent

做的事：
  1. 先讀網站的 robots.txt，只爬允許的頁面，每頁之間間隔數秒
  2. 從 config.yaml 的起始頁出發，沿著站內連結往下爬（限定網址規則與深度）
  3. 抽出內文、下載附件（PDF、Word、Excel、PowerPoint，含舊版 .doc／.xls／.ppt 和 .odt）並抽出文字；
     掃描檔或整頁圖片的投影片，有裝 Tesseract 時自動 OCR
  4. 自動標記：主題（食衣住行育樂＋居留、工作、獎學金…）、適用身分、語言、風險等級、可能過期
  5. 切成段落（chunk），輸出成 RAG 可以直接匯入的 JSONL
  6. 跟上一次的結果比對，列出新增、修改、消失的頁面，產生報告

用法：
  python crawler.py                    # 依 config.yaml 爬全部網站
  python crawler.py --site oia         # 只爬某個網站
  python crawler.py --max-pages 20     # 先小量試跑
  python crawler.py --config my.yaml   # 用別的設定檔
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import sys
import time
import urllib.robotparser
from collections import Counter, deque
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup

import taxonomy

try:  # 讓 Python 用作業系統的憑證庫，解決部分台灣學校網站的 SSL 憑證問題
    import truststore
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

try:
    import trafilatura
except ImportError:  # trafilatura 不是必要，沒有就用備用抽取法
    trafilatura = None

HERE = Path(__file__).resolve().parent
GENERIC_LINK = re.compile(r"^(pdf|doc|docx|下載|點此|點我|按此|here|click here|download|附件|檔案|this document|this file|this link|link|more|詳見|詳細內容|\(?\d+(\.\d+)?\s*[KM]B?\)?)$", re.I)
ATTACH_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt"}
CJK = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------- 工具函式
def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def normalize_url(url: str, keep_params: list[str]) -> str:
    """去掉 #錨點、只保留設定檔指定的參數（例如 Lang），讓同一頁不會被爬兩次。"""
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if k in keep_params]
    return urlunparse((p.scheme, p.netloc.lower(), p.path, "", urlencode(q), ""))


def page_key(url: str) -> str:
    """判斷「是不是同一頁」用的鍵。

    學校網站的公告網址會帶「,r342」這類尾巴，只是標記從哪個公告列表點進來
    （r342＝最新公告、r565＝置頂公告…），內容完全一樣。網址照原樣抓，
    但比對有沒有抓過時把尾巴拿掉，同一則公告只存一份。
    """
    return re.sub(r",r\d+(?=\.php)", "", url)


def guess_lang(url: str, text: str) -> str:
    q = dict(parse_qsl(urlparse(url).query))
    if q.get("Lang", "").lower().startswith("en"):
        return "en"
    if q.get("Lang", "").lower().startswith("zh"):
        return "zh"
    if not text:
        return "unknown"
    ratio = len(CJK.findall(text)) / max(len(text), 1)
    return "zh" if ratio > 0.15 else "en"


# ---------------------------------------------------------------- 內文抽取
NOISE_SELECTORS = ["script", "style", "noscript", "nav", "header", "footer", "form", "iframe"]


def element_text(el) -> str:
    """把 HTML 區塊轉成純文字，表格每列用 | 隔開，保留表格結構。"""
    for table in el.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(" | ".join(cells))
        table.replace_with(BeautifulSoup("<p>" + "</p><p>".join(rows) + "</p>", "lxml").body or "")
    return el.get_text("\n", strip=True)


def extract_html(html: str, url: str, content_selectors: list[str]) -> tuple[str, str]:
    """回傳 (標題, 內文)。
    順序：1) 設定檔指定的內文區塊（最準） 2) trafilatura 3) 文字最多的區塊。
    """
    soup = BeautifulSoup(html, "lxml")
    title = ""
    for sel in ["h1", "h2.hdline", ".mpgtitle h2", "h2", "title"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(" ", strip=True)
            break
    # <title> 常是「頁名 - 國立中央大學國際事務處」，只留前段
    title = re.split(r"\s+[-|｜]\s+", title)[0].strip()

    for tag in soup(NOISE_SELECTORS):  # 先拿掉選單、頁首頁尾，避免混進內文
        tag.decompose()

    text = ""
    for sel in content_selectors:
        parts = [element_text(el) for el in soup.select(sel)]
        joined = "\n\n".join(p for p in parts if p)
        if len(joined) >= 40:
            text = joined
            break
    if not text and trafilatura is not None:
        text = trafilatura.extract(str(soup), url=url, include_tables=True,
                                   include_links=False, favor_recall=True) or ""
    if len(text) < 80:
        candidates = soup.select("main, article, section, div")
        best = max(candidates, key=lambda e: len(e.get_text(strip=True)), default=soup.body or soup)
        fallback = element_text(best)
        if len(fallback) > len(text):
            text = fallback
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text


# 舊格式和圖片檔的處理：
#   .doc／.ppt  → 用 LibreOffice（免費）或電腦上的 Word／PowerPoint 轉成新格式再抽文字
#   .xls        → xlrd 直接讀（不用另外裝軟體）
#   .odt        → 直接讀檔案裡的 XML（不用另外裝軟體）
#   PDF、PPTX 抽不出文字（掃描檔、整頁圖片）→ 有裝 Tesseract 就自動 OCR，結果標成「OCR，請人工確認」
SOFFICE_PATHS = [r"C:\Program Files\LibreOffice\program\soffice.exe",
                 r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
TESSERACT_PATHS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                   r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
OCR_NOTE = "OCR 自動辨識，重要內容（電話、金額、日期）請人工確認"


def _find(cmds: list[str], paths: list[str]) -> str | None:
    import shutil
    for c in cmds:
        found = shutil.which(c)
        if found:
            return found
    return next((x for x in paths if Path(x).exists()), None)


def convert_legacy(data: bytes, ext: str) -> tuple[bytes | None, str]:
    """把 .doc／.ppt 轉成 .docx／.pptx。回傳 (新檔內容, 說明)。"""
    import subprocess
    import tempfile
    target = {".doc": "docx", ".ppt": "pptx"}[ext]
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{ext}"
        src.write_bytes(data)
        out = Path(tmp) / f"in.{target}"
        soffice = _find(["soffice", "libreoffice"], SOFFICE_PATHS)
        if soffice:
            subprocess.run([soffice, "--headless", "--convert-to", target, "--outdir", tmp, str(src)],
                           capture_output=True, timeout=120)
            if out.exists():
                return out.read_bytes(), "LibreOffice 轉檔"
        if sys.platform == "win32":  # 沒有 LibreOffice 時，改用電腦上的 Office
            try:
                import win32com.client
                if ext == ".doc":
                    app = win32com.client.DispatchEx("Word.Application")
                    app.Visible = False
                    doc = app.Documents.Open(str(src), ReadOnly=True)
                    doc.SaveAs2(str(out), FileFormat=16)
                    doc.Close(False)
                else:
                    app = win32com.client.DispatchEx("PowerPoint.Application")
                    pres = app.Presentations.Open(str(src), ReadOnly=True, WithWindow=False)
                    pres.SaveAs(str(out), 24)
                    pres.Close()
                app.Quit()
                if out.exists():
                    return out.read_bytes(), "Office 轉檔"
            except Exception:  # noqa: BLE001
                pass
    return None, f"{ext} 為舊格式：請安裝 LibreOffice（免費）後重跑，或人工轉成新格式"


def ocr_images(images: list[bytes]) -> str:
    """有裝 Tesseract 才會做；沒裝就回傳空字串。"""
    if not images:
        return ""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    exe = _find(["tesseract"], TESSERACT_PATHS)
    if not exe:
        return ""
    pytesseract.pytesseract.tesseract_cmd = exe
    parts = []
    for b in images:
        try:
            parts.append(pytesseract.image_to_string(Image.open(io.BytesIO(b)), lang="chi_tra+eng").strip())
        except Exception:  # noqa: BLE001  缺中文語言包時改用英文
            try:
                parts.append(pytesseract.image_to_string(Image.open(io.BytesIO(b)), lang="eng").strip())
            except Exception:  # noqa: BLE001
                pass
    return "\n\n".join(p for p in parts if p)


def pdf_page_images(data: bytes, max_pages: int = 30) -> list[bytes]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    out = []
    pdf = pdfium.PdfDocument(data)
    for i in range(min(len(pdf), max_pages)):
        img = pdf[i].render(scale=2).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    pdf.close()
    return out


def extract_attachment(data: bytes, ext: str) -> tuple[str, str]:
    """回傳 (內文, 備註)。"""
    try:
        if ext in (".doc", ".ppt"):
            new, how = convert_legacy(data, ext)
            if new is None:
                return "", how
            text, note = extract_attachment(new, ".docx" if ext == ".doc" else ".pptx")
            return text, "；".join(x for x in (f"{how}（原檔 {ext}）", note) if x)
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = [p.extract_text() or "" for p in reader.pages]
            text = "\n\n".join(pages).strip()
            if len(text) > 50:
                return text, ""
            ocr = ocr_images(pdf_page_images(data))
            if ocr:
                return ocr, OCR_NOTE
            return text, "PDF 抽不出文字，可能是掃描檔；安裝 Tesseract 後重跑可自動 OCR"
        if ext == ".docx":
            import docx
            d = docx.Document(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for t in d.tables:
                for row in t.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
            return "\n".join(parts), ""
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"## {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts), ""
        if ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(file_contents=data)
            parts = []
            for ws in wb.sheets():
                parts.append(f"## {ws.name}")
                for r in range(ws.nrows):
                    cells = [str(c).strip() for c in ws.row_values(r) if str(c).strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts), ""
        if ext == ".odt":
            import zipfile
            from xml.etree import ElementTree as ET
            root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml"))
            ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
            parts = ["".join(el.itertext()).strip() for el in root.iter() if el.tag in (ns + "p", ns + "h")]
            return "\n".join(p for p in parts if p), ""
        if ext == ".pptx":
            from pptx import Presentation
            from pptx.enum.shapes import MSO_SHAPE_TYPE
            prs = Presentation(io.BytesIO(data))
            parts, images = [], []
            for i, slide in enumerate(prs.slides, 1):
                parts.append(f"## 投影片 {i}")
                for shp in slide.shapes:
                    if shp.has_text_frame and shp.text_frame.text.strip():
                        parts.append(shp.text_frame.text)
                    elif shp.shape_type == MSO_SHAPE_TYPE.PICTURE:
                        images.append((i, shp.image.blob))
            text = "\n".join(parts)
            body = re.sub(r"(## 投影片 \d+|\s)", "", text)
            if len(body) < 40 * max(len(prs.slides), 1) // 4 and images:  # 幾乎都是圖片：逐張 OCR
                ocr_parts = []
                for i, blob in images:
                    t = ocr_images([blob])
                    if t:
                        ocr_parts.append(f"## 投影片 {i}\n{t}")
                if ocr_parts:
                    return "\n".join(ocr_parts), OCR_NOTE
                return text, "投影片大多是圖片；安裝 Tesseract 後重跑可自動 OCR"
            return text, ""
    except Exception as e:  # noqa: BLE001
        return "", f"解析失敗：{e}"
    return "", f"{ext} 尚未支援"


# ---------------------------------------------------------------- 自動標記
_PATTERNS: dict[tuple, re.Pattern] = {}


def count_hits(text_lower: str, words: list[str]) -> int:
    """長的關鍵字優先比對、不重複計算（「居留證」不會同時算成「居留」）。
    英文關鍵字要整個字相符，避免 arc 被算進 search。"""
    key = tuple(words)
    if key not in _PATTERNS:
        alts = []
        for w in sorted(words, key=len, reverse=True):
            w = re.escape(w.lower())
            alts.append(rf"\b{w}(?:s|es)?\b" if re.fullmatch(r"[a-z0-9\\ \-]+", w) else w)  # 英文允許複數
        _PATTERNS[key] = re.compile("|".join(alts))
    return len(_PATTERNS[key].findall(text_lower))


def tag_document(title: str, text: str) -> dict:
    body = f"{title}\n{title}\n{text}".lower()  # 標題算兩次，加重權重
    topic_hits = {k: count_hits(body, v) for k, v in taxonomy.TOPICS.items()}
    # 短文件（例如一頁表單）出現一次就算；長文件至少要兩次，避免順帶提到就被歸類
    need = 1 if len(text) < 300 else taxonomy.MIN_TOPIC_HITS
    topics = [k for k, n in sorted(topic_hits.items(), key=lambda x: -x[1]) if n >= need]
    ident_hits = {k: count_hits(body, v) for k, v in taxonomy.IDENTITIES.items()}
    identities = [k for k, n in ident_hits.items() if n > 0] or [taxonomy.DEFAULT_IDENTITY]
    risk = "high" if set(topics) & taxonomy.HIGH_RISK_TOPICS else "normal"
    return {"topics": topics, "identities": identities, "risk": risk,
            "topic_hits": {k: v for k, v in topic_hits.items() if v}}


DATE_PATTERNS = [
    (re.compile(r"(20\d{2})[./\-年](\d{1,2})[./\-月](\d{1,2})"), "ad"),
    (re.compile(r"(?<!\d)(1[01]\d)[./\-年](\d{1,2})[./\-月](\d{1,2})"), "roc"),
    (re.compile(r"(?<!\d)(1[01]\d)(\d{2})(\d{2})(?!\d)"), "roc"),        # 1150126
    (re.compile(r"(?<!\d)(1[01]\d)\s*[-－~～]\s*1[01]\d\s*學?年度"), "roc_year"),  # 109-110年度
    (re.compile(r"(?<!\d)(1[01]\d)\s*學?年度"), "roc_year"),
]


def find_years(text: str) -> list[int]:
    years = []
    for pat, kind in DATE_PATTERNS:
        for m in pat.finditer(text):
            y = int(m.group(1))
            if kind.startswith("roc"):
                y += 1911
            if 2000 <= y <= dt.date.today().year + 2:
                years.append(y)
    return years


def stale_hint(text: str) -> tuple[int | None, bool]:
    """有提到年份、但最近的年份已經是兩年前以上 → 可能過期，需要人工確認。"""
    years = find_years(text)
    if not years:
        return None, False
    latest = max(years)
    return latest, latest < dt.date.today().year - 1


def full_dates(text: str) -> list[dt.date]:
    """抓出完整的年月日（西元或民國），用來判斷申請期限是否已過。"""
    out = []
    for pat, kind in DATE_PATTERNS[:3]:
        for m in pat.finditer(text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if kind == "roc":
                y += 1911
            try:
                out.append(dt.date(y, mo, d))
            except ValueError:
                pass
    return out


DEADLINE_WORDS = re.compile(r"截止|期限|前|deadline|due|until|before", re.I)


def deadline_status(text: str) -> tuple[str | None, bool]:
    """有寫期限而且所有日期都已經過了 → 這則公告多半已失效。"""
    dates = full_dates(text)
    if not dates or not DEADLINE_WORDS.search(text):
        return None, False
    latest = max(dates)
    return latest.isoformat(), latest < dt.date.today()


# ---------------------------------------------------------------- 切段
SENT_END = "。！？；.!?;"


def _sentence_cut(buf: str, lo: float = 0.5) -> int:
    """在 buf 後半段找最後一個句子結尾，回傳切點（句號後面）；找不到回傳 -1。"""
    cut = max(buf.rfind(ch, int(len(buf) * lo)) for ch in SENT_END)
    return cut + 1 if cut > 0 else -1


SAVED_DIR_NAME = "網頁存檔"   # 和「原始資料」同一層：04_知識庫資料/網頁存檔/<網站名稱>/*.html
SAVED_FROM = re.compile(r"<!--\s*saved from url=\(\d+\)(\S+?)\s*-->", re.I)


def load_saved_pages(folder: Path, keep_params: list[str]) -> dict[str, dict]:
    """讀入用瀏覽器「另存網頁」存下來的 .html。網址從 Chrome／Edge 自動加的
    <!-- saved from url=... --> 註解、canonical 或 og:url 讀出來；都沒有時，檔名第一行寫網址也可以（見 crawler-agent.md）。"""
    out = {}
    if not folder.exists():
        return out
    for f in sorted(folder.glob("*.htm*")):
        raw = f.read_bytes()
        html = raw.decode("utf-8", errors="replace")
        if "\ufffd" in html[:5000]:
            html = raw.decode("big5", errors="replace")
        url = None
        m = SAVED_FROM.search(html[:2000])
        if m:
            url = m.group(1)
        else:
            soup = BeautifulSoup(html[:200000], "lxml")
            tag = soup.find("link", rel="canonical") or soup.find("meta", property="og:url")
            url = tag and (tag.get("href") or tag.get("content"))
        if not url or not url.startswith("http"):
            log(f"  人工存檔 {f.name} 讀不到原始網址，跳過（請用 Chrome 或 Edge 另存網頁）")
            continue
        url = normalize_url(url, keep_params)
        saved_at = dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
        out[page_key(url)] = {"url": url, "html": html, "file": f.name, "saved_at": saved_at}
    if out:
        log(f"讀到 {len(out)} 個人工存檔網頁：{folder}")
    return out


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """依段落切，盡量不把句子切斷；中文以字數計。

    PDF 抽出來的文字常在句子中間換行，所以每段收尾時，往回找最近的句號、問號或分號，
    把後面半句留給下一段；太長的單一段落也優先切在句子結尾，找不到才硬切。
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 1 <= size:
            buf = f"{buf}\n{p}" if buf else p
            continue
        if buf:
            cut = _sentence_cut(buf)
            head, tail = (buf[:cut], buf[cut:].strip()) if 0 < cut < len(buf) else (buf, "")
            chunks.append(head)
            carry = head[-overlap:] if overlap and not tail else ""
            buf = "\n".join(x for x in (carry, tail, p) if x)
        else:
            buf = p
        while len(buf) > size:  # 單一段落太長
            cut = _sentence_cut(buf[:size])
            cut = cut if cut > 0 else size
            chunks.append(buf[:cut])
            buf = buf[max(cut - overlap, 1):] if cut == size else buf[cut:].strip()
    if buf.strip():
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------- 爬蟲本體
class Crawler:
    def __init__(self, cfg: dict, site: dict, out_dir: Path, max_pages: int | None):
        self.cfg, self.site = cfg, site
        self.out = out_dir
        # 學校官網用 ?top=1&num=43 區分頁面，這類網站在 config.yaml 另外指定要保留哪些參數
        self.keep = site.get("keep_query_params", cfg.get("keep_query_params", ["Lang"]))
        self.allow = [re.compile(p) for p in site["allow"]]
        self.deny = [re.compile(p) for p in site.get("deny", [])]
        # 網站導覽、公告列表這類頁面只拿來找連結，不當成知識內容
        self.link_only = [re.compile(p) for p in site.get("link_only", [])]
        self.content_selectors = site.get("content_selectors", [])
        self.attach_allow = [re.compile(x, re.I) for x in site.get("attachment_allow", [])]
        self.attach_deny = [re.compile(x, re.I) for x in site.get("attachment_deny", [])]
        self.saved = load_saved_pages(out_dir.parent.parent / SAVED_DIR_NAME / site["name"], self.keep)
        self.saved_used: set[str] = set()
        self.attach_hosts = set(site.get("attachment_hosts", [])) | {urlparse(s).netloc for s in site["seeds"]}
        self.max_pages = max_pages or site.get("max_pages", cfg.get("max_pages", 300))
        self.max_depth = site.get("max_depth", cfg.get("max_depth", 3))
        self.delay = cfg.get("delay_seconds", 2.0)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = cfg["user_agent"]
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.records: list[dict] = []
        self.skipped: list[dict] = []
        self.index_pages: list[dict] = []
        self.last_request = 0.0
        # 涵蓋率檢查用：頁面上找到的範圍內連結、外部網站連結、最後沒爬到的連結
        self.discovered: dict[str, dict] = {}
        self.external: dict[str, dict] = {}
        self.fetched: set[str] = set()
        self.leftover: list[str] = []

    # -- 禮貌與規則
    def robots_ok(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.get(f"{base}/robots.txt", count=False)
                if r is not None and r.status_code == 200:
                    rp.parse(r.text.splitlines())
                elif r is not None and r.status_code in (401, 403):
                    rp.disallow_all = True
                else:  # 沒有 robots.txt = 全部允許
                    rp.parse([])
                self.robots[base] = rp
                crawl_delay = rp.crawl_delay(self.cfg["user_agent"])
                if crawl_delay:
                    self.delay = max(self.delay, float(crawl_delay))
            except Exception as e:  # noqa: BLE001
                log(f"讀不到 {base}/robots.txt（{e}），保守起見跳過這個網站")
                self.robots[base] = None
        rp = self.robots[base]
        return bool(rp) and rp.can_fetch(self.cfg["user_agent"], url)

    def get(self, url: str, count: bool = True):
        wait = self.delay - (time.time() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                self.last_request = time.time()
                r = self.s.get(url, timeout=self.cfg.get("timeout", 20))
                if r.status_code in (429, 503):
                    time.sleep(10 * (attempt + 1))
                    continue
                return r
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                log(f"  重試 {url}（{e.__class__.__name__}）")
                time.sleep(3 * (attempt + 1))
        return None

    def allowed_page(self, url: str) -> bool:
        return any(p.search(url) for p in self.allow) and not any(p.search(url) for p in self.deny)

    def is_attachment(self, url: str) -> bool:
        p = urlparse(url)
        if Path(p.path).suffix.lower() not in ATTACH_EXT or p.netloc not in self.attach_hosts:
            return False
        # 設定了 attachment_allow 的網站（例如移民署），只收檔名符合的附件，避免抓進一堆不相關的表單
        from urllib.parse import unquote
        if any(r.search(unquote(url)) for r in self.attach_deny):  # 例如舊學年度的校曆
            return False
        return not self.attach_allow or any(r.search(unquote(url)) for r in self.attach_allow)

    # -- 主迴圈
    def run(self) -> None:
        queue = deque((normalize_url(u, self.keep), 0, None) for u in self.site["seeds"])
        seen: set[str] = set()
        pages = 0
        while queue and pages < self.max_pages:
            url, depth, parent = queue.popleft()
            if page_key(url) in seen:
                continue
            seen.add(page_key(url))
            if not self.robots_ok(url):
                if self.use_saved(url, depth, parent, "robots.txt 不允許"):
                    pages += 1
                    continue
                self.skipped.append({"url": url, "reason": "robots.txt 不允許"})
                continue
            try:
                r = self.get(url)
            except Exception as e:  # noqa: BLE001
                if self.use_saved(url, depth, parent, f"連線失敗：{e.__class__.__name__}"):
                    pages += 1
                    continue
                self.skipped.append({"url": url, "reason": f"連線失敗：{e.__class__.__name__}"})
                continue
            if r is None or r.status_code != 200:
                code = getattr(r, "status_code", "?")
                if self.use_saved(url, depth, parent, f"HTTP {code}"):
                    pages += 1
                    continue
                self.skipped.append({"url": url, "reason": f"HTTP {code}"})
                continue
            pages += 1
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip()
            if self.is_attachment(url) or "html" not in ctype:
                self.handle_attachment(url, r, parent)
                continue
            r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
            self.fetched.add(page_key(url))
            links = self.handle_page(url, r.text, depth, parent)
            log(f"[{pages}/{self.max_pages}] 深度{depth} {url}")
            if depth >= self.max_depth:
                continue
            for link in links:
                if page_key(link) not in seen:
                    queue.append((link, depth + 1, url))
        if queue:
            log(f"已達頁數上限 {self.max_pages}，還有 {len(queue)} 個連結沒爬")
            self.leftover = [page_key(u) for u, _, _ in queue]
        for key, item in self.saved.items():
            if key not in self.saved_used and key not in self.fetched:
                self.use_saved(item["url"], 0, None, "起始頁沒有，直接匯入")

    def use_saved(self, url: str, depth: int, parent: str | None, why: str) -> bool:
        """網站擋爬蟲（403、robots.txt）時，改用人工用瀏覽器另存的網頁。
        不是繞過封鎖：是人自己打開頁面、自己存檔，爬蟲只負責整理成和其他資料一樣的格式。"""
        item = self.saved.get(page_key(url))
        if not item or page_key(url) in self.saved_used:
            return False
        self.saved_used.add(page_key(url))
        self.fetched.add(page_key(url))
        before = len(self.records)
        self.handle_page(item["url"], item["html"], self.max_depth, parent)  # 存檔頁不再往下爬
        for rec in self.records[before:]:
            rec["fetched_at"] = item["saved_at"]      # 資料日期用存檔那天，不是今天
            rec["saved_copy"] = item["file"]
            rec["fetch_note"] = f"{why}，改用人工存檔 {item['file']}"
        log(f"  人工存檔 {item['file']} → {item['url']}（{why}）")
        return True

    def coverage(self) -> dict:
        """找到但沒爬的連結和原因，給 check_crawl.py 產生驗收報告用。"""
        skipped = {page_key(s["url"]): s["reason"] for s in self.skipped}
        leftover = set(self.leftover)
        missing = []
        for key, d in self.discovered.items():
            if key in self.fetched or key in skipped:
                continue
            if key in leftover:
                reason = f"達頁數上限（{self.max_pages}）"
            elif d["depth"] > self.max_depth:
                reason = f"超過深度上限（{self.max_depth}）"
            else:
                reason = "未抓到（原因不明，請檢查）"
            missing.append({**d, "reason": reason})
        return {"site": self.site["name"], "max_pages": self.max_pages, "max_depth": self.max_depth,
                "fetched": len(self.fetched), "discovered": len(self.discovered),
                "skipped": [{"url": u, "reason": r} for u, r in skipped.items()],
                "missing": missing,
                "external": sorted(self.external.values(), key=lambda x: -x["count"])}

    def handle_page(self, url: str, html: str, depth: int, parent: str | None) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        links, attachments = [], []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            absu = normalize_url(urljoin(url, href), self.keep)
            in_scope = self.is_attachment(absu) or self.allowed_page(absu)
            host = urlparse(absu).netloc
            if in_scope and page_key(absu) not in self.discovered:
                self.discovered[page_key(absu)] = {"url": absu, "depth": depth + 1, "parent": url,
                                                   "text": a.get_text(" ", strip=True)[:60]}
            elif not in_scope and absu.startswith("http") and host and host not in self.attach_hosts:
                e = self.external.setdefault(host, {"host": host, "count": 0, "examples": []})
                e["count"] += 1
                if len(e["examples"]) < 3 and a.get_text(strip=True):
                    e["examples"].append({"text": a.get_text(" ", strip=True)[:40], "url": absu})
            if self.is_attachment(absu):
                attachments.append({"url": absu, "text": a.get_text(" ", strip=True)})
                links.append(absu)
            elif self.allowed_page(absu):
                links.append(absu)
        links = list(dict.fromkeys(links))
        if any(p.search(url) for p in self.link_only):
            self.index_pages.append({"url": url, "links_found": len(links)})
            return links
        title, text = extract_html(html, url, self.content_selectors)
        raw_name = sha(url) + ".html"
        (self.out / "raw" / raw_name).write_text(html, encoding="utf-8")
        self.add_record(url, "page", title, text, parent, raw_name,
                        extra={"attachments": attachments, "depth": depth})
        return links

    def handle_attachment(self, url: str, r, parent: str | None) -> None:
        ext = Path(urlparse(url).path).suffix.lower()
        if not ext:
            ctype = r.headers.get("Content-Type", "")
            ext = {"application/pdf": ".pdf"}.get(ctype.split(";")[0].strip(), ".bin")
        raw_name = sha(url) + ext
        (self.out / "raw" / raw_name).write_bytes(r.content)
        text, note = extract_attachment(r.content, ext)
        title = Path(urlparse(url).path).name
        # 附件標題用父頁面上的連結文字比較好懂；連結文字只有「pdf」「下載」這種時，改用檔名
        for rec in self.records:
            for att in rec.get("attachments", []):
                if att["url"] == url and att["text"] and not GENERIC_LINK.match(att["text"].strip()):
                    title = att["text"]
        from urllib.parse import unquote
        title = unquote(title)
        if GENERIC_LINK.match(title.strip()):
            title = unquote(Path(urlparse(url).path).stem)
        log(f"  附件 {title}（{len(text)} 字）{note}")
        self.add_record(url, "attachment", title, text, parent, raw_name, extra={"note": note})

    def add_record(self, url, kind, title, text, parent, raw_name, extra=None):
        tags = tag_document(title, text)
        latest_year, stale = stale_hint(text)
        latest_date, deadline_passed = deadline_status(text)
        rec = {
            "id": sha(url),
            "url": url,
            "site": self.site["name"],
            "unit": self.site.get("unit", ""),
            "kind": kind,
            "title": title,
            "lang": guess_lang(url, text),
            "parent_url": parent,
            **tags,
            "latest_year_mentioned": latest_year,
            "possibly_outdated": stale,
            "latest_date": latest_date,
            "deadline_passed": deadline_passed,
            "chars": len(text),
            "text_hash": sha(text),
            "raw_file": f"raw/{raw_name}",
            "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
            "text": text,
        }
        if extra:
            rec.update(extra)
        self.records.append(rec)


# ---------------------------------------------------------------- 輸出與比對
def write_outputs(out: Path, records: list[dict], skipped: list[dict], cfg: dict) -> dict:
    size, overlap = cfg.get("chunk_size", 500), cfg.get("chunk_overlap", 80)
    with open(out / "documents.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_chunks = 0
    keys = ["url", "site", "unit", "kind", "title", "lang", "topics", "identities",
            "risk", "possibly_outdated", "latest_year_mentioned", "latest_date",
            "deadline_passed", "fetched_at"]
    with open(out / "chunks.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            if r["chars"] < cfg.get("min_chars", 40):
                continue
            for i, c in enumerate(chunk_text(r["text"], size, overlap)):
                meta = {k: r[k] for k in keys}
                # 每段開頭帶上標題，檢索時才知道這段在講哪一頁
                f.write(json.dumps({"chunk_id": f"{r['id']}-{i}", "doc_id": r["id"],
                                    "text": f"【{r['title']}】\n{c}", **meta},
                                   ensure_ascii=False) + "\n")
                n_chunks += 1
    with open(out / "skipped.jsonl", "w", encoding="utf-8") as f:
        for s in skipped:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return {"chunks": n_chunks}


def diff_state(out: Path, records: list[dict]) -> dict:
    state_file = out / "state.json"
    old = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    new = {r["url"]: {"hash": r["text_hash"], "title": r["title"]} for r in records}
    changes = {
        "new": [u for u in new if u not in old],
        "changed": [u for u in new if u in old and old[u]["hash"] != new[u]["hash"]],
        "gone": [u for u in old if u not in new],
    }
    state_file.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    return changes


def write_report(out: Path, site: dict, records, skipped, changes, extra, started) -> Path:
    topic_c = Counter(t for r in records for t in r["topics"])
    ident_c = Counter(i for r in records for i in r["identities"])
    lang_c = Counter(r["lang"] for r in records)
    lines = [
        f"# 爬取報告：{site.get('unit', site['name'])}",
        "",
        f"- 執行時間：{started:%Y-%m-%d %H:%M} ～ {dt.datetime.now():%H:%M}",
        f"- 頁面 {sum(r['kind'] == 'page' for r in records)} 個、附件 {sum(r['kind'] == 'attachment' for r in records)} 個、"
        f"切成 {extra['chunks']} 段；跳過 {len(skipped)} 個",
        "- 語言：" + "、".join(f"{k} {v}" for k, v in lang_c.most_common()),
        f"- 與上次相比：新增 {len(changes['new'])}、內容變動 {len(changes['changed'])}、消失 {len(changes['gone'])}",
        f"- 只用來找連結的目錄頁（網站導覽、公告列表）：{extra.get('index_pages', 0)} 個",
        "",
        "## 主題覆蓋",
        "",
        "| 主題 | 文件數 |",
        "| --- | --- |",
    ]
    for t in taxonomy.TOPICS:
        n = topic_c.get(t, 0)
        lines.append(f"| {t} | {n}{'（缺資料，需向單位索取）' if n == 0 else ''} |")
    lines += ["", "## 適用身分", "", "| 身分 | 文件數 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in ident_c.most_common()]

    def table(title, rows, cols):
        if not rows:
            return []
        out_l = ["", f"## {title}", "", "| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
        return out_l + rows

    lines += table("期限已過的公告（建議下架或不放進知識庫）",
                   [f"| {r['title']} | {r['latest_date']} | {r['url']} |"
                    for r in records if r.get("deadline_passed")], ["標題", "最晚日期", "網址"])
    lines += table("可能過期（提到的最近年份是兩年前以前）",
                   [f"| {r['title']} | {r['latest_year_mentioned']} | {r['url']} |"
                    for r in records if r["possibly_outdated"]], ["標題", "最近年份", "網址"])
    lines += table("高風險主題（居留、工作、緊急）",
                   [f"| {r['title']} | {'、'.join(r['topics'])} | {r['url']} |"
                    for r in records if r["risk"] == "high"], ["標題", "主題", "網址"])
    lines += table("需要人工處理的附件",
                   [f"| {r['title']} | {r.get('note')} | {r['url']} |"
                    for r in records if r["kind"] == "attachment" and r.get("note")], ["檔名", "原因", "網址"])
    lines += table("內容很少的頁面（可能是 JavaScript 載入或連結集合頁）",
                   [f"| {r['title']} | {r['chars']} | {r['url']} |"
                    for r in records if r["kind"] == "page" and r["chars"] < 80], ["標題", "字數", "網址"])
    lines += table("跳過的網址", [f"| {s['url']} | {s['reason']} |" for s in skipped], ["網址", "原因"])
    if changes["changed"] or changes["gone"]:
        lines += table("與上次相比有變動", [f"| 內容變動 | {u} |" for u in changes["changed"]] +
                       [f"| 頁面消失 | {u} |" for u in changes["gone"]], ["狀態", "網址"])
    path = out / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- 進入點
def main() -> int:
    ap = argparse.ArgumentParser(description="校園 RAG 專案爬蟲")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--site", help="只爬指定 name 的網站")
    ap.add_argument("--max-pages", type=int, help="覆寫頁數上限，試跑用")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if "your-email" in cfg.get("user_agent", ""):
        log("請先在 config.yaml 的 user_agent 填上你們的聯絡信箱，讓網站管理者知道是誰在爬")
        return 1
    sites = [s for s in cfg["sites"] if s.get("enabled", True) and (not args.site or s["name"] == args.site)]
    if not sites:
        log("找不到要爬的網站，檢查 --site 名稱或 config.yaml 的 enabled")
        return 1

    for site in sites:
        started = dt.datetime.now()
        out = (Path(args.config).resolve().parent / cfg["output_dir"] / site["name"]).resolve()
        (out / "raw").mkdir(parents=True, exist_ok=True)
        log(f"=== 開始爬 {site.get('unit', site['name'])}，輸出到 {out}")
        c = Crawler(cfg, site, out, args.max_pages)
        c.run()
        if not c.records:
            # 一筆都沒有時不覆蓋上次的結果，避免把好的資料洗掉
            with open(out / "skipped.jsonl", "w", encoding="utf-8") as f:
                for s in c.skipped:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
            reasons = Counter(s["reason"] for s in c.skipped)
            log(f"一頁都沒爬到，保留上次的結果。原因統計：{dict(reasons)}")
            log("若原因是 robots.txt 不允許：請向國際處或計中申請授權，或請單位直接提供檔案")
            continue
        extra = write_outputs(out, c.records, c.skipped, cfg)
        extra["index_pages"] = len(c.index_pages)
        (out / "coverage.json").write_text(json.dumps(c.coverage(), ensure_ascii=False, indent=1), encoding="utf-8")
        changes = diff_state(out, c.records)
        report = write_report(out, site, c.records, c.skipped, changes, extra, started)
        log(f"=== 完成：{len(c.records)} 筆、{extra['chunks']} 段，報告在 {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
