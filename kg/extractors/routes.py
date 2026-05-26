"""Routes + Components + Guards + Permissions extractor."""

from __future__ import annotations

import os
from typing import Any

from ..db import session, wipe_labels
from ..http import client

NAME = "routes"
LABELS = ("Route", "Component", "Guard", "Permission")


# Per-project router files. Override via env: ROUTER_FILES_<NAME>="a,b,c"
_DEFAULT_ROUTER_FILES: dict[str, list[str]] = {
    "adsw": ["src/router/index.tsx"],
    "network": ["src/router/index.tsx"],
}


def _router_files(project: str) -> list[str]:
    override = os.getenv(f"ROUTER_FILES_{project.upper()}")
    if override:
        return [s.strip() for s in override.split(",") if s.strip()]
    return _DEFAULT_ROUTER_FILES.get(project, ["src/router/index.tsx"])


async def _fetch(project: str, known_hooks: list[str], known_operations: list[str]) -> dict[str, Any]:
    r = await client().post(
        "/extract/routes",
        json={
            "project": project,
            "routerFiles": _router_files(project),
            "knownHooks": known_hooks,
            "knownOperations": known_operations,
        },
    )
    r.raise_for_status()
    return r.json()


def _write(project: str, payload: dict[str, Any]) -> dict[str, int]:
    routes = payload.get("routes", [])
    components = payload.get("components", [])

    wipe_labels(project, list(LABELS))

    with session() as s:
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

        route_render = [
            {"path": r["path"], "depth": r["depth"], "line": r["line"], "component": comp}
            for r in routes
            for comp in r.get("components", [])
        ]
        if route_render:
            s.run(
                """
                UNWIND $items AS i
                MATCH (rt:Route     {project: $project, path: i.path, depth: i.depth, line: i.line})
                MATCH (co:Component {project: $project, name: i.component})
                MERGE (rt)-[:RENDERS]->(co)
                """,
                project=project,
                items=route_render,
            )

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

        comp_hook = [{"name": c["name"], "hook": h} for c in components for h in c.get("hookCalls", [])]
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

        comp_op = [{"name": c["name"], "symbol": s_} for c in components for s_ in c.get("operationRefs", [])]
        if comp_op:
            s.run(
                """
                UNWIND $items AS i
                MATCH (co:Component    {project: $project, name: i.name})
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


async def run(project: str) -> dict[str, Any]:
    with session() as s:
        hooks = [
            r["name"]
            for r in s.run(
                "MATCH (h:GqlHook {project: $project}) RETURN h.name AS name",
                project=project,
            ).data()
        ]
        ops = [
            r["symbol"]
            for r in s.run(
                "MATCH (o:GqlOperation {project: $project}) RETURN DISTINCT o.symbol AS symbol",
                project=project,
            ).data()
        ]

    payload = await _fetch(project, hooks, ops)
    counts = _write(project, payload)
    return {"stats": payload.get("stats", {}), "written": counts}
