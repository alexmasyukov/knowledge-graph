"""Permissions source-of-truth extractor.

Phase 2 created :Permission nodes from the JSX attribute usage. This
extractor reads src/common/permissions/index.ts via the ts-morph indexer
and back-fills the same nodes with their canonical roles[] and source
location.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..db import session
from ..settings import settings


async def fetch_permissions(project: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{settings.indexer_url}/extract/permissions",
            json={"project": project},
        )
        r.raise_for_status()
        return r.json()


def write_permissions(project: str, payload: dict[str, Any]) -> dict[str, int]:
    perms = payload.get("permissions", [])
    if not perms:
        return {"permissions": 0, "roles": 0}

    roles = sorted({role for p in perms for role in p["roles"]})

    with session() as s:
        # Role nodes
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

        # Upsert Permission attributes (MERGE so we extend Phase 2 nodes if they exist)
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


async def run_for_project(project: str) -> dict[str, Any]:
    payload = await fetch_permissions(project)
    counts = write_permissions(project, payload)
    return {"project": project, "stats": payload.get("stats", {}), "written": counts}
