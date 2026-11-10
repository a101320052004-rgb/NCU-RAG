"""一次檢查所有金鑰能不能用。不會顯示金鑰本身。

  python check_keys.py
"""
from __future__ import annotations

import config


def show(name: str, ok: bool, msg: str) -> None:
    print(f"{'[OK]  ' if ok else '[失敗]'} {name}：{msg}")


def reason(e: Exception) -> str:
    t = str(e)
    for k, v in [("credit balance", "Claude 帳戶沒有餘額，請到 console.anthropic.com → Billing 儲值"),
                 ("authentication", "金鑰錯誤，請重新複製貼上"), ("invalid x-api-key", "金鑰錯誤，請重新複製貼上"),
                 ("API_KEY_INVALID", "金鑰錯誤，請重新複製貼上"), ("API key not valid", "金鑰錯誤，請重新複製貼上"),
                 ("not_found", "模型名稱不存在，請改 .env 的模型名稱"), ("NOT_FOUND", "模型名稱不存在，請改 .env 的模型名稱"),
                 ("429", "免費額度用完了，台灣時間下午 4 點重置"), ("RESOURCE_EXHAUSTED", "免費額度用完了，台灣時間下午 4 點重置"),
                 ("rate limited", "免費額度用完了，台灣時間下午 4 點重置"),
                 ("Connect", "連不上網路")]:
        if k.lower() in t.lower():
            return f"{v}（{t[:150]}）"
    return t[:300]


def main() -> None:
    print(f"目前設定：回答用 {config.LLM_PROVIDER}，本機查資料用 {config.EMBEDDER}\n")

    # 1. Claude
    if not config.ANTHROPIC_API_KEY:
        show("Claude", False, ".env 的 ANTHROPIC_API_KEY 是空的")
    else:
        try:
            import anthropic
            r = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY).messages.create(
                model=config.CLAUDE_MODEL, max_tokens=20,
                messages=[{"role": "user", "content": "只回答兩個字：成功"}])
            show("Claude", True, f"{config.CLAUDE_MODEL} 回覆「{r.content[0].text.strip()}」")
        except Exception as e:  # noqa: BLE001
            show("Claude", False, reason(e))

    # 2. Gemini 回答
    if not config.COMPAT_API_KEY:
        show("Gemini 回答", False, ".env 的 COMPAT_API_KEY 是空的")
    else:
        try:
            from openai import OpenAI
            r = OpenAI(base_url=config.COMPAT_BASE_URL, api_key=config.COMPAT_API_KEY).chat.completions.create(
                model=config.COMPAT_MODEL, max_tokens=20,
                messages=[{"role": "user", "content": "只回答兩個字：成功"}])
            show("Gemini 回答", True, f"{config.COMPAT_MODEL} 回覆「{(r.choices[0].message.content or '').strip()}」")
        except Exception as e:  # noqa: BLE001
            show("Gemini 回答", False, f"{config.COMPAT_MODEL}：{reason(e)}")

    # 3. 雲端版向量模型（4_上傳雲端.bat 和 Vercel 用的，要一樣）
    try:
        import embedder
        config.EMBEDDER = config.CLOUD_EMBEDDER
        if config.CLOUD_EMBEDDER == "e5_onnx":
            print("（第一次會下載 e5 模型約 140MB，請稍等）")
        v = embedder.embed_query("宿舍怎麼申請")
        show("雲端向量模型", True, f"{config.CLOUD_EMBEDDER}，{len(v)} 維")
    except Exception as e:  # noqa: BLE001
        show("雲端向量模型", False, reason(e))

    # 4. Supabase
    if not config.SUPABASE_URL:
        show("Supabase", False, ".env 沒有 SUPABASE_URL")
    else:
        try:
            import sb
            s = (sb.rpc("kb_stats", {}) or [{}])[0]
            show("Supabase", True, f"連線正常，雲端知識庫可用 {s.get('active')} 段")
        except Exception as e:  # noqa: BLE001
            show("Supabase", False, reason(e))

    print(f"\n本機 demo 現在會用：{'Claude' if config.LLM_PROVIDER == 'claude' else 'Gemini'} 回答"
          "（要換就改 .env 第 4 行 LLM_PROVIDER，改完重開後端）")


if __name__ == "__main__":
    main()
