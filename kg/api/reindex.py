from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from ..db import session
from ..settings import settings


router = APIRouter()


@router.post("/reindex")
async def reindex(project: str | None = None) -> dict:
    """Phase 0 stub: registers each configured project in Neo4j and ts-morph indexer.
    Phase 1+ will plug in actual extractors."""
    targets = (
        [p for p in settings.projects if p.name == project]
        if project
        else settings.projects
    )
    if not targets:
        raise HTTPException(404, f"project not configured: {project}")

    results = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for proj in targets:
            # 1) ensure project node in graph
            with session() as s:
                s.run(
                    "MERGE (p:Project {name: $name}) "
                    "SET p.root = $root, p.updated_at = datetime()",
                    name=proj.name,
                    root=str(proj.root),
                )

            # 2) register in indexer
            r = await client.post(
                f"{settings.indexer_url}/projects/register",
                json={"name": proj.name, "root": str(proj.root)},
            )
            r.raise_for_status()
            payload = r.json()

            # 3) fetch stats
            stats = await client.get(
                f"{settings.indexer_url}/projects/stats",
                params={"project": proj.name},
            )
            stats.raise_for_status()
            results.append({"register": payload, "stats": stats.json()})

    return {"ok": True, "results": results}
