"""Routes read API.

Endpoints:
  GET /routes/list            flat listing with optional prefix filter
  GET /routes/resolve         full picture for one route (components + gql)
  GET /routes/by-component    routes that render a given component name

Response shapes match the MCP wrapper in `arenadata-mcp/tools/kg.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get("/list")
async def list_routes(
    project: str,
    prefix: str | None = Query(default=None, description="filter by path prefix"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    """Flat listing. One row per Route node; aggregates the names of
    rendered components, guards, and permissions for that route."""
    where = "r.project = $project"
    if prefix:
        where += " AND r.path STARTS WITH $prefix"
    cypher = f"""
        MATCH (r:Route)
        WHERE {where}
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

    routes = []
    for r in rows:
        routes.append({
            "path": r["path"],
            "index": bool(r["index"]),
            "depth": r["depth"],
            "file": r["file"],
            "line": r["line"],
            "components": [x for x in (r["components"] or []) if x],
            "guards": [x for x in (r["guards"] or []) if x],
            "permissions": [x for x in (r["permissions"] or []) if x],
        })
    return {"project": project, "count": len(routes), "routes": routes}


@router.get("/resolve")
async def resolve_route(project: str, path: str) -> dict:
    """Full picture for a path. Gql operations are collected through
    three channels:
      1. direct USES_OPERATION from the rendered Component
      2. WRAPS edges of any hook the Component calls
      3. any GqlHook/GqlOperation whose source lives under the
         component's parent directory (picks up sub-component usage —
         e.g. Booking.tsx delegates to Form/ which is where mutations
         actually live)
    """
    cypher = """
        MATCH (r:Route {project: $project, path: $path})
        WITH r ORDER BY r.depth DESC LIMIT 1
        OPTIONAL MATCH (r)-[:RENDERS]->(c:Component)
        OPTIONAL MATCH (r)-[:GUARDED_BY]->(g:Guard)
        OPTIONAL MATCH (r)-[:REQUIRES]->(p:Permission)
        WITH r, collect(DISTINCT c) AS comps, collect(DISTINCT g.name) AS guards, collect(DISTINCT p.key) AS perms
        // Per-component hook + op lookups (direct, then via hooks)
        UNWIND CASE WHEN size(comps) = 0 THEN [null] ELSE comps END AS c
        OPTIONAL MATCH (c)-[:CALLS_HOOK]->(h:GqlHook)
        OPTIONAL MATCH (c)-[:USES_OPERATION]->(o:GqlOperation)
        OPTIONAL MATCH (h)-[:WRAPS]->(opViaHook:GqlOperation)
        // Component → page directory (everything under it). Strip the
        // file extension so 'src/pages/foo/bar/Baz.tsx' → 'src/pages/foo/bar/'.
        WITH r, comps, guards, perms, c, h, o, opViaHook,
             CASE WHEN c IS NULL THEN null
                  ELSE substring(c.file, 0, size(c.file) - size(split(c.file, '/')[-1])) END AS comp_dir
        OPTIONAL MATCH (hPage:GqlHook {project: $project})
          WHERE comp_dir IS NOT NULL AND hPage.file STARTS WITH comp_dir
        OPTIONAL MATCH (hPage)-[:WRAPS]->(opPageHook:GqlOperation)
        OPTIONAL MATCH (fSub:File {project: $project})-[:USES_OPERATION]->(opPageDirect:GqlOperation)
          WHERE comp_dir IS NOT NULL AND fSub.path STARTS WITH comp_dir
        RETURN
          {path: r.path, index: r.index, depth: r.depth, file: r.file, line: r.line} AS route,
          guards, perms AS permissions,
          collect(DISTINCT {name: c.name, file: c.file, line: c.line}) AS components,
          collect(DISTINCT {hook: h.name, file: h.file}) +
            collect(DISTINCT {hook: hPage.name, file: hPage.file})         AS hooks_via_components,
          collect(DISTINCT {symbol: o.symbol, gql_name: o.gql_name, kind: o.kind}) +
            collect(DISTINCT {symbol: opPageDirect.symbol, gql_name: opPageDirect.gql_name, kind: opPageDirect.kind})
              AS operations_via_components,
          collect(DISTINCT {symbol: opViaHook.symbol, gql_name: opViaHook.gql_name, kind: opViaHook.kind}) +
            collect(DISTINCT {symbol: opPageHook.symbol, gql_name: opPageHook.gql_name, kind: opPageHook.kind})
              AS operations_via_hooks
    """
    with session() as s:
        rec = s.run(cypher, project=project, path=path).single()
    if not rec or rec["route"] is None:
        raise HTTPException(404, f"route not found: {path} (project={project})")

    def _clean(lst, key):
        seen: set = set()
        out = []
        for item in lst or []:
            v = item.get(key)
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(item)
        return out

    data = rec.data()
    return {
        "project": project,
        "route": data["route"],
        "guards": [x for x in data.get("guards") or [] if x],
        "permissions": [x for x in data.get("permissions") or [] if x],
        "components": _clean(data.get("components") or [], "name"),
        "hooks_via_components": _clean(data.get("hooks_via_components") or [], "hook"),
        "operations_via_components": _clean(data.get("operations_via_components") or [], "symbol"),
        "operations_via_hooks": _clean(data.get("operations_via_hooks") or [], "symbol"),
    }


@router.get("/by-component")
async def routes_by_component(project: str, name: str) -> dict:
    """Find every Route that renders a Component with the given name.
    Multiple Component nodes may share a name (legacy + new); the
    response collapses them and lists the union of routes."""
    cypher = """
        MATCH (c:Component {project: $project, name: $name})
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(c)
        WITH c, collect(DISTINCT r.path) AS paths
        RETURN
          collect(DISTINCT {name: c.name, file: c.file, line: c.line}) AS components,
          [p IN apoc.coll.flatten(collect(paths)) WHERE p IS NOT NULL] AS routes
    """
    # APOC isn't guaranteed on Memgraph — flatten manually below.
    cypher = """
        MATCH (c:Component {project: $project, name: $name})
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(c)
        RETURN
          c.name AS name, c.file AS file, c.line AS line,
          collect(DISTINCT r.path) AS routes
    """
    with session() as s:
        rows = s.run(cypher, project=project, name=name).data()
    if not rows:
        raise HTTPException(404, f"component not found: {name} (project={project})")

    # If the component appears in multiple files, pick the one with the
    # most routes — that's the live component, the others are typically
    # legacy duplicates the routes never actually use.
    rows.sort(key=lambda r: -len([p for p in (r["routes"] or []) if p]))
    primary = rows[0]
    routes = [p for p in (primary["routes"] or []) if p]
    return {
        "project": project,
        "component": {"name": primary["name"], "file": primary["file"], "line": primary["line"]},
        "routes": sorted(routes),
    }
