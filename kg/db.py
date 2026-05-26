"""Memgraph connection + idempotent schema bootstrap.

Memgraph speaks the Bolt protocol, so we use the `neo4j` driver — same
library, different server. Cypher syntax has minor differences from
Neo4j; the schema setup below sticks to the Memgraph-supported variants.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from neo4j import Driver, GraphDatabase, Session

from .settings import settings

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        auth = None
        if settings.memgraph_user and settings.memgraph_password:
            auth = (settings.memgraph_user, settings.memgraph_password)
        _driver = GraphDatabase.driver(settings.memgraph_uri, auth=auth)
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@contextmanager
def session() -> Iterator[Session]:
    driver = get_driver()
    with driver.session() as s:
        yield s


def ping() -> dict:
    """Return server metadata. Raises if Memgraph is unreachable."""
    with session() as s:
        row = s.run("SHOW VERSION").single()
        version = row["version"] if row else "unknown"
    return {"ok": True, "name": "Memgraph", "version": version}


def init_schema() -> None:
    """Create uniqueness constraints and indexes if not already present.

    Memgraph doesn't support `IF NOT EXISTS` on schema DDL — we read
    `SHOW INDEX INFO` / `SHOW CONSTRAINT INFO` first and skip what's
    already there. This avoids swallowing real errors via try/except.
    """
    with session() as s:
        existing_indexes: set[tuple[str | None, str | None]] = set()
        for row in s.run("SHOW INDEX INFO").data():
            existing_indexes.add((row.get("label"), row.get("property")))

        existing_constraints: set[tuple[str, str, str]] = set()
        for row in s.run("SHOW CONSTRAINT INFO").data():
            props = row.get("properties") or []
            existing_constraints.add(
                (row.get("constraint_type", ""), row.get("label", ""), ",".join(props))
            )

        if ("unique", "Project", "name") not in existing_constraints:
            s.run("CREATE CONSTRAINT ON (p:Project) ASSERT p.name IS UNIQUE")

        # Per-label property indexes (Memgraph has no composite indexes).
        wanted_indexes = [
            ("Project", "name"),
            ("Symbol", "project"),
            ("Symbol", "id"),
            ("Symbol", "name"),
            ("Symbol", "file"),
            ("Symbol", "kind"),
            ("File", "project"),
            ("File", "path"),
        ]
        for label, prop in wanted_indexes:
            if (label, prop) not in existing_indexes:
                s.run(f"CREATE INDEX ON :{label}({prop})")


def wipe_project(project: str) -> None:
    """Delete every node belonging to the given project. Used at the
    start of a full reindex to guarantee idempotency."""
    with session() as s:
        s.run(
            "MATCH (n {project: $project}) DETACH DELETE n",
            project=project,
        )


def ensure_project(project: str) -> None:
    """Make sure a Project node exists. Idempotent."""
    with session() as s:
        s.run(
            "MERGE (p:Project {name: $project}) RETURN p",
            project=project,
        )
