"""Shared environment derived from .env (no pydantic — keep startup light)."""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent  # repo root


def _load_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    out: dict[str, str] = dict(os.environ)
    if env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out.setdefault(k.strip(), v.strip())
    return out


ENV = _load_env()
INDEXER_URL = ENV.get("INDEXER_URL", "http://127.0.0.1:7401")
CORE_URL = f"http://{ENV.get('API_HOST', '127.0.0.1')}:{ENV.get('API_PORT', '7400')}"
NEO4J_BROWSER = "http://localhost:7474"
PROJECTS = sorted(
    {
        k[len("PROJECT_"):].lower()
        for k in ENV
        if k.startswith("PROJECT_") and ENV[k].strip()
    }
)

RUN_DIR = ROOT / ".run"
LOG_DIR = ROOT / ".logs"
RUN_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
