"""把知識庫上傳到雲端（Supabase），給 Vercel 上的版本用。在你自己的電腦執行。

和 ingest.py 用同一套時效規則：只有「可用」的段落會上傳，過期、待確認的不上傳。
向量化用 e5（ONNX 版，在你電腦上算，不用任何 API）。雲端版查詢時也用同一個模型，兩邊才對得起來。
第一次會從 Hugging Face 下載模型（約 120MB）。

  python cloud_ingest.py

需要 .env 裡有：SUPABASE_URL、SUPABASE_KEY、SUPABASE_INGEST_SECRET

算過的向量會存起來（store/<模型>_cache.jsonl），下次只算新增或內容有變的段落。
全部都算完的那一次，才會刪掉雲端上這次沒出現的舊段落（網站上已下架、或已過期的）。
（備用：.env 設 CLOUD_EMBEDDER=gemini 會改用 Gemini，那時雲端的 Vercel 也要一起改。）
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import time

import config

config.EMBEDDER = config.CLOUD_EMBEDDER  # 必須和 Vercel 上的 EMBEDDER 一樣

import embedder  # noqa: E402
import ingest  # noqa: E402
import sb  # noqa: E402

CACHE = config.STORE_DIR / f"{config.CLOUD_EMBEDDER}_cache.jsonl"
MODEL_ID = (f"{config.E5_ONNX_REPO}/{config.E5_ONNX_FILE}" if config.CLOUD_EMBEDDER == "e5_onnx"
            else f"{config.GEMINI_EMBED_MODEL}|{config.GEMINI_EMBED_DIMS}")
EMBED_BATCH = 50
UPLOAD_BATCH = 40


def key(text: str) -> str:
    return hashlib.sha1(f"{MODEL_ID}|{text}".encode()).hexdigest()


def load_cache() -> dict:
    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            try:
                k, v = json.loads(line)
                cache[k] = v
            except ValueError:
                pass  # 上次中斷時寫壞的最後一行
    return cache


def embed_all(texts: list[str], cache: dict) -> bool:
    """回傳 True 表示全部算完；False 表示今天的額度用完了。"""
    todo = list(dict.fromkeys(t for t in texts if key(t) not in cache))
    print(f"向量化（{config.CLOUD_EMBEDDER}）：{len(texts) - len(todo)} 段用快取，{len(todo)} 段要計算")
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as f:
        i = 0
        while i < len(todo):
            part = todo[i:i + EMBED_BATCH]
            try:
                vecs = embedder.embed_passages(part)
            except embedder.RateLimited as e:
                if e.wait > 120 or e.daily:
                    print("  Gemini 今天的免費額度用完了，先上傳已經算好的部分。")
                    return False
                print(f"  每分鐘的額度到了，等 {e.wait:.0f} 秒再繼續…")
                time.sleep(e.wait)
                continue
            for t, v in zip(part, vecs):
                cache[key(t)] = v
                f.write(json.dumps([key(t), [round(x, 6) for x in v]]) + "\n")
            f.flush()
            i += len(part)
            print(f"  {i}/{len(todo)}")
            time.sleep(1)
    return True


def iso(s) -> str:
    n = ingest.as_int_date(s)
    return dt.datetime.strptime(str(n), "%Y%m%d").date().isoformat() if n else ""


CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(t) -> str:
    """PDF 抽出來的文字常夾著看不見的控制字元（例如 \\x00），資料庫不收，先拿掉。"""
    return CTRL.sub("", t or "")


def to_row(r: dict, vec: list[float]) -> dict:
    return {
        "id": str(r["chunk_id"]), "source": r.get("source", ""), "site": r.get("site", ""),
        "unit": clean(r.get("unit")), "title": clean(r.get("title"))[:200], "url": clean(r.get("url")),
        "lang": r.get("lang", "zh"), "body": clean(r["text"]),
        "topics": r.get("topics", []), "identities": r.get("identities", []), "status": "active",
        "valid_until": "" if r.get("deadline_passed") or ingest.is_regulation(r) else iso(r.get("latest_date")),
        "last_verified": iso(r.get("last_verified_at") or r.get("fetched_at")),
        "embedding": sb.vec(vec),
    }


def main() -> int:
    need = ["SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_INGEST_SECRET"]
    if config.CLOUD_EMBEDDER == "gemini":
        need.append("GEMINI_API_KEY")
    missing = [k for k in need if not getattr(config, k)]
    if missing:
        print("`.env` 少了：" + "、".join(missing))
        return 1

    keep, review = ingest.split_records()
    if not keep:
        print("沒有可用的資料。請先執行 2_更新資料.bat，或放入 faq.csv。")
        return 1
    seen, uniq = set(), []
    for r in keep:  # 同一個 id 只留第一個
        if str(r["chunk_id"]) not in seen:
            seen.add(str(r["chunk_id"]))
            uniq.append(r)
    ingest.write_review(review)

    cache = load_cache()
    complete = embed_all([r["text"] for r in uniq], cache)
    ready = [r for r in uniq if key(r["text"]) in cache]

    run_id = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    secret = config.SUPABASE_INGEST_SECRET
    print(f"上傳到 Supabase：{len(ready)} 段")
    skipped = []
    for i in range(0, len(ready), UPLOAD_BATCH):
        rows = [to_row(r, cache[key(r["text"])]) for r in ready[i:i + UPLOAD_BATCH]]
        try:
            sb.rpc("ingest_upsert", {"p_secret": secret, "p_run_id": run_id, "p_rows": rows})
        except RuntimeError as e:
            if "invalid ingest secret" in str(e):
                print(".env 的 SUPABASE_INGEST_SECRET 不對，停止上傳。")
                return 1
            # 這一批有問題：改成一段一段傳，壞掉的那段跳過，其他照常上傳
            for row in rows:
                try:
                    sb.rpc("ingest_upsert", {"p_secret": secret, "p_run_id": run_id, "p_rows": [row]})
                except RuntimeError as e2:
                    skipped.append((row["title"], row["url"], str(e2)[:120]))
        print(f"  {min(i + UPLOAD_BATCH, len(ready))}/{len(ready)}")
    if skipped:
        complete = False  # 有段落沒傳上去，這次先不刪雲端舊資料
        print(f"有 {len(skipped)} 段上傳失敗、已跳過：")
        for t, u, err in skipped:
            print(f"  - {t} {u}\n    {err}")
    removed = sb.rpc("ingest_finish", {"p_secret": secret, "p_run_id": run_id}) if complete else 0
    stats = (sb.rpc("kb_stats", {}) or [{}])[0]
    print(f"雲端可用 {stats.get('active')} 段，刪掉舊資料 {removed} 段；"
          f"被時效規則擋下 {len(review)} 段（見 待確認清單.csv）")
    if len(uniq) > len(ready):
        print(f"還有 {len(uniq) - len(ready)} 段沒算完：Gemini 額度在台灣時間下午 4 點重置，之後再跑一次就會補上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
