"""向量化。
  e5：multilingual-e5（Microsoft 開源，中英文都可，免費、在本機執行；第一次會下載約 470MB）
  e5_onnx：同一個 e5 的 ONNX 量化版（約 120MB），不需要 PyTorch，雲端版用；第一次會從 Hugging Face 下載
  gemini：Gemini embedding API（備用）
本機版和雲端版的向量不能混用，各自的資料庫要用各自的方式建。"""
from __future__ import annotations

import hashlib
import math
import re
import threading

import config

_model = None
_onnx = None
_onnx_lock = threading.Lock()  # 雲端同時來好幾個請求時，只讓一個去下載、載入模型


def _e5():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(config.E5_MODEL)
    return _model


def _download(name: str):
    """從 Hugging Face 下載模型檔，已經有就不再下載。"""
    import httpx
    path = config.MODEL_DIR / config.E5_ONNX_REPO.replace("/", "__") / name
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{threading.get_ident()}.part")
    url = f"https://huggingface.co/{config.E5_ONNX_REPO}/resolve/main/{name}"
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(path)
    return path


def _e5_onnx():
    global _onnx
    if _onnx is not None:
        return _onnx
    with _onnx_lock:
        if _onnx is not None:
            return _onnx
        import onnxruntime as ort
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(_download("tokenizer.json")))
        tok.enable_truncation(512)
        pad_id = tok.token_to_id("<pad>")
        tok.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token="<pad>")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        sess = ort.InferenceSession(str(_download(config.E5_ONNX_FILE)), opts, providers=["CPUExecutionProvider"])
        _onnx = (tok, sess, {i.name for i in sess.get_inputs()})
    return _onnx


def _onnx_embed(texts: list[str], batch: int = 16) -> list[list[float]]:
    import numpy as np
    tok, sess, names = _e5_onnx()
    out = []
    for i in range(0, len(texts), batch):
        enc = tok.encode_batch(texts[i:i + batch])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = sess.run(None, feeds)[0]                       # (筆數, 字數, 384)
        m = mask[..., None].astype(hidden.dtype)
        vec = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)  # e5 規定用平均
        vec /= np.linalg.norm(vec, axis=1, keepdims=True)
        out.extend(v.tolist() for v in vec)
    return out


def warm_up() -> None:
    """雲端版：網頁一打開就先載入模型，學生第一個問題才不用等。"""
    if config.EMBEDDER == "e5_onnx":
        _e5_onnx()


def _hash_vec(text: str, dim: int = 256) -> list[float]:
    """測試用：字元 bigram 雜湊成向量，不需下載模型。"""
    v = [0.0] * dim
    t = re.sub(r"\s+", "", text.lower())
    for i in range(len(t) - 1):
        h = int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class RateLimited(Exception):
    def __init__(self, wait: float, daily: bool = False):
        super().__init__(f"rate limited, retry in {wait:.0f}s")
        self.wait, self.daily = wait, daily


def _gemini(texts: list[str], task: str) -> list[list[float]]:
    import httpx
    model = config.GEMINI_EMBED_MODEL
    body = {"requests": [{"model": f"models/{model}", "content": {"parts": [{"text": t[:6000]}]},
                          "taskType": task, "outputDimensionality": config.GEMINI_EMBED_DIMS}
                         for t in texts]}
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents",
                   headers={"x-goog-api-key": config.GEMINI_API_KEY}, json=body, timeout=60)
    if r.status_code == 429:
        m = re.search(r'"retryDelay":\s*"(\d+)', r.text)
        raise RateLimited(float(m.group(1)) + 1 if m else 30.0, daily="PerDay" in r.text)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini embedding 失敗 HTTP {r.status_code}: {r.text[:300]}")
    return [e["values"] for e in r.json()["embeddings"]]


def embed_passages(texts: list[str]) -> list[list[float]]:
    if config.EMBEDDER == "e5_onnx":
        return _onnx_embed([f"passage: {t}" for t in texts])
    if config.EMBEDDER == "gemini":
        return _gemini(texts, "RETRIEVAL_DOCUMENT")
    if config.EMBEDDER == "hash":
        return [_hash_vec(t) for t in texts]
    # e5 規定文件要加 "passage: " 前綴
    vecs = _e5().encode([f"passage: {t}" for t in texts], normalize_embeddings=True,
                        batch_size=16, show_progress_bar=len(texts) > 50)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    if config.EMBEDDER == "e5_onnx":
        return _onnx_embed([f"query: {text}"])[0]
    if config.EMBEDDER == "gemini":
        return _gemini([text], "RETRIEVAL_QUERY")[0]
    if config.EMBEDDER == "hash":
        return _hash_vec(text)
    return _e5().encode([f"query: {text}"], normalize_embeddings=True)[0].tolist()
