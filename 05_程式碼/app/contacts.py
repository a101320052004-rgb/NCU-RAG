"""承辦窗口：依學生身分轉給負責的承辦人，方便國際處直接轉接。

資料來源：國際處提供的「國際處同仁工作職掌」（2026-11-10，Excel 11511_02_01）；
張雪蓮不在那份表上，但仍在職（使用者確認保留），職掌取自國際處網站「成員及職掌」（2026-11-09）。
依國際處要求，學生端只給分機和國際處信箱，不給承辦人個人 email。人員或分機有異動時，只要改這個檔案。
"""
from __future__ import annotations

import datetime as dt

SWITCHBOARD = "03-422-7151"
SWITCHBOARD_EN = "+886-3-422-7151"
OIA_MAIL = "ncuoia@ncu.edu.tw"
SOURCE_URL = "https://oia.ncu.edu.tw/p/412-1026-966.php?Lang=zh-tw"

STAFF = {
    "簡君怡": {"ext": "57080", "en": "Ms. Kelly Chien",
               "duty": "行政事務、訪問／實習學生、陸生及港澳大陸交換生",
               "duty_en": "general affairs, visiting students, Mainland/HK/Macau exchange students"},
    "徐心荷": {"ext": "57081", "en": "Ms. Lotus Hsu",
               "duty": "僑生、外籍交換生在校輔導", "duty_en": "overseas Chinese students, international exchange students"},
    "張雪蓮": {"ext": "57081", "en": "Ms. Cheung",
               "duty": "陸生、陸港澳交換生、秋季班外籍交換生", "duty_en": "mainland Chinese students, fall exchange students"},
    "羅如華": {"ext": "57079", "en": "Ms. Anita Lo",
               "duty": "外籍學位生輔導、學生保險、工作證", "duty_en": "degree students, insurance, work permits"},
    "阮塔尼": {"ext": "57079", "en": "Mr. Otani Nguyen",
               "duty": "外籍學位生輔導、獎助學金", "duty_en": "degree students, scholarships"},
    "陳宣霏": {"ext": "57082", "en": "Ms. Fei Chen",
               "duty": "外籍交換生申請", "duty_en": "applications for international exchange students"},
    "黃婕": {"ext": "57083", "en": "Ms. Jess Huang",
             "duty": "外籍學位生招生及申請入學", "duty_en": "admissions for international degree programs"},
    "王美琦": {"ext": "57083", "en": "Ms. Eileen Wang",
               "duty": "陸生、僑生及港澳生申請入學", "duty_en": "admissions for mainland, overseas Chinese, HK and Macau students"},
}

ADMISSION_WORDS = ("申請入學", "招生", "報考", "入學申請", "admission", "apply to ncu", "application for admission")
SCHOLARSHIP_WORDS = ("獎學金", "獎助", "助學金", "scholarship")


# 學生還沒選身分、但問題裡自己講了（「我是外籍學位生…」），就照問題裡的身分轉接
IDENTITY_WORDS = (("外籍學位生", ("外籍學位生", "外籍生", "degree student")),
                  ("交換生", ("交換生", "exchange student")),
                  ("僑生", ("僑生", "港澳生", "overseas chinese")),
                  ("陸生", ("陸生", "大陸學生", "mainland")))


def guess_identity(text: str) -> str | None:
    low = (text or "").lower()
    found = [name for name, words in IDENTITY_WORDS if any(w in low for w in words)]
    return found[0] if len(found) == 1 else None  # 問題裡提到兩種身分（例如在比較）就不猜


def pick(identity: str | None, text: str = "", today: dt.date | None = None) -> str:
    """依身分和問題內容，挑出最該轉接的承辦人。"""
    low = (text or "").lower()
    identity = identity or guess_identity(text)
    if any(w in low for w in ADMISSION_WORDS):
        if identity == "交換生":
            return "陳宣霏"
        return "王美琦" if identity in ("陸生", "僑生") else "黃婕"
    if identity == "外籍學位生":
        return "阮塔尼" if any(w in low for w in SCHOLARSHIP_WORDS) else "羅如華"
    if identity == "僑生":
        return "徐心荷"
    if identity == "陸生":
        return "張雪蓮"
    if identity == "交換生":
        month = (today or dt.date.today()).month
        return "徐心荷" if 2 <= month <= 7 else "張雪蓮"   # 春季班 / 秋季班
    return "簡君怡"  # 身分不明或訪問研修生：國際行政事務組，可再轉接


def call_button(identity: str | None, lang: str, text: str = "") -> dict:
    """一鍵撥號：撥總機後自動停頓、再輸入分機（逗號＝停頓 2 秒左右，多數手機支援）。"""
    name = pick(identity, text)
    s = STAFF[name]
    label = f"撥給{name}（分機 {s['ext']}）" if lang == "zh" else f"Call {s['en']} (ext. {s['ext']})"
    return {"label": label, "tel": f"tel:+88634227151,{s['ext']}"}


SECURITY_CALL = {"zh": "校安中心 24 小時", "en": "Campus Security 24h", "tel": "tel:+88632805666"}
CRISIS_CALL = {"zh": "1925 安心專線", "en": "1925 support line", "tel": "tel:1925"}


def contact_line(identity: str | None, lang: str, text: str = "") -> str:
    name = pick(identity, text)
    s = STAFF[name]
    if lang == "zh":
        return (f"國際處 {name}（{s['duty']}）：{SWITCHBOARD} 分機 {s['ext']}，Email {OIA_MAIL}"
                f"；國際學生事務專線 03-425-4945")
    return (f"OIA – {s['en']} {name} ({s['duty_en']}): {SWITCHBOARD_EN} ext. {s['ext']}, email {OIA_MAIL}"
            f"; International Students Affairs: +886-3-425-4945")
