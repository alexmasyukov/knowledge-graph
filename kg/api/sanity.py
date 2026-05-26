"""Data quality probes.

`GET /sanity?project=X` runs a battery of read-only Cypher probes that
surface structural anomalies the developer didn't necessarily know to
look for. Distinct from tests (which guard known behaviours) — these
flag *unknown unknowns*: duplicate hook names, components nobody routes
to, template substrings that leaked into stored values, runtime
permission keys that aren't defined, and so on.

Add probes by appending a callable to ALL_PROBES. Each probe returns
its findings as a plain list of dicts; the endpoint sums them into a
single report.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from ..db import session

router = APIRouter(prefix="/sanity", tags=["sanity"])

Probe = Callable[[str], list[dict[str, Any]]]


def _q(cypher: str, **params) -> list[dict]:
    with session() as s:
        return s.run(cypher, **params).data()


# ── probes ─────────────────────────────────────────────────────────────

def probe_duplicate_hook_names(project: str) -> list[dict]:
    """Hook names that map to multiple definition files. Catches legacy
    + new-convention coexistence (the original useCourses bug)."""
    rows = _q(
        """
        MATCH (h:GqlHook {project: $project})
        WITH h.name AS name, collect(DISTINCT h.file) AS files
        WHERE size(files) > 1
        RETURN name, files
        ORDER BY size(files) DESC, name
        """,
        project=project,
    )
    return [{"label": "GqlHook", "name": r["name"], "files": r["files"]} for r in rows]


def probe_duplicate_component_names(project: str) -> list[dict]:
    rows = _q(
        """
        MATCH (c:Component {project: $project})
        WITH c.name AS name, collect(DISTINCT c.file) AS files
        WHERE size(files) > 1
        RETURN name, files
        ORDER BY size(files) DESC, name
        """,
        project=project,
    )
    return [{"label": "Component", "name": r["name"], "files": r["files"]} for r in rows]


def probe_orphan_components(project: str) -> list[dict]:
    """Components that aren't rendered by any Route. They might be sub-
    components or dead code; either way worth a glance."""
    rows = _q(
        """
        MATCH (c:Component {project: $project})
        WHERE NOT (:Route)-[:RENDERS]->(c)
        RETURN c.name AS name, c.file AS file
        ORDER BY c.file, c.name
        LIMIT 200
        """,
        project=project,
    )
    return rows


def probe_unused_operations(project: str) -> list[dict]:
    """GqlOperation nodes with zero callsites — typically left after a
    feature gets removed but the query file stayed."""
    rows = _q(
        """
        MATCH (o:GqlOperation {project: $project})
        WHERE o.callsites IS NULL OR o.callsites = 0
        RETURN o.name AS name, o.gql_name AS gql_name, o.file AS file
        ORDER BY o.file
        LIMIT 200
        """,
        project=project,
    )
    return rows


def probe_template_in_route_paths(project: str) -> list[dict]:
    """Route paths that still carry `${...}` — extractor missed an enum
    or template substitution it couldn't resolve."""
    rows = _q(
        """
        MATCH (r:Route {project: $project})
        WHERE r.path CONTAINS '${'
        RETURN r.path AS path, r.file AS file, r.line AS line
        ORDER BY r.path
        """,
        project=project,
    )
    return rows


def probe_template_in_testids(project: str) -> list[dict]:
    rows = _q(
        """
        MATCH (t:TestId {project: $project})
        WHERE t.value CONTAINS '${'
        RETURN t.value AS value, t.pattern AS pattern, t.first_file AS file
        ORDER BY t.value
        LIMIT 200
        """,
        project=project,
    )
    return rows


def probe_permissions_unused(project: str) -> list[dict]:
    """Permissions defined in source but no Route REQUIRES them. Either
    runtime-checked elsewhere or dead permission."""
    rows = _q(
        """
        MATCH (p:Permission {project: $project})
        WHERE p.file IS NOT NULL
          AND NOT (:Route)-[:REQUIRES]->(p)
        RETURN p.key AS key, p.file AS file
        ORDER BY p.key
        LIMIT 200
        """,
        project=project,
    )
    return rows


def probe_permissions_undeclared(project: str) -> list[dict]:
    """Permissions referenced by Routes but not defined in
    src/common/permissions/index.ts — i.e. typo'd or stale key."""
    rows = _q(
        """
        MATCH (perm:Permission {project: $project})
        WHERE perm.file IS NULL
        OPTIONAL MATCH (r:Route)-[:REQUIRES]->(perm)
        WITH perm, collect(DISTINCT r.path) AS routes
        RETURN perm.key AS key, routes
        ORDER BY perm.key
        """,
        project=project,
    )
    return rows


def probe_outlier_hook_callsites(project: str, threshold: int = 30) -> list[dict]:
    rows = _q(
        """
        MATCH (h:GqlHook {project: $project})
        OPTIONAL MATCH (f:File)-[:CALLS_HOOK]->(h)
        WITH h, count(DISTINCT f) AS callers
        WHERE callers >= $threshold
        RETURN h.name AS name, h.file AS file, callers
        ORDER BY callers DESC
        """,
        project=project,
        threshold=threshold,
    )
    return rows


def probe_components_without_page(project: str) -> list[dict]:
    """A Component under src/pages/ that doesn't BELONG_TO any Page —
    means the pages extractor didn't find it. Often a sub-component
    that lives one directory deeper than the entity root."""
    rows = _q(
        """
        MATCH (c:Component {project: $project})
        WHERE c.file STARTS WITH 'src/pages/' AND NOT (c)-[:BELONGS_TO]->(:Page)
        RETURN c.name AS name, c.file AS file
        ORDER BY c.file, c.name
        LIMIT 200
        """,
        project=project,
    )
    return rows


ALL_PROBES: list[tuple[str, Probe]] = [
    ("duplicate_hook_names", probe_duplicate_hook_names),
    ("duplicate_component_names", probe_duplicate_component_names),
    ("orphan_components", probe_orphan_components),
    ("unused_operations", probe_unused_operations),
    ("template_in_route_paths", probe_template_in_route_paths),
    ("template_in_testids", probe_template_in_testids),
    ("permissions_unused", probe_permissions_unused),
    ("permissions_undeclared", probe_permissions_undeclared),
    ("outlier_hook_callsites", probe_outlier_hook_callsites),
    ("components_without_page", probe_components_without_page),
]


@router.get("")
async def sanity(project: str) -> dict:
    findings: dict[str, list[dict]] = {}
    summary: dict[str, int] = {}
    for name, probe in ALL_PROBES:
        try:
            result = probe(project)
        except Exception as e:
            result = [{"_error": f"{type(e).__name__}: {e}"}]
        findings[name] = result
        summary[name] = len(result)
    summary["total_issues"] = sum(summary.values())
    return {"project": project, "summary": summary, "findings": findings}
