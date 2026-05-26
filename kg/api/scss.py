"""SCSS modules read API.

  GET /scss/list?project[&class_name]
  GET /scss/class/{name}?project
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session

router = APIRouter(prefix="/scss", tags=["scss"])


@router.get("/list")
async def list_modules(
    project: str,
    class_name: str | None = Query(default=None, description="filter to modules defining this class"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    if class_name:
        cypher = """
            MATCH (m:ScssModule {project: $project})-[:DEFINES]->(c:ScssClass {name: $class_name})
            OPTIONAL MATCH (f:File)-[:CONSUMES]->(m)
            WITH m, collect(DISTINCT c.name) AS classes, collect(DISTINCT f.path) AS consumers
            RETURN m.file AS file, classes, consumers
            ORDER BY m.file
            LIMIT $limit
        """
    else:
        cypher = """
            MATCH (m:ScssModule {project: $project})
            OPTIONAL MATCH (m)-[:DEFINES]->(c:ScssClass)
            OPTIONAL MATCH (f:File)-[:CONSUMES]->(m)
            WITH m, collect(DISTINCT c.name) AS classes, collect(DISTINCT f.path) AS consumers
            RETURN m.file AS file, classes, consumers
            ORDER BY m.file
            LIMIT $limit
        """
    with session() as s:
        rows = s.run(cypher, project=project, class_name=class_name, limit=limit).data()
    modules = [
        {
            "file": r["file"],
            "classes": sorted([c for c in (r["classes"] or []) if c]),
            "consumers": sorted([c for c in (r["consumers"] or []) if c]),
        }
        for r in rows
    ]
    return {"project": project, "count": len(modules), "modules": modules}


@router.get("/class/{name}")
async def class_usage(name: str, project: str) -> dict:
    cypher = """
        MATCH (c:ScssClass {project: $project, name: $name})
        OPTIONAL MATCH (m:ScssModule)-[:DEFINES]->(c)
        OPTIONAL MATCH (f:File)-[:CONSUMES]->(m)
        RETURN m.file AS module, collect(DISTINCT f.path) AS consumers
    """
    with session() as s:
        rows = s.run(cypher, project=project, name=name).data()
    if not rows or all(r["module"] is None for r in rows):
        raise HTTPException(404, f"scss class not found: {name} (project={project})")
    modules = []
    for r in rows:
        if r["module"]:
            modules.append({
                "file": r["module"],
                "consumers": sorted([c for c in (r["consumers"] or []) if c]),
            })
    return {"project": project, "class_name": name, "modules": modules}
