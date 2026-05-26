from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from ..db import session
from ..extractors import gql as gql_extractor
from ..extractors import routes as routes_extractor
from ..extractors import permissions as permissions_extractor
from ..extractors import pages as pages_extractor
from ..extractors import docs as docs_extractor
from ..settings import settings


router = APIRouter()


@router.post("/reindex")
async def reindex(project: str | None = None) -> dict:
    """Reindex one or all configured projects.

    Pipeline:
      1) MERGE Project node in Neo4j
      2) Register project in ts-morph indexer (loads SourceFiles into memory)
      3) Run extractors (Phase 1: gql)
    """
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
            register = r.json()

            stats = await client.get(
                f"{settings.indexer_url}/projects/stats",
                params={"project": proj.name},
            )
            stats.raise_for_status()

            # 3) extractors (order matters: gql → routes → permissions/pages/docs)
            gql_result = await gql_extractor.run_for_project(proj.name)
            routes_result = await routes_extractor.run_for_project(proj.name)
            permissions_result = await permissions_extractor.run_for_project(proj.name)
            pages_result = await pages_extractor.run_for_project(proj.name)
            docs_result = await docs_extractor.run_for_project(proj.name)

            results.append(
                {
                    "register": register,
                    "stats": stats.json(),
                    "gql": gql_result,
                    "routes": routes_result,
                    "permissions": permissions_result,
                    "pages": pages_result,
                    "docs": docs_result,
                }
            )

    return {"ok": True, "results": results}
