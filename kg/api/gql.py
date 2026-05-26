"""Read-only GraphQL graph endpoints (Phase 1)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session


router = APIRouter(prefix="/gql", tags=["gql"])


@router.get("/operations")
async def list_operations(
    project: str,
    kind: str | None = Query(default=None, description="query|mutation|subscription|fragment"),
    name_substr: str | None = Query(default=None, description="filter by case-insensitive substring of symbol or gql_name"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    cypher = """
        MATCH (o:GqlOperation {project: $project})
        WHERE ($kind IS NULL OR o.kind = $kind)
          AND ($q IS NULL OR toLower(o.symbol) CONTAINS toLower($q)
               OR toLower(o.gql_name) CONTAINS toLower($q))
        OPTIONAL MATCH (h:GqlHook)-[:WRAPS]->(o)
        WITH o, collect(DISTINCT h.name) AS hooks
        RETURN o.symbol AS symbol, o.gql_name AS gql_name, o.kind AS kind,
               o.file AS file, o.line AS line, o.exported AS exported, hooks
        ORDER BY o.kind, o.symbol
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, kind=kind, q=name_substr, limit=limit).data()
    return {"project": project, "count": len(rows), "operations": rows}


@router.get("/hooks/{name}")
async def hook_info(name: str, project: str) -> dict:
    cypher = """
        MATCH (h:GqlHook {project: $project, name: $name})
        OPTIONAL MATCH (h)-[:WRAPS]->(o:GqlOperation)
        OPTIONAL MATCH (f:File)-[c:CALLS_HOOK]->(h)
        WITH h,
             collect(DISTINCT {symbol: o.symbol, gql_name: o.gql_name, kind: o.kind}) AS operations,
             collect(DISTINCT {file: f.path, line: c.line, column: c.column}) AS callers
        RETURN
          {file: h.file, line: h.line} AS location,
          [op IN operations WHERE op.symbol IS NOT NULL] AS operations,
          [c IN callers WHERE c.file IS NOT NULL] AS callers
    """
    with session() as s:
        rec = s.run(cypher, project=project, name=name).single()
    if not rec or rec["location"] is None:
        raise HTTPException(404, f"hook not found: {name} in project {project}")
    return {"project": project, "name": name, **rec.data()}


@router.get("/callsites")
async def find_callsites(
    project: str,
    target: str = Query(..., description="GqlOperation symbol OR GqlHook name"),
) -> dict:
    """Return all callsites of the given symbol — operation or hook."""
    # operation callsites
    op_cypher = """
        MATCH (o:GqlOperation {project: $project, symbol: $target})
        OPTIONAL MATCH (f:File)-[c:USES_OPERATION]->(o)
        WITH o, collect(DISTINCT {file: f.path, line: c.line, column: c.column}) AS sites
        RETURN
          {symbol: o.symbol, gql_name: o.gql_name, kind: o.kind, file: o.file, line: o.line} AS def,
          [s IN sites WHERE s.file IS NOT NULL] AS callsites
    """
    # hook callsites
    hook_cypher = """
        MATCH (h:GqlHook {project: $project, name: $target})
        OPTIONAL MATCH (f:File)-[c:CALLS_HOOK]->(h)
        WITH h, collect(DISTINCT {file: f.path, line: c.line, column: c.column}) AS sites
        RETURN
          {name: h.name, file: h.file, line: h.line} AS def,
          [s IN sites WHERE s.file IS NOT NULL] AS callsites
    """

    out: dict = {"project": project, "target": target, "as_operation": [], "as_hook": None}
    with session() as s:
        for r in s.run(op_cypher, project=project, target=target).data():
            out["as_operation"].append(r)
        hook_rec = s.run(hook_cypher, project=project, target=target).single()
        if hook_rec:
            out["as_hook"] = hook_rec.data()

    if not out["as_operation"] and out["as_hook"] is None:
        raise HTTPException(404, f"no operation or hook named {target!r} in project {project}")
    return out
