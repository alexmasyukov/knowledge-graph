"""TypeScript types read API.

  GET /types/list?project[&kind][&name][&limit]
  GET /types/get/{name}?project
  GET /types/search?project&q
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session

router = APIRouter(prefix="/types", tags=["types"])


@router.get("/list")
async def list_types(
    project: str,
    kind: str | None = Query(default=None, description="filter to one kind: interface | type_alias | enum | class"),
    name: str | None = Query(default=None, description="substring filter on name"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    where = ["t.project = $project"]
    if kind:
        where.append("t.kind = $kind")
    if name:
        where.append("toLower(t.name) CONTAINS toLower($name)")
    cypher = f"""
        MATCH (t:Type)
        WHERE {' AND '.join(where)}
        OPTIONAL MATCH (f:File)-[:USES_TYPE]->(t)
        WITH t, count(DISTINCT f) AS consumers
        RETURN t.name AS name, t.kind AS kind, t.file AS file, t.line AS line,
               t.members AS members, consumers
        ORDER BY t.name
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, kind=kind, name=name, limit=limit).data()
    types = [
        {
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file"],
            "line": r["line"],
            "members": list(r["members"] or []),
            "consumers": r["consumers"] or 0,
        }
        for r in rows
    ]
    return {"project": project, "count": len(types), "types": types}


@router.get("/get/{name}")
async def get_type(name: str, project: str) -> dict:
    cypher = """
        MATCH (t:Type {project: $project, name: $name})
        OPTIONAL MATCH (f:File)-[r:USES_TYPE]->(t)
        WITH t, collect(DISTINCT {file: f.path, line: r.line}) AS uses
        RETURN t.name AS name, t.kind AS kind, t.file AS file, t.line AS line,
               t.members AS members, uses
    """
    with session() as s:
        rec = s.run(cypher, project=project, name=name).single()
    if rec is None or rec["file"] is None:
        raise HTTPException(404, f"type not found: {name} (project={project})")
    data = rec.data()
    return {
        "project": project,
        "name": data["name"],
        "kind": data["kind"],
        "file": data["file"],
        "line": data["line"],
        "members": list(data["members"] or []),
        "consumers": [u for u in (data["uses"] or []) if u.get("file")],
    }


@router.get("/search")
async def search_types(project: str, q: str, limit: int = Query(default=50, ge=1, le=500)) -> dict:
    cypher = """
        MATCH (t:Type {project: $project})
        WHERE toLower(t.name) CONTAINS toLower($q)
        RETURN t.name AS name, t.kind AS kind, t.file AS file, t.line AS line
        ORDER BY t.name
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, q=q, limit=limit).data()
    return {"project": project, "query": q, "count": len(rows), "types": rows}
