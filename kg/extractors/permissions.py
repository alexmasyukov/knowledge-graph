"""Permissions source-of-truth extractor."""

from __future__ import annotations

from typing import Any

from ..db import session
from ..http import client

NAME = "permissions"
# Permission nodes are owned both here and in routes (we MERGE, not wipe).
# Role nodes we do own — but they're rarely changed, so safe to MERGE too.
LABELS: tuple[str, ...] = ()


async def _fetch(project: str) -> dict[str, Any]:
    r = await client().post("/extract/permissions", json={"project": project})
    r.raise_for_status()
    return r.json()


def _write(project: str, payload: dict[str, Any]) -> dict[str, int]:
    perms = payload.get("permissions", [])
    if not perms:
        return {"permissions": 0, "roles": 0}

    roles = sorted({role for p in perms for role in p["roles"]})

    with session() as s:
        s.run(
            """
            MATCH (p:Project {name: $project})
            UNWIND $roles AS r
            MERGE (role:Role {project: $project, code: r})
            MERGE (role)-[:IN_PROJECT]->(p)
            """,
            project=project,
            roles=roles,
        )

        s.run(
            """
            MATCH (p:Project {name: $project})
            UNWIND $perms AS pm
            MERGE (pe:Permission {project: $project, key: pm.key})
            SET pe.roles = pm.roles, pe.file = pm.file, pe.line = pm.line
            MERGE (pe)-[:IN_PROJECT]->(p)
            WITH pe, pm
            UNWIND pm.roles AS roleCode
            MATCH (role:Role {project: $project, code: roleCode})
            MERGE (pe)-[:GRANTED_TO]->(role)
            """,
            project=project,
            perms=perms,
        )

    return {"permissions": len(perms), "roles": len(roles)}


async def run(project: str) -> dict[str, Any]:
    payload = await _fetch(project)
    counts = _write(project, payload)
    return {"stats": payload.get("stats", {}), "written": counts}
