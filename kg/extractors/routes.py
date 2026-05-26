"""Routes + Components + Guards + Permissions extractor.

Schema:
    (:Route       {project, path, index, depth, file, line})
    (:Component   {project, name, file, line, exported})
    (:Guard       {project, name})
    (:Permission  {project, key})

    (Route|Component|Guard|Permission)-[:IN_PROJECT]->(Project)
    (Route)-[:CHILD_OF]->(Route)
    (Route)-[:RENDERS]->(Component)
    (Route)-[:GUARDED_BY]->(Guard)
    (Route)-[:REQUIRES]->(Permission)
    (Component)-[:CALLS_HOOK]->(GqlHook)
    (Component)-[:USES_OPERATION]->(GqlOperation)
"""
from __future__ import annotations

from typing import Any

import httpx

from ..db import session
from ..settings import settings


# Adsw router file relative to project root
ROUTER_FILES_BY_PROJECT: dict[str, list[str]] = {
    "adsw": ["src/router/index.tsx"],
    "network": ["src/router/index.tsx"],
}


async def fetch_routes_extract(project: str, known_hooks: list[str], known_operations: list[str]) -> dict[str, Any]:
    files = ROUTER_FILES_BY_PROJECT.get(project, ["src/router/index.tsx"])
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            f"{settings.indexer_url}/extract/routes",
            json={
                "project": project,
                "routerFiles": files,
                "knownHooks": known_hooks,
                "knownOperations": known_operations,
            },
        )
        r.raise_for_status()
        return r.json()


def write_routes_extract(project: str, payload: dict[str, Any]) -> dict[str, int]:
    routes = payload.get("routes", [])
    components = payload.get("components", [])

    with session() as s:
        # 1) wipe previous nodes for this project
        s.run(
            """
            MATCH (n {project: $project})
            WHERE n:Route OR n:Component OR n:Guard OR n:Permission
            DETACH DELETE n
            """,
            project=project,
        )

        # 2) routes
        s.run(
            """
            MATCH (p:Project {name: $project})
            UNWIND $routes AS r
            CREATE (rt:Route {
                project: $project,
                path:    r.path,
                index:   r.index,
                depth:   r.depth,
                file:    r.file,
                line:    r.line
            })
            MERGE (rt)-[:IN_PROJECT]->(p)
            """,
            project=project,
            routes=routes,
        )

        # 3) components
        s.run(
            """
            MATCH (p:Project {name: $project})
            UNWIND $components AS c
            CREATE (co:Component {
                project:  $project,
                name:     c.name,
                file:     c.file,
                line:     c.line,
                exported: c.exported
            })
            MERGE (co)-[:IN_PROJECT]->(p)
            """,
            project=project,
            components=components,
        )

        # 4) guards & permissions (deduplicated names/keys)
        guards = sorted({g for r in routes for g in r.get("guards", [])})
        permissions = sorted({p for r in routes for p in r.get("permissions", [])})

        if guards:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $names AS n
                MERGE (g:Guard {project: $project, name: n})
                MERGE (g)-[:IN_PROJECT]->(p)
                """,
                project=project,
                names=guards,
            )

        if permissions:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $keys AS k
                MERGE (pe:Permission {project: $project, key: k})
                MERGE (pe)-[:IN_PROJECT]->(p)
                """,
                project=project,
                keys=permissions,
            )

        # 5) Route → Route (CHILD_OF), keyed by (path, depth, line)
        s.run(
            """
            UNWIND $routes AS r
            WITH r WHERE r.parentPath IS NOT NULL
            MATCH (child:Route {project: $project, path: r.path, depth: r.depth, line: r.line})
            MATCH (parent:Route {project: $project, path: r.parentPath, depth: r.depth - 1})
            MERGE (child)-[:CHILD_OF]->(parent)
            """,
            project=project,
            routes=routes,
        )

        # 6) Route → Component (RENDERS)
        route_render = [
            {"path": r["path"], "depth": r["depth"], "line": r["line"], "component": comp}
            for r in routes
            for comp in r.get("components", [])
        ]
        if route_render:
            s.run(
                """
                UNWIND $items AS i
                MATCH (rt:Route      {project: $project, path: i.path, depth: i.depth, line: i.line})
                MATCH (co:Component  {project: $project, name: i.component})
                MERGE (rt)-[:RENDERS]->(co)
                """,
                project=project,
                items=route_render,
            )

        # 7) Route → Guard
        route_guard = [
            {"path": r["path"], "depth": r["depth"], "line": r["line"], "guard": g}
            for r in routes
            for g in r.get("guards", [])
        ]
        if route_guard:
            s.run(
                """
                UNWIND $items AS i
                MATCH (rt:Route {project: $project, path: i.path, depth: i.depth, line: i.line})
                MATCH (g:Guard  {project: $project, name: i.guard})
                MERGE (rt)-[:GUARDED_BY]->(g)
                """,
                project=project,
                items=route_guard,
            )

        # 8) Route → Permission
        route_perm = [
            {"path": r["path"], "depth": r["depth"], "line": r["line"], "key": k}
            for r in routes
            for k in r.get("permissions", [])
        ]
        if route_perm:
            s.run(
                """
                UNWIND $items AS i
                MATCH (rt:Route      {project: $project, path: i.path, depth: i.depth, line: i.line})
                MATCH (pe:Permission {project: $project, key: i.key})
                MERGE (rt)-[:REQUIRES]->(pe)
                """,
                project=project,
                items=route_perm,
            )

        # 9) Component → GqlHook (CALLS_HOOK)
        comp_hook = [
            {"name": c["name"], "hook": h}
            for c in components
            for h in c.get("hookCalls", [])
        ]
        if comp_hook:
            s.run(
                """
                UNWIND $items AS i
                MATCH (co:Component {project: $project, name: i.name})
                MATCH (h:GqlHook    {project: $project, name: i.hook})
                MERGE (co)-[:CALLS_HOOK]->(h)
                """,
                project=project,
                items=comp_hook,
            )

        # 10) Component → GqlOperation (USES_OPERATION)
        comp_op = [
            {"name": c["name"], "symbol": s_}
            for c in components
            for s_ in c.get("operationRefs", [])
        ]
        if comp_op:
            s.run(
                """
                UNWIND $items AS i
                MATCH (co:Component   {project: $project, name: i.name})
                MATCH (op:GqlOperation {project: $project, symbol: i.symbol})
                MERGE (co)-[:USES_OPERATION]->(op)
                """,
                project=project,
                items=comp_op,
            )

    return {
        "routes": len(routes),
        "components": len(components),
        "guards": len({g for r in routes for g in r.get("guards", [])}),
        "permissions": len({p for r in routes for p in r.get("permissions", [])}),
    }


async def run_for_project(project: str) -> dict[str, Any]:
    # Fetch known hooks and operations from existing graph (Phase 1 must have run)
    with session() as s:
        hooks = [r["name"] for r in s.run("MATCH (h:GqlHook {project: $project}) RETURN h.name AS name", project=project).data()]
        ops = [r["symbol"] for r in s.run("MATCH (o:GqlOperation {project: $project}) RETURN DISTINCT o.symbol AS symbol", project=project).data()]

    payload = await fetch_routes_extract(project, hooks, ops)
    counts = write_routes_extract(project, payload)
    return {"project": project, "stats": payload.get("stats", {}), "written": counts}
