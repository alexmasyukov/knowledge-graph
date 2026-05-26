"""Batched MERGE helpers for writing extractor output into Memgraph.

Each extractor returns IngestResult(nodes, edges). This module turns
that into Cypher in batches sized to stay well under Memgraph's
default Bolt message limits.

Identity rules:
  - Every node carries `project` so we can MERGE per-project.
  - Identity key per label is set in NODE_KEYS below; everything else
    is overwritten on every MERGE (idempotent).
  - Edges MERGE by (source label, source key) → (target label, target
    key), with the `type` taken from the row.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from ..db import session

BATCH_SIZE = 500

# Label → (key properties used to MERGE; all other props are SET).
# Adjust here when introducing a new node type.
NODE_KEYS: dict[str, tuple[str, ...]] = {
    "File":         ("project", "path"),
    "GqlOperation": ("project", "symbol"),
    "GqlHook":      ("project", "symbol"),
    "Component":    ("project", "file", "name"),
    "Route":        ("project", "path", "depth", "line"),
    "Page":         ("project", "domain", "entity"),
    "Permission":   ("project", "key"),
    "Role":         ("project", "code"),
    "Guard":        ("project", "name"),
    "TestId":       ("project", "value", "pattern"),
    "TestIdLoc":    ("project", "name", "file"),
    "PageObject":   ("project", "name"),
    "E2eSpec":      ("project", "file", "line"),
    "Doc":          ("project", "file"),
    "Domain":       ("project", "name"),
    "ScssModule":   ("project", "file"),
    "ScssClass":    ("project", "module", "name"),
    "Type":         ("project", "name"),
}


def _chunks(seq: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def write_nodes(label: str, rows: Iterable[dict]) -> int:
    """MERGE the given rows under `label`. Returns number written."""
    rows = list(rows)
    if not rows:
        return 0
    key_props = NODE_KEYS.get(label)
    if not key_props:
        raise KeyError(f"NODE_KEYS missing entry for label {label!r}")
    key_pairs = ", ".join(f"{k}: row.{k}" for k in key_props)
    cypher = f"""
        UNWIND $rows AS row
        MERGE (n:{label} {{{key_pairs}}})
        SET n += row
    """
    total = 0
    with session() as s:
        for chunk in _chunks(rows, BATCH_SIZE):
            s.run(cypher, rows=chunk).consume()
            total += len(chunk)
    return total


def write_edges(rows: Iterable[dict]) -> int:
    """MERGE edges. Each row is:
        {
            "source": {"label": str, "key": {...}},
            "type":   str,
            "target": {"label": str, "key": {...}},
            "props":  {...}            # optional, set on the edge
        }
    Edges are written one *type* at a time — Memgraph doesn't allow a
    parameterised relationship type in MATCH, so we group and emit a
    dedicated query per (source_label, type, target_label) triplet.
    """
    rows = list(rows)
    if not rows:
        return 0

    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (row["source"]["label"], row["type"], row["target"]["label"])
        groups.setdefault(key, []).append(row)

    total = 0
    with session() as s:
        for (src_label, etype, tgt_label), edges in groups.items():
            src_keys = NODE_KEYS[src_label]
            tgt_keys = NODE_KEYS[tgt_label]
            src_match = ", ".join(f"{k}: row.source.key.{k}" for k in src_keys)
            tgt_match = ", ".join(f"{k}: row.target.key.{k}" for k in tgt_keys)
            cypher = f"""
                UNWIND $rows AS row
                MATCH (a:{src_label} {{{src_match}}})
                MATCH (b:{tgt_label} {{{tgt_match}}})
                MERGE (a)-[r:{etype}]->(b)
                SET r += coalesce(row.props, {{}})
            """
            for chunk in _chunks(edges, BATCH_SIZE):
                s.run(cypher, rows=chunk).consume()
                total += len(chunk)
    return total


def attach_to_project(project: str, labels: list[str]) -> None:
    """Link every node of the given labels to its Project. Keeps Browser
    queries `MATCH (p:Project)-[:IN_PROJECT]-(n)` cheap."""
    if not labels:
        return
    with session() as s:
        s.run("MERGE (:Project {name: $project})", project=project)
        for label in labels:
            s.run(
                f"""
                MATCH (n:{label} {{project: $project}})
                MATCH (p:Project {{name: $project}})
                MERGE (n)-[:IN_PROJECT]->(p)
                """,
                project=project,
            )
