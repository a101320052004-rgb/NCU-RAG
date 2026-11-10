"""新抓的資料和原本的知識庫比對，上傳雲端前先看一下差在哪裡。

  python compare_new.py            # 預設比對 ncu_life（校園生活資訊）
  python compare_new.py shsd       # 也可以指定其他網站

每一段新資料會找出原本知識庫裡最像的一段，分成：
  重複：原本就有幾乎一樣的內容
  相關：講同一件事，如果時間、金額、電話、日期不一樣，會標成「數字不同」
  新增：原本沒有的內容
報告寫在 04_知識庫資料/整理後/新舊資料比對_<網站>.md（另有 .csv 逐段明細）。
只讀資料、不改任何東西；向量用和雲端一樣的 e5 模型，算過的會共用 store/ 裡的快取。
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict

import config
import cloud_ingest as ci  # 共用向量快取，和 4_上傳雲端 用同一個模型
import ingest

DUP = 0.93       # 相似度高於這個 → 視為重複
RELATED = 0.86   # 介於兩者之間 → 講同一件事，進一步比對數字
TOPICS = ["食", "衣", "住", "行", "育", "樂"]

FACTS = {
    "時間": re.compile(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)"),
    "金額": re.compile(r"(?:NT\$|NTD|\$)\s?\d[\d,]*|\d[\d,]*\s*(?:元|塊)"),
    "電話": re.compile(r"(?:分機|ext\.?)\s*\d{3,5}|\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{3,4}", re.I),
    "日期": re.compile(r"20\d\d\s*[年/.\-]\s*\d{1,2}\s*[月/.\-]\s*\d{1,2}|(?<![\d/])\d{1,2}/\d{1,2}(?![\d/])"),
}


def facts(text: str) -> dict[str, set[str]]:
    out = {}
    for k, pat in FACTS.items():
        vals = {re.sub(r"[\s,：]", "", m.group(0)).replace("：", ":") for m in pat.finditer(text)}
        if vals:
            out[k] = vals
    return out


def diff_facts(new: str, old: str) -> list[str]:
    """同一類數字兩邊都有、但內容不同，才算不一致（只有一邊有的不算）。"""
    a, b = facts(new), facts(old)
    notes = []
    for k in a.keys() & b.keys():
        only_new, only_old = sorted(a[k] - b[k]), sorted(b[k] - a[k])
        if only_new and only_old:
            notes.append(f"{k}：新 {'、'.join(only_new[:4])}／舊 {'、'.join(only_old[:4])}")
    return notes


def body(r: dict) -> str:
    return re.sub(r"^【[^】]*】\n?", "", r.get("text", "")).strip()


def short(t: str, n: int = 60) -> str:
    t = re.sub(r"\s+", " ", t)
    return t[:n] + ("…" if len(t) > n else "")


def main() -> int:
    site = sys.argv[1] if len(sys.argv) > 1 else "ncu_life"
    records = ingest.load_crawled() + ingest.load_faq() + ingest.load_manual()
    new = [r for r in records if r.get("source") == f"crawler:{site}"]
    if not new:
        print(f"找不到 {site} 的資料：請先執行 crawler.py --site {site}，"
              f"再看 04_知識庫資料/原始資料/{site}/report.md 是否有被 robots.txt 擋下")
        return 1
    old = [r for r in records if r.get("source") != f"crawler:{site}" and ingest.judge(r)[0] == "active"]

    if not old:
        print("原本的知識庫是空的，沒有東西可以比對：請先執行 2_更新資料.bat")
        return 1
    print(f"新資料 {len(new)} 段，原本知識庫可用的 {len(old)} 段")
    cache = ci.load_cache()
    ci.embed_all([r["text"] for r in new + old], cache)
    import numpy as np
    N = np.array([cache[ci.key(r["text"])] for r in new], dtype=np.float32)
    O = np.array([cache[ci.key(r["text"])] for r in old], dtype=np.float32)
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    O /= np.linalg.norm(O, axis=1, keepdims=True)
    sims = N @ O.T

    rows = []
    for i, r in enumerate(new):
        status, reason = ingest.judge(r)
        j = int(sims[i].argmax())
        s = float(sims[i, j])
        o = old[j]
        notes = diff_facts(body(r), body(o)) if s >= RELATED else []
        kind = "重複" if s >= DUP else "相關" if s >= RELATED else "新增"
        if kind == "相關" and notes:
            kind = "數字不同"
        rows.append({"r": r, "status": status, "reason": reason, "kind": kind, "sim": s, "old": o, "notes": notes})

    # ---- 主題涵蓋：比對前後各主題能用的段落數
    def topic_count(rs):
        c = Counter(t for x in rs for t in x.get("topics", []))
        return [c.get(t, 0) for t in TOPICS]
    usable_new = [x["r"] for x in rows if x["status"] == "active" and x["kind"] != "重複"]
    before, add = topic_count(old), topic_count(usable_new)

    # ---- 依文件彙整
    docs = defaultdict(list)
    for x in rows:
        docs[(x["r"].get("title") or "（無標題）", x["r"].get("url", ""))].append(x)

    config.CURATED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = config.CURATED_DIR / f"新舊資料比對_{site}.md"
    csv_path = config.CURATED_DIR / f"新舊資料比對_{site}.csv"
    kinds = Counter(x["kind"] for x in rows)
    blocked = Counter(x["status"] for x in rows if x["status"] != "active")
    L = [f"# 新舊資料比對：{site}", "",
         f"比對日期 {config.today().isoformat()}；新資料 {len(new)} 段、{len(docs)} 份文件；"
         f"原本知識庫可用 {len(old)} 段。", "",
         "| 結果 | 段數 | 說明 |", "|---|---|---|",
         f"| 新增 | {kinds['新增']} | 原本沒有的內容 |",
         f"| 相關 | {kinds['相關']} | 原本有講到，數字沒有衝突 |",
         f"| 數字不同 | {kinds['數字不同']} | 原本有講到，但時間、金額、電話或日期不一樣，要人工確認哪個才對 |",
         f"| 重複 | {kinds['重複']} | 原本就有幾乎一樣的內容 |",
         f"| 被時效規則擋下 | {sum(blocked.values())} | 過期 {blocked['expired']}、待確認 {blocked['needs_review']}，不會上傳 |", "",
         "## 各主題可用段落", "", "| 主題 | " + " | ".join(TOPICS) + " |", "|---|" + "---|" * len(TOPICS),
         "| 原本 | " + " | ".join(map(str, before)) + " |",
         "| 新增可用 | " + " | ".join(f"+{n}" for n in add) + " |", ""]

    conflicts = [x for x in rows if x["kind"] == "數字不同"]
    L += ["## 需要人工確認：數字不同", ""]
    if conflicts:
        L += ["| 新資料 | 原本的資料 | 差異 |", "|---|---|---|"]
        for x in conflicts:
            L.append(f"| [{x['r'].get('title')}]({x['r'].get('url')}) | [{x['old'].get('title')}]({x['old'].get('url')}) "
                     f"| {'；'.join(x['notes'])} |")
    else:
        L.append("沒有發現數字衝突。")
    L += ["", "## 每份文件", "", "| 文件 | 狀態 | 段數 | 新增 | 相關 | 數字不同 | 重複 | 最像的原本資料 |",
          "|---|---|---|---|---|---|---|---|"]
    for (title, url), xs in sorted(docs.items(), key=lambda kv: -len(kv[1])):
        c = Counter(x["kind"] for x in xs)
        st = xs[0]["status"] + (f"（{xs[0]['reason']}）" if xs[0]["reason"] else "")
        best = max(xs, key=lambda x: x["sim"])
        L.append(f"| [{title}]({url}) | {st} | {len(xs)} | {c['新增']} | {c['相關']} | {c['數字不同']} | {c['重複']} "
                 f"| {best['old'].get('title')}（{best['sim']:.2f}） |")
    md_path.write_text("\n".join(L) + "\n", encoding="utf-8")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["結果", "相似度", "時效狀態", "原因", "新資料標題", "新資料網址", "新資料內容",
                    "最像的原本資料", "原本網址", "原本內容", "數字差異"])
        for x in sorted(rows, key=lambda x: ("數字不同", "新增", "相關", "重複").index(x["kind"])):
            w.writerow([x["kind"], f"{x['sim']:.3f}", x["status"], x["reason"], x["r"].get("title"), x["r"].get("url"),
                        short(body(x["r"]), 200), x["old"].get("title"), x["old"].get("url"),
                        short(body(x["old"]), 200), "；".join(x["notes"])])

    print(f"新增 {kinds['新增']}、相關 {kinds['相關']}、數字不同 {kinds['數字不同']}、重複 {kinds['重複']}；"
          f"被時效規則擋下 {sum(blocked.values())}")
    print(f"報告：{md_path}\n明細：{csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
