"""Health endpoint — quick sanity probe for Memgraph + config."""

from __future__ import annotations

from fastapi import APIRouter

from ..db import ping
from ..settings import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    try:
        meta = ping()
    except Exception as e:
        meta = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {
        "service": "kg-core",
        "ok": meta.get("ok", False),
        "memgraph": meta,
        "projects": [p.name for p in settings.projects],
    }
