"""爬蟲驗收報告：爬完之後，檢查「有沒有爬到該爬的」和「切得好不好」。

  python check_crawl.py

讀 04_知識庫資料/原始資料/<網站>/ 底下的 documents.jsonl、chunks.jsonl、coverage.json，
產生 04_知識庫資料/整理後/爬蟲驗收報告.md。只讀資料，不改任何東西，也不連網路。

檢查項目
  1. 涵蓋：頁面上找到的連結（在爬取範圍內的）有多少真的抓到；沒抓到的原因（頁數上限、深度上限、robots.txt）
  2. 外部網站：常被連到、但不在爬取範圍的網站（可能是該加的資料來源）
  3. 附件：抽不出文字、靠 OCR、舊格式沒轉成功的檔案
  4. 主題與關鍵字：每個主題有幾份文件；學生常問的關鍵字在資料裡出現幾次
  5. 切分：每段長度、太短的段、切在句子中間的段、重複的段、混進來的選單或頁尾文字
  6. 抽樣：隨機列出幾段，讓人用眼睛確認
"""
from __future__ import annotations

import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

import taxonomy

HERE = Path(__file__).resolve().parent
END_CHARS = "。！？.!?：:；;）)」』|…"
# 學生常問、資料裡一定要有的關鍵字（中英文任一出現就算有）
MUST_HAVE = {
    "居留證": ["居留證", "ARC", "Resident Certificate"], "工作證": ["工作證", "work permit"],
    "健保／保險": ["健保", "保險", "insurance"], "宿舍": ["宿舍", "dorm"], "獎學金": ["獎學金", "scholarship"],
    "選課": ["選課", "course selection"], "校曆／行事曆": ["校曆", "行事曆", "academic calendar"],
    "學餐／飲食": ["學餐", "餐廳", "cafeteria", "dining"], "交通": ["公車", "客運", "高鐵", "bus", "HSR"],
    "緊急聯絡": ["校安", "緊急", "emergency"], "就醫": ["就醫", "診所", "醫院", "clinic", "hospital"],
    "新生報到": ["報到", "新生", "orientation"],
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def body(text: str) -> str:
    return re.sub(r"^【[^】]*】\n?", "", text).strip()


def cell(t: str) -> str:
    """放進 Markdown 表格前，把換行和直線符號換掉，避免表格跑版。"""
    return re.sub(r"\s+", " ", t).replace("|", "／")


def mark(ok: bool, warn_text: str = "需注意") -> str:
    return "正常" if ok else warn_text


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    raw_dir = (HERE / cfg["output_dir"]).resolve()
    out_path = raw_dir.parent / "整理後" / "爬蟲驗收報告.md"
    size = cfg.get("chunk_size", 500)

    sites, docs, chunks, covs, skipped_all = [], [], [], {}, []
    for site in cfg["sites"]:
        d = raw_dir / site["name"]
        if not (d / "documents.jsonl").exists():
            continue
        sites.append(site["name"])
        docs += [{**r, "_site": site["name"]} for r in load_jsonl(d / "documents.jsonl")]
        chunks += [{**r, "_site": site["name"]} for r in load_jsonl(d / "chunks.jsonl")]
        for x in load_jsonl(d / "skipped.jsonl"):
            skipped_all.append({**x, "_site": site["name"]})
        if (d / "coverage.json").exists():
            covs[site["name"]] = json.loads((d / "coverage.json").read_text(encoding="utf-8"))
    if not docs:
        print(f"找不到爬取結果：請先執行 crawler.py（輸出資料夾 {raw_dir}）")
        return 1

    L = ["# 爬蟲驗收報告", "", f"網站：{'、'.join(sites)}；文件 {len(docs)} 份、段落 {len(chunks)} 段。", ""]
    verdict = []

    # ------------------------------------------------ 1. 涵蓋
    cov_rows, missing_rows = [], []
    total_missing = Counter()
    for name in sites:
        c = covs.get(name)
        n_docs = sum(r["_site"] == name for r in docs)
        if not c:
            cov_rows.append(f"| {name} | {n_docs} | — | — | — | 這次爬取沒有涵蓋率資料（用新版爬蟲重跑一次就有） |")
            continue
        reasons = Counter(m["reason"].split("（")[0] for m in c["missing"])
        total_missing.update(reasons)
        robots = sum("robots" in s["reason"] for s in c["skipped"])
        errors = len(c["skipped"]) - robots
        cov_rows.append(f"| {name} | {c['fetched']} | {c['discovered']} | {len(c['missing'])} | {robots}／{errors} | "
                        + ("、".join(f"{k} {v}" for k, v in reasons.items()) or "全部抓到") + " |")
        for m in c["missing"][:40]:
            missing_rows.append(f"| {name} | {m['reason']} | {m.get('text') or '—'} | {m['url']} |")
    unknown = total_missing.get("未抓到", 0)
    page_cap = total_missing.get("達頁數上限", 0)
    verdict.append(("涵蓋：範圍內的連結有沒有抓到",
                    "無法判斷" if not covs else "正常" if not (unknown or page_cap) else "需注意",
                    "這次的爬取結果沒有涵蓋率資料，用新版爬蟲重跑一次就有" if not covs else
                    "全部抓到" if not sum(total_missing.values()) else
                    "、".join(f"{k} {v} 個" for k, v in total_missing.items())
                    + ("；達頁數上限代表網站比設定的大，建議調高 max_pages 重跑" if page_cap else "")))

    blocked = [x for x in skipped_all if re.search(r"HTTP (401|403)|robots", x["reason"])]
    gone = [x for x in skipped_all if "HTTP 404" in x["reason"] or "HTTP 410" in x["reason"]]
    verdict.append(("連得到：起始頁和連結沒有被擋、沒有失效", mark(not blocked and not gone),
                    f"被網站拒絕（robots.txt 或 403）{len(blocked)} 個、網址失效（404）{len(gone)} 個"
                    + ("；被拒絕的內容不能硬爬，改成請單位提供或人工整理" if blocked else "")))

    # ------------------------------------------------ 2. 外部網站
    ext = Counter()
    ext_examples = {}
    for c in covs.values():
        for e in c.get("external", []):
            ext[e["host"]] += e["count"]
            ext_examples.setdefault(e["host"], e.get("examples", []))
    noise_hosts = ("facebook.com", "instagram.com", "youtube.com", "twitter.com", "x.com", "line.me", "google.com")
    ext_rows = [f"| {h} | {n} | " + "；".join(f"{x['text']}" for x in ext_examples.get(h, [])[:3]) + " |"
                for h, n in ext.most_common(20) if not any(h.endswith(x) for x in noise_hosts)]

    # ------------------------------------------------ 3. 附件
    atts = [r for r in docs if r["kind"] == "attachment"]
    empty = [r for r in atts if r["chars"] < 50]
    ocr = [r for r in atts if "OCR 自動辨識" in (r.get("note") or "")]
    verdict.append(("附件：讀得出文字", mark(not empty),
                    f"附件 {len(atts)} 份；抽不出文字 {len(empty)} 份、靠 OCR {len(ocr)} 份（OCR 的電話、金額、日期要人工確認）"))

    # ------------------------------------------------ 4. 主題與關鍵字
    topic_docs = Counter(t for r in docs for t in r.get("topics", []))
    thin = [t for t in taxonomy.TOPICS if topic_docs.get(t, 0) < 3]
    verdict.append(("主題：每個主題至少 3 份文件", mark(not thin), "偏少：" + "、".join(thin) if thin else "都有 3 份以上"))
    all_text = [(r, r.get("text", "").lower()) for r in chunks]
    kw_rows, kw_missing = [], []
    for name, words in MUST_HAVE.items():
        hits = [r for r, t in all_text if any(w.lower() in t for w in words)]
        n_docs = len({r.get("doc_id") for r in hits})
        kw_rows.append(f"| {name} | {len(hits)} | {n_docs} | {mark(n_docs >= 2, '偏少' if n_docs else '沒有')} |")
        if n_docs < 2:
            kw_missing.append(name)
    verdict.append(("關鍵字：學生常問的主題資料裡都有", mark(not kw_missing),
                    "偏少或沒有：" + "、".join(kw_missing) if kw_missing else "都有"))

    # ------------------------------------------------ 5. 切分
    by_doc = defaultdict(list)
    for r in chunks:
        by_doc[r.get("doc_id")].append(r)
    lens = [len(body(r["text"])) for r in chunks]
    short, cut, dup_groups = [], [], defaultdict(list)
    for doc_id, rs in by_doc.items():
        for i, r in enumerate(rs):
            b = body(r["text"])
            if len(b) < 80 and len(rs) > 1:
                short.append(r)
            if i < len(rs) - 1 and b and b[-1] not in END_CHARS and len(b) >= size - 20:
                cut.append(r)  # 段落太長被硬切，切點落在句子中間
            dup_groups[b].append(r)
    dups = [rs for b, rs in dup_groups.items() if len({x.get("doc_id") for x in rs}) > 1 and len(b) > 40]
    # 選單、頁尾這類每頁都有的文字：同一行出現在很多份文件裡
    line_docs = defaultdict(set)
    pages = [r for r in docs if r["kind"] == "page"]
    for r in pages:
        for line in {x.strip() for x in r.get("text", "").splitlines() if len(x.strip()) >= 8}:
            line_docs[line].add(r["url"])
    noise = sorted(((ln, len(u)) for ln, u in line_docs.items() if len(u) >= max(5, len(pages) * 0.3)), key=lambda x: -x[1])
    cut_pct = len(cut) / max(len(chunks), 1)
    verdict.append(("切分：長度合理", mark(len(short) / max(len(chunks), 1) < 0.1),
                    f"中位數 {int(statistics.median(lens)) if lens else 0} 字、九成在 "
                    f"{int(sorted(lens)[int(len(lens) * 0.9)]) if lens else 0} 字內；太短（< 80 字）{len(short)} 段"))
    verdict.append(("切分：沒有切在句子中間", mark(cut_pct < 0.15),
                    f"{len(cut)} 段（{cut_pct:.0%}）是長段落被硬切；有前後段補上下文，影響不大，但超過 15% 建議改成依標題切"))
    verdict.append(("切分：沒有重複的段", mark(not dups), f"{len(dups)} 組內容一樣、出現在不同文件"))
    verdict.append(("內容：沒有混進選單、頁尾", mark(not noise), f"{len(noise)} 行文字在三成以上的頁面重複出現"))

    # ------------------------------------------------ 輸出
    L += ["## 結論", "", "| 檢查 | 結果 | 說明 |", "|---|---|---|"]
    L += [f"| {a} | {b} | {c} |" for a, b, c in verdict]
    L += ["", "## 1. 各網站涵蓋", "",
          "| 網站 | 抓到 | 頁面上找到的範圍內連結 | 沒抓到 | robots 擋／連線錯誤 | 沒抓到的原因 |", "|---|---|---|---|---|---|"] + cov_rows
    if skipped_all:
        L += ["", "### 被擋下或失效的網址", "", "| 網站 | 原因 | 網址 |", "|---|---|---|"]
        L += [f"| {x['_site']} | {x['reason']} | {x['url']} |" for x in skipped_all[:60]]
    if missing_rows:
        L += ["", "### 沒抓到的連結（最多列 40 個／網站）", "", "| 網站 | 原因 | 連結文字 | 網址 |", "|---|---|---|---|"] + missing_rows
    L += ["", "## 2. 常被連到、但不在爬取範圍的外部網站", "",
          "這些是國際處或住宿組頁面上連出去的網站。學生常問的內容如果在這裡，考慮加進 config.yaml。", "",
          "| 網站 | 連結次數 | 連結文字範例 |", "|---|---|---|"] + (ext_rows or ["| — | — | — |"])
    L += ["", "## 3. 需要處理的附件", "", "| 檔名 | 字數 | 備註 | 網址 |", "|---|---|---|---|"]
    L += [f"| {r['title']} | {r['chars']} | {r.get('note') or '—'} | {r['url']} |" for r in empty + ocr] or ["| — | — | — | — |"]
    L += ["", "## 4. 主題與關鍵字", "", "| 主題 | 文件數 |", "|---|---|"]
    L += [f"| {t} | {topic_docs.get(t, 0)}{'（偏少）' if topic_docs.get(t, 0) < 3 else ''} |" for t in taxonomy.TOPICS]
    L += ["", "| 學生常問 | 出現段數 | 出現文件數 | 判斷 |", "|---|---|---|---|"] + kw_rows
    L += ["", "## 5. 切分品質", "",
          f"設定：每段約 {size} 字、前後重疊 {cfg.get('chunk_overlap', 80)} 字，依換行切，段落太長才硬切。", ""]
    buckets = Counter(min(n // 100, 6) for n in lens)
    L += ["| 段落長度 | 段數 |", "|---|---|"] + [f"| {'600 字以上' if b == 6 else f'{b * 100}～{b * 100 + 99} 字'} | {buckets.get(b, 0)} |" for b in range(7)]
    if short:
        L += ["", "### 太短的段（前 10 個）", "", "| 文件 | 內容 |", "|---|---|"]
        L += [f"| {r['title']} | {cell(body(r['text'])[:60])} |" for r in short[:10]]
    if cut:
        L += ["", "### 切在句子中間的段（前 10 個，看結尾）", "", "| 文件 | 結尾 |", "|---|---|"]
        L += [f"| {r['title']} | …{cell(body(r['text'])[-40:])} |" for r in cut[:10]]
    if dups:
        L += ["", "### 重複的段（前 10 組）", "", "| 出現次數 | 文件 | 內容開頭 |", "|---|---|---|"]
        L += [f"| {len(rs)} | {'、'.join(sorted({x['title'][:20] for x in rs}))} | {cell(body(rs[0]['text'])[:40])} |" for rs in dups[:10]]
    if noise:
        L += ["", "### 疑似選單或頁尾的文字（在很多頁重複出現）", "", "| 出現頁數 | 文字 |", "|---|---|"]
        L += [f"| {n} | {cell(ln[:60])} |" for ln, n in noise[:15]]
        L += ["", "處理方式：調整 config.yaml 的 content_selectors，只抓內文區塊。"]
    L += ["", "## 6. 抽樣檢查（隨機 5 段，請用眼睛看是否完整、通順）", ""]
    for r in random.Random(42).sample(chunks, min(5, len(chunks))):
        L += [f"**{r['title']}**　{r['url']}", "", "```", r["text"][:600], "```", ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    for a, b, c in verdict:
        print(f"[{b}] {a}：{c}")
    print(f"\n報告：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
