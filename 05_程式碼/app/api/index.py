"""Vercel 入口：把 FastAPI app 交給 Vercel。所有網址都會轉到這裡（見 vercel.json）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402,F401
