from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from neo4j import Driver, GraphDatabase, Session

from .settings import settings


_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
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
    """Verify connectivity. Returns server metadata."""
    with session() as s:
        rec = s.run("CALL dbms.components() YIELD name, versions, edition").single()
        if not rec:
            return {"ok": False}
        return {
            "ok": True,
            "name": rec["name"],
            "versions": rec["versions"],
            "edition": rec["edition"],
        }


def init_schema() -> None:
    """Idempotent schema setup: constraints and indexes."""
    with session() as s:
        # Unique project identity
        s.run(
            "CREATE CONSTRAINT project_name IF NOT EXISTS "
            "FOR (p:Project) REQUIRE p.name IS UNIQUE"
        )
        # Generic node identity: (project, kind, qualified_name)
        s.run(
            "CREATE CONSTRAINT entity_qid IF NOT EXISTS "
            "FOR (n:Entity) REQUIRE (n.project, n.kind, n.qid) IS UNIQUE"
        )
        # Useful indexes
        s.run("CREATE INDEX entity_kind IF NOT EXISTS FOR (n:Entity) ON (n.project, n.kind)")
        s.run("CREATE INDEX entity_name IF NOT EXISTS FOR (n:Entity) ON (n.project, n.name)")
        s.run("CREATE INDEX entity_file IF NOT EXISTS FOR (n:Entity) ON (n.project, n.file)")


def wipe_labels(project: str, labels: list[str]) -> None:
    """DETACH DELETE all nodes of the given labels belonging to a project.

    Used by extractors at the start of write_*() for idempotency.
    """
    if not labels:
        return
    where = " OR ".join(f"n:{lbl}" for lbl in labels)
    with session() as s:
        s.run(
            f"""
            MATCH (n {{project: $project}})
            WHERE {where}
            DETACH DELETE n
            """,
            project=project,
        )
