from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from .. import extractors
from ..db import session
from ..http import client
from ..settings import settings


from ._models import ReindexResponse

router = APIRouter()
log = logging.getLogger("kg.reindex")


@router.post("/reindex", response_model=ReindexResponse)
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
        log.info("reindex start: %s (root=%s)", proj.name, proj.code_root)
        t_total = time.perf_counter()

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
        log.info("indexer registered: %d source files", stats.json().get("sourceFiles", 0))

        per_extractor: dict[str, dict] = {}
        for ext in extractors.ALL:
            t = time.perf_counter()
            try:
                result = await ext.run(proj.name)
            except Exception as e:
                log.exception("extractor %s failed", ext.NAME)
                per_extractor[ext.NAME] = {"error": str(e)}
                continue
            elapsed_ms = int((time.perf_counter() - t) * 1000)
            written = result.get("written") or {}
            log.info("extractor %s: %s (%dms)", ext.NAME, written or result, elapsed_ms)
            per_extractor[ext.NAME] = result

        log.info("reindex done: %s in %.1fs", proj.name, time.perf_counter() - t_total)

        results.append(
            {
                "project": proj.name,
                "register": register,
                "stats": stats.json(),
                "extractors": per_extractor,
            }
        )

    return {"ok": True, "results": results}
