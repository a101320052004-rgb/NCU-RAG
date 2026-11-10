"""國際處提供的 Excel（2026-11）→ 人工整理 Markdown（給 ingest.py）＋ 上傳用 JSON（給 /api/admin/kb-import）。

用法：python convert.py <xlsx 資料夾> <輸出資料夾>
每個 Markdown 檔：開頭 --- 欄位（title、topics、identities、last_verified、note），內文用「## 小標」分段，
一段就是知識庫裡的一筆。段落第一行若是「網址：https://…」，那一筆的來源網址就用它。
"""
from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

import openpyxl

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
VERIFIED = "2026-11-10"
NOTE = "國際處提供（2026-11-10），原始檔為 Excel"
OIA_MAIL = "ncuoia@ncu.edu.tw"
SWITCH = "03-422-7151"


def book(part: str):
    f = next(p for p in SRC.glob("*.xlsx") if part in p.name)
    return openpyxl.load_workbook(f, data_only=True)


def rows(ws, start=2):
    for r in ws.iter_rows(min_row=start, values_only=True):
        vals = [clean(v) for v in r]
        if any(vals):
            yield vals


def clean(v) -> str:
    if v is None:
        return ""
    s = str(v).replace("\r", "").replace("\t", " ").strip()
    s = re.sub(r"[ 　]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s)


def ext_of(phone: str) -> str:
    m = re.search(r"轉\s*(\d{5})", phone or "")
    return m.group(1) if m else ""


FILES: list[dict] = []


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def tidy(t: str) -> str:
    """學生端只給國際處總信箱（國際處要求）；Excel 換行轉頓號時留下的重複標點也一併整理。"""
    t = EMAIL.sub(lambda m: m.group(0) if m.group(0) == OIA_MAIL else OIA_MAIL, t)
    t = re.sub(r"、{2,}", "、", t)
    return re.sub(r"[。．]\s*[；;]", "；", t).replace(" ，", "，").replace(" 。", "。")


def doc(slug, title, topics, identities, sections, url=""):
    FILES.append({"slug": slug, "title": title, "topics": topics, "identities": identities,
                  "url": url, "sections": [(h, tidy(t.strip()), u) for h, t, u in sections if t.strip()]})


# ---------------------------------------------------------------- 食、逛街購物
wb = book("06_01")
food = list(rows(wb["食在中大"]))
sec = [("附近餐廳總覽", "國際處整理的中央大學附近餐廳（價位為每人約略金額）：\n" + "\n".join(
    f"- {n}（{p or '校內，價位依店家'}）：{d.replace(chr(10), '、')}；{loc.replace(chr(10), '')}" for n, d, loc, p in food), "")]
for n, d, loc, p in food:
    sec.append((n, f"特色：{d}\n位置：{loc}" + (f"\n價位：約 {p}" if p else ""), ""))
doc("food", "食在中大：學校附近餐廳", ["食"], ["全體境外生"], sec)

shop = [r[:3] for r in rows(wb["逛街購物"])]
sec = [("逛街購物總覽", "國際處整理的中壢、中央大學附近購物地點：\n" + "\n".join(
    f"- {n}（{p}）：{d.replace(chr(10), '、')}" for n, d, p in shop), "")]
for n, d, p in shop:
    sec.append((n, f"特色：{d}\n價位：{p}", ""))
doc("shop", "逛街購物：中壢與校內商店", ["衣", "樂"], ["全體境外生"], sec)

# ---------------------------------------------------------------- 住
wb = book("06_02")
dorm = [r[:4] for r in rows(wb["學校宿舍"])]
osds = "https://in.ncu.edu.tw/ncu7221/OSDS/index.php"
sec = [("學校宿舍總覽", "中央大學學校宿舍一覽（由學務處住宿服務組管理，詳細規定以住宿服務組網站為準）：\n" + "\n".join(
    f"- {n}：{re.sub(r'^1\.\s*', '', d.split(chr(10))[0]).strip()}" for n, d, _, _ in dorm)
    + f"\n住宿服務組網站：{osds}", osds)]
for n, d, e, u in dorm:
    sec.append((n, f"{d}\n住宿服務組分機：{SWITCH} 分機 {e}\n參考網址：{u.strip()}", u.strip()))
hotel = [r[:4] for r in rows(wb["鄰近旅館"])]
sec.append(("學校附近旅館（家人朋友來訪）", "\n".join(f"- {n}（{p}）：{d}　{u}" for n, d, p, u in hotel), ""))
for q, a, u in (r[:3] for r in rows(wb["問答"])):
    if "culture-exploration" in u:  # 原表這一列的網址填錯（指到出國交流頁），改用住宿服務組的頁面
        u = "https://in.ncu.edu.tw/ncu7221/OSDS/about.php"
    sec.append((q, a, u))
doc("dorm", "學校宿舍與住宿", ["住"], ["全體境外生"], sec, osds)

# ---------------------------------------------------------------- 行
wb = book("06_03")
ub = [r[:3] for r in rows(wb["Ubike"])]
bus = [r[:3] for r in rows(wb["公車"])]
hsr = [r[:3] for r in rows(wb["高鐵"])]
rail = [r[:2] for r in rows(wb["火車"])]
ub_text = (f"中央大學有 {len(ub)} 個 YouBike 站：" + "、".join(s for _, s, _ in ub)
           + f"\n租用方法：{ub[0][0]}\n價格：{ub[0][2]}")
bus_lines = [f"- {n} 路：{t.replace(chr(10), '；')}（{p}）" for n, t, p in bus]
sec = [("交通總覽", "往返中央大學的交通方式（國際處整理，班次與票價以客運公司公告為準）：\n"
        + "公車：\n" + "\n".join(bus_lines)
        + f"\nYouBike：{ub_text.replace(chr(10), '；')}"
        + "\n高鐵：" + "；".join(f"{a}，{c or b}" for a, b, c in hsr)
        + "\n火車：" + "；".join(f"{a}，{b}" for a, b in rail), ""),
       ("YouBike 公共自行車", ub_text, "")]
for n, t, p in bus:
    sec.append((f"公車 {n} 路", f"路線與班次：{t}\n票價：{p}\n（班次以客運公司最新公告為準）", ""))
sec.append(("高鐵與火車", "高鐵：" + "；".join(f"{a}，票價{c or b}" for a, b, c in hsr)
            + "。從高鐵桃園站到中央大學可搭 172、173 路公車。\n火車：" + "；".join(f"{a}，票價{b}" for a, b in rail)
            + "。132 路公車從中壢公車站開往中央大學。", ""))
doc("traffic", "交通：公車、YouBike、高鐵、火車", ["行"], ["全體境外生"], sec)

# ---------------------------------------------------------------- 國際處簡介與承辦人
wb_new, wb_ai = book("11511"), book("0410")
units = list(rows(wb_new["國際處及各組簡介"]))
units_en = {i: r for i, r in enumerate(rows(wb_ai["國際處及各組簡介(英文版)"]))}
sec = []
for i, (name, intro, phone, url) in enumerate(u[:4] for u in units):
    en = units_en.get(i)
    sec.append((name, f"{intro}\n電話：{phone.replace('+886-3-', '03-')}"
                + (f"\n{en[0]}: {en[1]}" if en else ""), url))
staff = list(rows(wb_new["國際處同仁工作職掌"]))
lines, people = [], []
for r in staff:
    r = (r + [""] * 8)[:8]
    # 國際長、副國際長那兩列沒有英文名，欄位往前移一格
    if r[1] in ("男", "女"):
        name, en, title, group, duty, phone = r[0], "", r[2], "", r[3], r[5]
    else:
        name, en, _, title, group, duty, _, phone = r
    duty = "\n".join(OrderedDict.fromkeys(re.sub(r"^\d+\.\s*", "", x).strip() for x in duty.split("\n") if x.strip()))
    people.append((name, en, title, group, duty, ext_of(phone)))
# 張雪蓮：國際處表格沒有列，但仍在職（依使用者確認保留，職掌取自國際處網站「成員及職掌」2026-11-09）
people.append(("張雪蓮", "", "專員", "國際行政事務組", "陸生在校輔導與服務\n陸港澳交換生\n秋季班外籍交換生", "57081"))
for name, en, title, group, duty, ext in people:
    head = f"{name}" + (f" {en}" if en else "")
    sec.append((f"承辦人：{head}", f"職稱：{group + ' ' if group else ''}{title}\n負責業務：\n"
                + "\n".join(f"- {d}" for d in duty.split("\n")) + f"\n電話：{SWITCH} 分機 {ext}\n"
                f"Email：請寄國際處信箱 {OIA_MAIL}（信中註明找{name}）", ""))
    lines.append(f"- {head}（{group + ' ' if group else ''}{title}，分機 {ext}）：" + "、".join(duty.split("\n")))
sec.insert(len(units), ("國際處誰負責什麼", "國際事務處各承辦人的業務（電話一律撥 "
                         f"{SWITCH} 轉分機；Email 請寄國際處信箱 {OIA_MAIL}）：\n" + "\n".join(lines), ""))
doc("oia", "國際事務處簡介與承辦人", [], ["全體境外生"], sec, "https://www.oia.ncu.edu.tw/tw/")

# ---------------------------------------------------------------- 常見問題
OUTBOUND = re.compile(r"出國")  # 本地生出國交流的題目不放（境外生小幫手不回答出國交換）
faq = {"office": [], "oc": [], "cn": [], "degree": []}
for q, a in (r[:2] for r in rows(wb_new["問答"])):
    if OUTBOUND.search(q):
        continue
    key = ("cn" if re.search(r"陸生|大陸", q) else "oc" if re.search(r"僑生|港澳|馬來西亞", q)
           else "degree" if re.search(r"申請|入學許可|短期訪問|新住民", q) else "office")
    faq[key].append((q, a, ""))
deg_zh = [r[:2] for r in rows(book("05_01_01")["問答"])]
deg_en = [r[:2] for r in rows(wb_ai["外籍學位生申請常問問題"])]
for (qz, az), (qe, ae) in zip(deg_zh, deg_en):
    faq["degree"].append((qz, f"{az}\n\n{qe.strip()}\n{ae.strip()}", "https://cis.ncu.edu.tw/admissions/"))
doc("faq-office", "常見問題：國際處服務", [], ["全體境外生"], faq["office"])
doc("faq-degree", "常見問題：申請入學與外籍學位生", ["新生"], ["外籍學位生"], faq["degree"])
doc("faq-oc", "常見問題：僑生與港澳生", ["新生"], ["僑生"], faq["oc"])
doc("faq-cn", "常見問題：陸生", ["新生"], ["陸生"], faq["cn"])
ex = []
for q, a in (r[:2] for r in rows(wb_ai["交換生申請常問問題"])):
    ex.append((q.strip(), a, ""))
doc("faq-exchange", "常見問題：交換生", ["新生"], ["交換生"], ex)

# ---------------------------------------------------------------- 姐妹校（精簡版：只有學校名稱、國家、合作類別）
TYPE = {"MOU": "合作備忘錄", "EA": "交換學生", "DD": "雙聯學位", "DDM": "碩士雙聯", "DDP": "博士雙聯",
        "AA": "學術合作", "SA": "學生交流"}
FOOT = ("能不能來中大交換，要看你的學校是否和中大簽有「交換學生」合約，並由你的學校推薦；"
        f"合約是否仍有效、名額多少，請洽國際處（{OIA_MAIL}）。")


def kind(code: str) -> str:
    code = code.strip().upper()
    return TYPE.get(code, code) if code else ""


ws = wb_ai["國外姐妹校"]
by_country: "OrderedDict[str, OrderedDict[str, set]]" = OrderedDict()
for r in rows(ws, start=3):
    region, country, en, zh, dept, level, ctype = (r + [""] * 7)[:7]
    if not (en or zh):
        continue
    country = country.replace("\n", " ") or "其他"
    name = f"{zh}（{en}）" if zh and en else zh or en
    k = kind(ctype)
    if level and level != "校" and dept:
        k = f"{k}（{dept}）" if k else dept
    by_country.setdefault(country, OrderedDict()).setdefault(name, set()).add(k)
sec = []
for country, schools in by_country.items():
    items = [f"- {n}：{'、'.join(sorted(x for x in ks if x)) or '合作學校'}" for n, ks in schools.items()]
    for i in range(0, len(items), 12):
        part = items[i:i + 12]
        sec.append((country + (f"（{i // 12 + 1}）" if len(items) > 12 else ""),
                    f"中央大學在{country}的姊妹校（{len(items)} 所）：\n" + "\n".join(part) + "\n" + FOOT, ""))
doc("partners-intl", "姊妹校名單：國外", ["育"], ["交換生", "全體境外生"], sec)

ws = wb_ai["港澳大陸姐妹校"]
by_area: "OrderedDict[str, OrderedDict[str, set]]" = OrderedDict()
for r in rows(ws):
    r = (r + [""] * 9)[:9]
    area, zh, en, dept, level, ctype = r[0], r[1], r[2], r[3], r[4], r[8]
    if not (zh or en):
        continue
    name = re.sub(r"\(非\d+\)", "", zh).strip() + (f"（{en.title()}）" if en else "")
    by_area.setdefault(area or "其他", OrderedDict()).setdefault(name, set()).add(kind(ctype))
sec = []
for area, schools in by_area.items():
    items = [f"- {n}：{'、'.join(sorted(x for x in ks if x)) or '合作學校'}" for n, ks in schools.items()]
    for i in range(0, len(items), 12):
        part = items[i:i + 12]
        sec.append((area + (f"（{i // 12 + 1}）" if len(items) > 12 else ""),
                    f"中央大學在{area}的姊妹校（{len(items)} 所）：\n" + "\n".join(part) + "\n" + FOOT, ""))
doc("partners-cn", "姊妹校名單：港澳與大陸地區", ["育"], ["交換生", "陸生", "全體境外生"], sec)

# ---------------------------------------------------------------- 輸出
OUT.mkdir(parents=True, exist_ok=True)
md_dir = OUT / "人工整理"
md_dir.mkdir(exist_ok=True)
upload = []
for d in FILES:
    head = (f"---\ntitle: {d['title']}\nurl: {d['url']}\nunit: 國際事務處\ntopics: {'、'.join(d['topics'])}\n"
            f"identities: {'、'.join(d['identities'])}\nlang: zh\nlast_verified: {VERIFIED}\nnote: {NOTE}\n---\n\n")
    body = "\n\n".join((f"## {h}\n" + (f"網址：{u}\n" if u else "") + t) for h, t, u in d["sections"])
    (md_dir / f"國際處提供_{d['slug']}.md").write_text(head + body + "\n", encoding="utf-8")
    for i, (h, t, u) in enumerate(d["sections"]):
        upload.append({"id": f"sheet-{d['slug']}-{i}", "title": f"{d['title']}｜{h}", "url": u or d["url"],
                       "topics": d["topics"], "identities": d["identities"], "lang": "zh",
                       "body": f"【{d['title']}｜{h}】\n{t}"})
(OUT / "upload.json").write_text(json.dumps(upload, ensure_ascii=False, indent=1), encoding="utf-8")
for d in FILES:
    print(f"{d['slug']:15s} {len(d['sections']):3d} 段  {d['title']}")
print("合計", len(upload), "段；最長", max(len(u["body"]) for u in upload), "字")
