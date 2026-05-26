from __future__ import annotations

import httpx
from fastapi import APIRouter

from ..db import ping
from ..settings import settings
from ._models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> dict:
    out: dict = {"service": "kg-core", "ok": True}

    try:
        out["neo4j"] = ping()
    except Exception as e:
        out["ok"] = False
        out["neo4j"] = {"ok": False, "error": str(e)}

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.indexer_url}/health")
            out["indexer"] = r.json()
    except Exception as e:
        out["ok"] = False
        out["indexer"] = {"ok": False, "error": str(e)}

    out["projects_configured"] = [p.name for p in settings.projects]
    return out
