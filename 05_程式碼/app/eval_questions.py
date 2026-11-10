"""檢索測試：拿一組「學生常問的問題」去問知識庫，看正確的資料有沒有被找出來。

  python eval_questions.py

題目放在 04_知識庫資料/整理後/測試題.csv，欄位：
  問題｜身分（可空白）｜應該找到（標題或網址裡會出現的字，多個用、隔開）
只做檢索、不呼叫 Claude，不花錢。用 .env 的設定：本機版查 Chroma，STORE=supabase 查雲端。
報告寫在 04_知識庫資料/整理後/檢索測試報告.md。每次重爬、改切分方式之後跑一次，比較前後分數。
"""
from __future__ import annotations

import csv
import sys

import config
import ingest
import rag

QUESTIONS = config.CURATED_DIR / "測試題.csv"
REPORT = config.CURATED_DIR / "檢索測試報告.md"


def main() -> int:
    if not QUESTIONS.exists():
        print(f"找不到 {QUESTIONS}")
        return 1
    rows = []
    with open(QUESTIONS, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            q = (r.get("問題") or "").strip()
            if q:
                rows.append((q, (r.get("身分") or "").strip() or None, ingest.split_list(r.get("應該找到", ""))))
    results = []
    for q, ident, expect in rows:
        hits = rag.retrieve(q, ident, rag.detect_lang(q))
        rank = next((i for i, h in enumerate(hits, 1)
                     if any(w.lower() in (h["meta"].get("title", "") + " " + h["meta"].get("url", "")).lower() for w in expect)), None)
        results.append((q, ident, expect, rank, hits))
        print(f"{'第 ' + str(rank) + ' 名' if rank else '沒找到':>6}  {q}")

    n = len(results)
    top1 = sum(r[3] == 1 for r in results)
    top5 = sum(r[3] is not None for r in results)
    L = ["# 檢索測試報告", "", f"題目 {n} 題｜知識庫：{config.STORE}｜不呼叫 Claude", "",
         "| 指標 | 結果 | 說明 |", "|---|---|---|",
         f"| 第一名就找對 | {top1}／{n}（{top1 / max(n, 1):.0%}） | 最理想，AI 拿到的第一段就是對的資料 |",
         f"| 前 5 名有找到 | {top5}／{n}（{top5 / max(n, 1):.0%}） | AI 會看前 5 段，有在裡面就答得出來；建議 80% 以上 |",
         "", "| 問題 | 應該找到 | 結果 | 實際找到的前 3 段 |", "|---|---|---|---|"]
    for q, ident, expect, rank, hits in results:
        top = "<br>".join(f"{h['meta'].get('title', '')[:30]}（{h['distance']:.2f}）" for h in hits[:3]) or "（沒有結果）"
        q_show = f"{q}（{ident}）" if ident else q
        L.append(f"| {q_show} | {'、'.join(expect)} | {'第 ' + str(rank) + ' 名' if rank else '**沒找到**'} | {top} |")
    L += ["", "沒找到的題目：先看「實際找到」是不是其實也答得出來（可能只是預期的關鍵字要改）；"
          "如果真的找錯，通常是資料沒爬到、被時效規則擋下，或段落切得太碎。"]
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n第一名就找對 {top1}/{n}，前 5 名有找到 {top5}/{n}\n報告：{REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
