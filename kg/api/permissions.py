"""Permissions read API.

Endpoints:
  GET /permissions/list           flat list with optional prefix + role filter
  GET /permissions/info/{key}     full info for one permission key

Response shapes match the MCP wrapper in `arenadata-mcp/tools/kg.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("/list")
async def list_permissions(
    project: str,
    prefix: str | None = Query(default=None, description="filter by key prefix, e.g. 'education.'"),
    role: str | None = Query(default=None, description="filter to permissions granting this role"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    where = ["p.project = $project"]
    if prefix:
        where.append("p.key STARTS WITH $prefix")
    cypher = f"""
        MATCH (p:Permission)
        WHERE {' AND '.join(where)}
        OPTIONAL MATCH (p)-[:GRANTS]->(r:Role)
        OPTIONAL MATCH (rt:Route)-[:REQUIRES]->(p)
        WITH p,
             collect(DISTINCT r.code) AS roles,
             collect(DISTINCT rt.path) AS routes
        WHERE $role IS NULL OR $role IN roles
        RETURN p.key AS key, p.file AS file, p.line AS line,
               roles, routes
        ORDER BY p.key
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, prefix=prefix, role=role, limit=limit).data()

    permissions = []
    for r in rows:
        permissions.append({
            "key": r["key"],
            "file": r["file"],
            "line": r["line"],
            "roles": sorted([x for x in (r["roles"] or []) if x]),
            "routes": sorted([x for x in (r["routes"] or []) if x]),
        })
    return {"project": project, "count": len(permissions), "permissions": permissions}


@router.get("/info/{key:path}")
async def permission_info(key: str, project: str) -> dict:
    cypher = """
        MATCH (p:Permission {project: $project, key: $key})
        OPTIONAL MATCH (p)-[:GRANTS]->(r:Role)
        OPTIONAL MATCH (rt:Route)-[:REQUIRES]->(p)
        RETURN p.file AS file, p.line AS line,
               collect(DISTINCT r.code) AS roles,
               collect(DISTINCT rt.path) AS routes
    """
    with session() as s:
        rec = s.run(cypher, project=project, key=key).single()
    if rec is None or rec["file"] is None:
        raise HTTPException(404, f"permission not found: {key} (project={project})")
    data = rec.data()
    return {
        "project": project,
        "key": key,
        "file": data["file"],
        "line": data["line"],
        "roles": sorted([x for x in (data["roles"] or []) if x]),
        "routes": sorted([x for x in (data["routes"] or []) if x]),
    }
