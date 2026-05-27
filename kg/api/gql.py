"""GraphQL knowledge-graph endpoints.

These read from Memgraph only — they don't touch the live GraphQL
schema (that is the gateway's job, served by other tools in the
arenadata-docs MCP).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ..schema import (
    GqlCallsitesResponse,
    GqlHookInfoResponse,
    GqlOperationListResponse,
)

router = APIRouter(prefix="/gql", tags=["gql"])


@router.get("/operations", response_model=GqlOperationListResponse)
async def list_operations(
    project: str,
    kind: str | None = Query(default=None, description="filter by op kind (query/mutation/...)"),
    name: str | None = Query(default=None, description="substring of TS symbol name"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    where = ["o.project = $project"]
    if kind:
        where.append("o.kind = $kind")
    if name:
        where.append("toLower(o.name) CONTAINS toLower($name)")
    cypher = f"""
        MATCH (o:GqlOperation)
        WHERE {' AND '.join(where)}
        RETURN o.symbol AS symbol, o.gql_name AS gql_name, o.kind AS kind,
               o.name AS name, o.file AS file, o.line AS line,
               o.callsites AS callsites
        ORDER BY o.file, o.name
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, kind=kind, name=name, limit=limit).data()
    operations = [
        {
            "symbol": r["name"] or r["symbol"],
            "gql_name": r["gql_name"],
            "kind": r["kind"],
            "file": r["file"],
            "line": r["line"],
            "callsites": r["callsites"] or 0,
        }
        for r in rows
    ]
    return {"project": project, "count": len(operations), "operations": operations}


@router.get("/hooks/{name}", response_model=GqlHookInfoResponse)
async def hook_info(name: str, project: str) -> dict:
    """Hook names are not unique — legacy and new-convention hooks can
    coexist. Return every definition with its operations and callers
    so duplication is visible."""
    cypher = """
        MATCH (h:GqlHook {project: $project, name: $name})
        OPTIONAL MATCH (h)-[:WRAPS]->(op:GqlOperation)
        OPTIONAL MATCH (f:File)-[c:CALLS_HOOK]->(h)
        WITH h,
             collect(DISTINCT {symbol: op.name, gql_name: op.gql_name, kind: op.kind}) AS operations,
             collect(DISTINCT {file: f.path, lines: c.lines, count: c.count}) AS callers_raw
        RETURN h.file AS file, h.line AS line,
               [o IN operations WHERE o.symbol IS NOT NULL] AS operations,
               callers_raw
        ORDER BY h.file
    """
    with session() as s:
        rows = s.run(cypher, project=project, name=name).data()
    if not rows:
        raise HTTPException(404, f"hook not found: {name} (project={project})")

    definitions = []
    for r in rows:
        callers = []
        for raw in r["callers_raw"]:
            if not raw.get("file"):
                continue
            lines = raw.get("lines") or [0]
            for line in lines:
                callers.append({"file": raw["file"], "line": line, "column": 0})
        definitions.append({
            "location": {"file": r["file"], "line": r["line"]},
            "operations": r["operations"],
            "callers": callers,
        })

    return {"project": project, "name": name, "definitions": definitions}


@router.get("/callsites", response_model=GqlCallsitesResponse)
async def find_callsites(
    project: str,
    target: str = Query(..., description="GqlOperation name (UPPER_SNAKE_CASE) or hook name (useX)"),
) -> dict:
    """Find every callsite of an operation OR hook by name. Name
    duplicates (e.g. two `useCourses`) yield multiple match groups."""
    op_cypher = """
        MATCH (o:GqlOperation {project: $project, name: $target})
        OPTIONAL MATCH (f:File)-[u:USES_OPERATION]->(o)
        WITH o, collect(DISTINCT {file: f.path, lines: u.lines}) AS callers
        RETURN o.file AS file, o.line AS line,
               [c IN callers WHERE c.file IS NOT NULL] AS callers
    """
    hook_cypher = """
        MATCH (h:GqlHook {project: $project, name: $target})
        OPTIONAL MATCH (h)-[:WRAPS]->(op:GqlOperation)
        OPTIONAL MATCH (f:File)-[c:CALLS_HOOK]->(h)
        WITH h,
             collect(DISTINCT {symbol: op.name, gql_name: op.gql_name, kind: op.kind}) AS operations,
             collect(DISTINCT {file: f.path, lines: c.lines}) AS callers
        RETURN h.file AS file, h.line AS line,
               [o IN operations WHERE o.symbol IS NOT NULL] AS operations,
               [c IN callers WHERE c.file IS NOT NULL] AS callers
    """

    def _explode(rows: list[dict], with_ops: bool) -> list[dict]:
        out: list[dict] = []
        for r in rows:
            callsites: list[dict] = []
            for c in r.get("callers", []):
                for ln in c.get("lines") or [0]:
                    callsites.append({"file": c["file"], "line": ln, "column": 0})
            item = {"definition": {"file": r["file"], "line": r["line"]}, "callsites": callsites}
            if with_ops:
                item["operations"] = r.get("operations", [])
            out.append(item)
        return out

    with session() as s:
        op_rows = s.run(op_cypher, project=project, target=target).data()
        hook_rows = s.run(hook_cypher, project=project, target=target).data()

    if not op_rows and not hook_rows:
        raise HTTPException(404, f"no operation or hook named {target!r} (project={project})")

    # Match the schema: hook_matches reuse GqlHookDefinition shape.
    hook_matches = []
    for hr in _explode(hook_rows, with_ops=True):
        hook_matches.append({
            "location": hr["definition"],
            "operations": hr.get("operations", []),
            "callers": hr["callsites"],
        })

    return {
        "project": project,
        "target": target,
        "operation_matches": _explode(op_rows, with_ops=False),
        "hook_matches": hook_matches,
    }
