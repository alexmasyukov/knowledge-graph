"""Read-only routes graph endpoints (Phase 2)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ._models import (
    ComponentRoutesResponse,
    RouteResolveResponse,
    RoutesListResponse,
)


router = APIRouter(prefix="/routes", tags=["routes"])


@router.get("/list", response_model=RoutesListResponse)
async def list_routes(
    project: str,
    prefix: str | None = Query(default=None, description="filter by path prefix"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    cypher = """
        MATCH (r:Route {project: $project})
        WHERE $prefix IS NULL OR r.path STARTS WITH $prefix
        OPTIONAL MATCH (r)-[:RENDERS]->(c:Component)
        OPTIONAL MATCH (r)-[:GUARDED_BY]->(g:Guard)
        OPTIONAL MATCH (r)-[:REQUIRES]->(p:Permission)
        WITH r,
             collect(DISTINCT c.name) AS components,
             collect(DISTINCT g.name) AS guards,
             collect(DISTINCT p.key)  AS permissions
        RETURN r.path AS path, r.index AS index, r.depth AS depth,
               r.file AS file, r.line AS line,
               components, guards, permissions
        ORDER BY r.path, r.depth
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, prefix=prefix, limit=limit).data()
    return {"project": project, "count": len(rows), "routes": rows}


@router.get("/resolve", response_model=RouteResolveResponse)
async def resolve_route(project: str, path: str) -> dict:
    """Full picture for a route: components, guards, permissions, and transitive gql layer."""
    cypher = """
        MATCH (r:Route {project: $project, path: $path})
        OPTIONAL MATCH (r)-[:RENDERS]->(c:Component)
        OPTIONAL MATCH (r)-[:GUARDED_BY]->(g:Guard)
        OPTIONAL MATCH (r)-[:REQUIRES]->(p:Permission)
        OPTIONAL MATCH (c)-[:CALLS_HOOK]->(h:GqlHook)
        OPTIONAL MATCH (c)-[:USES_OPERATION]->(o:GqlOperation)
        OPTIONAL MATCH (h)-[:WRAPS]->(opViaHook:GqlOperation)
        WITH r, c, g, p, h, o, opViaHook
        RETURN
          {path: r.path, index: r.index, depth: r.depth, file: r.file, line: r.line} AS route,
          collect(DISTINCT g.name) AS guards,
          collect(DISTINCT p.key)  AS permissions,
          collect(DISTINCT {name: c.name, file: c.file, line: c.line}) AS components,
          collect(DISTINCT {hook: h.name, file: h.file}) AS hooks_via_components,
          collect(DISTINCT {symbol: o.symbol, gql_name: o.gql_name, kind: o.kind}) AS operations_via_components,
          collect(DISTINCT {symbol: opViaHook.symbol, gql_name: opViaHook.gql_name, kind: opViaHook.kind}) AS operations_via_hooks
    """
    with session() as s:
        rec = s.run(cypher, project=project, path=path).single()
    if not rec or rec["route"] is None:
        raise HTTPException(404, f"route not found: {path} (project={project})")
    data = rec.data()
    # Cypher returns rows with placeholder dicts when OPTIONAL MATCH fails — strip those
    data["components"] = [c for c in data["components"] if c.get("name")]
    data["hooks_via_components"] = [h for h in data["hooks_via_components"] if h.get("hook")]
    data["operations_via_components"] = [o for o in data["operations_via_components"] if o.get("symbol")]
    data["operations_via_hooks"] = [o for o in data["operations_via_hooks"] if o.get("symbol")]
    return {"project": project, **data}


@router.get("/by-component", response_model=ComponentRoutesResponse)
async def routes_by_component(project: str, name: str) -> dict:
    cypher = """
        MATCH (c:Component {project: $project, name: $name})
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(c)
        RETURN
          {name: c.name, file: c.file, line: c.line} AS component,
          collect(DISTINCT r.path) AS routes
    """
    with session() as s:
        rec = s.run(cypher, project=project, name=name).single()
    if not rec or rec["component"] is None:
        raise HTTPException(404, f"component not found: {name} (project={project})")
    return {"project": project, **rec.data()}
