"""Visualization endpoint — runs an arbitrary read Cypher and returns
the result as a cytoscape.js elements payload (nodes + edges)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..db import session

router = APIRouter(prefix="/viz", tags=["viz"])


_VIZ_HTML = Path(__file__).parent.parent.parent / "viz" / "index.html"


@router.get("/")
async def viz_page() -> FileResponse:
    if not _VIZ_HTML.exists():
        raise HTTPException(500, f"viz/index.html missing at {_VIZ_HTML}")
    return FileResponse(_VIZ_HTML, media_type="text/html")


@router.get("/graph")
async def graph_data(
    cypher: str = Query(..., min_length=10),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    """Run a Cypher query and return cytoscape elements.

    Cypher must return nodes/relationships (not scalars). Anything else
    is silently dropped. Read-only — we reject write keywords client-side
    by convention; for hardening run under a read role."""
    forbidden = ("CREATE ", "DELETE ", "MERGE ", "SET ", "REMOVE ", "DROP ")
    upper = cypher.upper()
    if any(w in upper for w in forbidden):
        raise HTTPException(400, f"write operations not allowed: {forbidden}")

    nodes: dict[int, dict] = {}
    edges: dict[int, dict] = {}

    def _label_of(node) -> str:
        for lbl in node.labels:
            if lbl != "Project":
                return lbl
        return next(iter(node.labels), "Node")

    def _caption(node, label: str) -> str:
        props = dict(node)
        for key in ("name", "path", "key", "value", "symbol", "title", "gql_name"):
            v = props.get(key)
            if v:
                return str(v)
        if label == "Page":
            return f"{props.get('domain', '?')}/{props.get('entity', '')}"
        if label == "File":
            return props.get("path", "File")
        return label

    def _add_node(item):
        if item.element_id in nodes:
            return
        label = _label_of(item)
        nodes[item.element_id] = {
            "data": {
                "id": str(item.element_id),
                "label": label,
                "caption": _caption(item, label),
                "props": dict(item),
            }
        }

    def _add_edge(rel):
        if rel.element_id in edges:
            return
        _add_node(rel.start_node)
        _add_node(rel.end_node)
        edges[rel.element_id] = {
            "data": {
                "id": f"e{rel.element_id}",
                "source": str(rel.start_node.element_id),
                "target": str(rel.end_node.element_id),
                "label": rel.type,
            }
        }

    with session() as s:
        result = s.run(cypher)
        for record in result:
            for value in record.values():
                if value is None:
                    continue
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if hasattr(item, "labels"):
                        _add_node(item)
                    elif hasattr(item, "type") and hasattr(item, "start_node"):
                        _add_edge(item)
                    elif hasattr(item, "relationships"):  # neo4j Path
                        for n in item.nodes:
                            _add_node(n)
                        for r in item.relationships:
                            _add_edge(r)
            if len(nodes) >= limit:
                break

        # Auto-enrich: pull every relationship that exists between the
        # nodes we collected. Lets users write simple `RETURN n1, n2`
        # queries without having to bind a variable to every edge.
        if nodes:
            id_list = list(nodes.keys())
            enrich = s.run(
                """
                MATCH (a)-[r]->(b)
                WHERE elementId(a) IN $ids AND elementId(b) IN $ids
                RETURN r
                """,
                ids=id_list,
            )
            for record in enrich:
                rel = record["r"]
                _add_edge(rel)

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}
