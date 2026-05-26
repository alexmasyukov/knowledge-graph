from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import extractors
from ..db import session
from ..http import client
from ..settings import settings


router = APIRouter()


@router.post("/reindex")
async def reindex(project: str | None = None) -> dict:
    """Reindex one or all configured projects.

    Pipeline per project:
      1) MERGE Project node in Neo4j
      2) Register project in ts-morph indexer (loads SourceFiles into memory)
      3) Iterate extractors.ALL — each owns its labels and writes its slice
    """
    targets = (
        [p for p in settings.projects if p.name == project]
        if project
        else settings.projects
    )
    if not targets:
        raise HTTPException(404, f"project not configured: {project}")

    http = client()
    results = []
    for proj in targets:
        with session() as s:
            s.run(
                "MERGE (p:Project {name: $name}) "
                "SET p.root = $root, p.updated_at = datetime()",
                name=proj.name,
                root=str(proj.code_root),
            )

        r = await http.post(
            "/projects/register",
            json={"name": proj.name, "root": str(proj.code_root)},
        )
        r.raise_for_status()
        register = r.json()

        stats = await http.get("/projects/stats", params={"project": proj.name})
        stats.raise_for_status()

        per_extractor: dict[str, dict] = {}
        for ext in extractors.ALL:
            per_extractor[ext.NAME] = await ext.run(proj.name)

        results.append(
            {
                "project": proj.name,
                "register": register,
                "stats": stats.json(),
                "extractors": per_extractor,
            }
        )

    return {"ok": True, "results": results}
