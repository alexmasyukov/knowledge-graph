"""Permissions endpoints (Phase 3)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session


router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("/list")
async def list_permissions(
    project: str,
    prefix: str | None = Query(default=None, description="key prefix filter"),
    role: str | None = Query(default=None, description="permissions granted to this role"),
) -> dict:
    cypher = """
        MATCH (pe:Permission {project: $project})
        WHERE ($prefix IS NULL OR pe.key STARTS WITH $prefix)
          AND ($role IS NULL OR $role IN coalesce(pe.roles, []))
        OPTIONAL MATCH (r:Route)-[:REQUIRES]->(pe)
        WITH pe, collect(DISTINCT r.path) AS routes
        RETURN pe.key AS key, coalesce(pe.roles, []) AS roles,
               pe.file AS file, pe.line AS line, routes
        ORDER BY pe.key
    """
    with session() as s:
        rows = s.run(cypher, project=project, prefix=prefix, role=role).data()
    return {"project": project, "count": len(rows), "permissions": rows}


@router.get("/info/{key}")
async def permission_info(key: str, project: str) -> dict:
    cypher = """
        MATCH (pe:Permission {project: $project, key: $key})
        OPTIONAL MATCH (r:Route)-[:REQUIRES]->(pe)
        OPTIONAL MATCH (pe)-[:GRANTED_TO]->(role:Role)
        WITH pe, collect(DISTINCT r.path) AS routes, collect(DISTINCT role.code) AS roles
        RETURN
          {key: pe.key, roles: coalesce(pe.roles, []), file: pe.file, line: pe.line} AS permission,
          routes,
          roles AS roles_via_edges
    """
    with session() as s:
        rec = s.run(cypher, project=project, key=key).single()
    if not rec or rec["permission"] is None or rec["permission"].get("key") is None:
        raise HTTPException(404, f"permission not found: {key} (project={project})")
    return {"project": project, **rec.data()}


@router.get("/roles")
async def list_roles(project: str) -> dict:
    cypher = """
        MATCH (role:Role {project: $project})
        OPTIONAL MATCH (pe:Permission)-[:GRANTED_TO]->(role)
        RETURN role.code AS code, count(DISTINCT pe) AS permission_count
        ORDER BY role.code
    """
    with session() as s:
        rows = s.run(cypher, project=project).data()
    return {"project": project, "count": len(rows), "roles": rows}
