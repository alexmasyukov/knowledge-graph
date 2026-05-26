"""SCSS endpoints (Phase 5)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ._models import ScssClassResponse, ScssListResponse


router = APIRouter(prefix="/scss", tags=["scss"])


@router.get("/list", response_model=ScssListResponse)
async def list_modules(
    project: str,
    class_name: str | None = Query(default=None, description="filter by class name presence"),
) -> dict:
    cypher = """
        MATCH (m:ScssModule {project: $project})
        WHERE $cls IS NULL OR $cls IN m.classes
        OPTIONAL MATCH (f:File)-[:USES_SCSS]->(m)
        WITH m, collect(DISTINCT f.path) AS consumers
        RETURN m.file AS file, m.classes AS classes, consumers
        ORDER BY m.file
    """
    with session() as s:
        rows = s.run(cypher, project=project, cls=class_name).data()
    return {"project": project, "count": len(rows), "modules": rows}


@router.get("/class/{name}", response_model=ScssClassResponse)
async def class_usage(name: str, project: str) -> dict:
    """Find which scss modules declare this class and which files use those modules."""
    cypher = """
        MATCH (m:ScssModule {project: $project})
        WHERE $name IN m.classes
        OPTIONAL MATCH (f:File)-[:USES_SCSS]->(m)
        WITH m, collect(DISTINCT f.path) AS consumers
        RETURN m.file AS module, consumers
        ORDER BY m.file
    """
    with session() as s:
        rows = s.run(cypher, project=project, name=name).data()
    if not rows:
        raise HTTPException(404, f"class not found: {name} (project={project})")
    return {"project": project, "class_name": name, "modules": rows}
