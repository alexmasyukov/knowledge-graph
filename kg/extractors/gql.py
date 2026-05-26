"""GraphQL extractor: gql operations + hooks + callsites."""

from __future__ import annotations

from typing import Any

from ..db import session, wipe_labels
from ..http import client

NAME = "gql"
LABELS = ("GqlOperation", "GqlHook")


async def _fetch(project: str) -> dict[str, Any]:
    r = await client().post("/extract/gql", json={"project": project})
    r.raise_for_status()
    return r.json()


def _write(project: str, payload: dict[str, Any]) -> dict[str, int]:
    operations = payload.get("operations", [])
    hooks = payload.get("hooks", [])
    callsites = payload.get("callsites", [])
    op_callsites = [c for c in callsites if c.get("targetKind") == "operation"]
    hook_callsites = [c for c in callsites if c.get("targetKind") == "hook"]

    wipe_labels(project, list(LABELS))

    with session() as s:
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


async def run(project: str) -> dict[str, Any]:
    payload = await _fetch(project)
    counts = _write(project, payload)
    return {"stats": payload.get("stats", {}), "written": counts}
