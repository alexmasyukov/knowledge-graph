"""Docs read API.

  GET /docs/list?project
  GET /docs/search?project&q
  GET /docs/get/{name}?project
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ..settings import settings

router = APIRouter(prefix="/docs", tags=["docs"])


@router.get("/list")
async def list_docs(project: str, limit: int = Query(default=500, ge=1, le=5000)) -> dict:
    cypher = """
        MATCH (d:Doc {project: $project})
        RETURN d.name AS name, d.file AS file, d.title AS title,
               d.size AS size, d.headings AS headings
        ORDER BY d.file
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, limit=limit).data()
    return {"project": project, "count": len(rows), "docs": rows}


@router.get("/search")
async def search_docs(project: str, q: str, limit: int = Query(default=50, ge=1, le=500)) -> dict:
    """Case-insensitive substring search across title + headings.
    For body search use the RAG tool."""
    cypher = """
        MATCH (d:Doc {project: $project})
        WITH d, toLower($q) AS needle
        WHERE toLower(coalesce(d.title, '')) CONTAINS needle
           OR any(h IN coalesce(d.headings, []) WHERE toLower(h) CONTAINS needle)
           OR toLower(d.file) CONTAINS needle
        RETURN d.name AS name, d.file AS file, d.title AS title,
               [h IN d.headings WHERE toLower(h) CONTAINS toLower($q)] AS matches
        ORDER BY d.file
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, q=q, limit=limit).data()
    return {"project": project, "query": q, "count": len(rows), "docs": rows}


@router.get("/get/{name:path}")
async def get_doc(name: str, project: str) -> dict:
    cypher = "MATCH (d:Doc {project: $project, name: $name}) RETURN d.file AS file"
    with session() as s:
        rec = s.run(cypher, project=project, name=name).single()
    if rec is None or rec["file"] is None:
        raise HTTPException(404, f"doc not found: {name} (project={project})")
    proj = settings.project(project)
    if proj is None:
        raise HTTPException(500, f"project {project} not configured")
    path = proj.repo_root / rec["file"]
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"failed to read {rec['file']}: {e}") from e
    return {"project": project, "name": name, "file": rec["file"], "content": content}
