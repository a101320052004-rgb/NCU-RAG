"""Supabase 連線：只透過資料庫函式（RPC）存取，資料表本身全部鎖住（RLS）。"""
from __future__ import annotations

import httpx

import config

_client: httpx.Client | None = None


def rpc(name: str, params: dict):
    global _client
    if _client is None:
        _client = httpx.Client(base_url=f"{config.SUPABASE_URL}/rest/v1/rpc/", timeout=30,
                               headers={"apikey": config.SUPABASE_KEY, "Content-Type": "application/json"})
    r = _client.post(name, json=params)
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase {name} 失敗 HTTP {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else None


def vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
