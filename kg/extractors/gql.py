"""GraphQL extractor: orchestrates the Node indexer and writes nodes/edges to Neo4j.

Schema produced:
    (:Project {name})
    (:GqlOperation {project, symbol, gql_name, kind, file, line, exported})
    (:GqlHook {project, name, file, line})
    (:File {project, path})

    (GqlOperation)-[:IN_PROJECT]->(Project)
    (GqlHook)-[:IN_PROJECT]->(Project)
    (GqlHook)-[:WRAPS]->(GqlOperation)             # via known operation symbols in hook body
    (File)-[:USES_OPERATION {line, column}]->(GqlOperation)
    (File)-[:CALLS_HOOK {line, column}]->(GqlHook)
"""
from __future__ import annotations

from typing import Any

import httpx

from ..db import session
from ..settings import settings


async def fetch_gql_extract(project: str) -> dict[str, Any]:
    """Calls the ts-morph indexer to extract gql operations/hooks/callsites."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            f"{settings.indexer_url}/extract/gql",
            json={"project": project},
        )
        r.raise_for_status()
        return r.json()


def write_gql_extract(project: str, payload: dict[str, Any]) -> dict[str, int]:
    """Persists the extract into Neo4j. Idempotent — replaces existing gql nodes/edges for the project."""
    operations = payload.get("operations", [])
    hooks = payload.get("hooks", [])
    callsites = payload.get("callsites", [])

    op_callsites = [c for c in callsites if c.get("targetKind") == "operation"]
    hook_callsites = [c for c in callsites if c.get("targetKind") == "hook"]

    with session() as s:
        # 1) wipe previous gql nodes for the project (relationships go with DETACH)
        s.run(
            """
            MATCH (n {project: $project})
            WHERE n:GqlOperation OR n:GqlHook
            DETACH DELETE n
            """,
            project=project,
        )
        # File nodes are kept — they may have edges from other extractors.
        # Old USES_OPERATION/CALLS_HOOK edges have already been removed via DETACH.

        # 2) operations
        s.run(
            """
            MATCH (p:Project {name: $project})
            UNWIND $ops AS op
            CREATE (o:GqlOperation {
                project:  $project,
                symbol:   op.symbol,
                gql_name: op.gqlName,
                kind:     op.kind,
                file:     op.file,
                line:     op.line,
                exported: op.exported
            })
            MERGE (o)-[:IN_PROJECT]->(p)
            """,
            project=project,
            ops=operations,
        )

        # 3) hooks + WRAPS edges
        s.run(
            """
            MATCH (p:Project {name: $project})
            UNWIND $hooks AS h
            CREATE (hk:GqlHook {
                project: $project,
                name:    h.name,
                file:    h.file,
                line:    h.line
            })
            MERGE (hk)-[:IN_PROJECT]->(p)
            WITH hk, h
            UNWIND h.operations AS opSymbol
            MATCH (o:GqlOperation {project: $project, symbol: opSymbol})
            MERGE (hk)-[:WRAPS]->(o)
            """,
            project=project,
            hooks=hooks,
        )

        # 4) callsites → operations
        if op_callsites:
            s.run(
                """
                UNWIND $cs AS c
                MERGE (f:File {project: $project, path: c.file})
                WITH f, c
                MATCH (o:GqlOperation {project: $project, symbol: c.target})
                MERGE (f)-[:USES_OPERATION {line: c.line, column: c.column}]->(o)
                """,
                project=project,
                cs=op_callsites,
            )

        # 5) callsites → hooks
        if hook_callsites:
            s.run(
                """
                UNWIND $cs AS c
                MERGE (f:File {project: $project, path: c.file})
                WITH f, c
                MATCH (hk:GqlHook {project: $project, name: c.target})
                MERGE (f)-[:CALLS_HOOK {line: c.line, column: c.column}]->(hk)
                """,
                project=project,
                cs=hook_callsites,
            )

    return {
        "operations": len(operations),
        "hooks": len(hooks),
        "callsites": len(callsites),
    }


async def run_for_project(project: str) -> dict[str, Any]:
    payload = await fetch_gql_extract(project)
    counts = write_gql_extract(project, payload)
    return {
        "project": project,
        "stats": payload.get("stats", {}),
        "written": counts,
    }
