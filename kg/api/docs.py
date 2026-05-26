"""Docs endpoints (Phase 3)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ..extractors import docs as docs_extractor


router = APIRouter(prefix="/docs", tags=["docs"])


@router.get("/list")
async def list_docs(project: str) -> dict:
    cypher = """
        MATCH (d:Doc {project: $project})
        RETURN d.name AS name, d.file AS file, d.title AS title, d.size AS size,
               d.headings AS headings
        ORDER BY d.file
    """
    with session() as s:
        rows = s.run(cypher, project=project).data()
    return {"project": project, "count": len(rows), "docs": rows}


@router.get("/get/{name}")
async def get_doc(name: str, project: str) -> dict:
    doc = docs_extractor.read_doc(project, name)
    if doc is None:
        raise HTTPException(404, f"doc not found: {name} (project={project})")
    return {"project": project, **doc}


@router.get("/search")
async def search_docs(project: str, q: str = Query(..., min_length=2)) -> dict:
    """Case-insensitive substring search across doc titles and headings."""
    cypher = """
        MATCH (d:Doc {project: $project})
        WHERE toLower(d.name)  CONTAINS toLower($q)
           OR toLower(coalesce(d.title, '')) CONTAINS toLower($q)
           OR any(h IN d.headings WHERE toLower(h) CONTAINS toLower($q))
        RETURN d.name AS name, d.file AS file, d.title AS title,
               [h IN d.headings WHERE toLower(h) CONTAINS toLower($q)] AS matched_headings
        ORDER BY d.name
    """
    with session() as s:
        rows = s.run(cypher, project=project, q=q).data()
    return {"project": project, "query": q, "count": len(rows), "hits": rows}
