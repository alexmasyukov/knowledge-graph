"""Visualization endpoint — runs an arbitrary read Cypher and returns
the result as a cytoscape.js elements payload (nodes + edges).

Memgraph uses `id(n)` rather than Neo4j 5's `elementId(n)` for node
identity; queries here are written against the Memgraph dialect."""

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
    # No cache — viz/index.html is iterated on; users hitting an old
    # version because the browser cached it caused real confusion.
    return FileResponse(
        _VIZ_HTML,
        media_type="text/html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@router.get("/graph")
async def graph_data(
    cypher: str = Query(..., min_length=10),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
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
        nid = item.id
        if nid in nodes:
            return
        label = _label_of(item)
        nodes[nid] = {
            "data": {
                "id": str(nid),
                "label": label,
                "caption": _caption(item, label),
                "props": dict(item),
            }
        }

    def _add_edge(rel):
        rid = rel.id
        if rid in edges:
            return
        _add_node(rel.start_node)
        _add_node(rel.end_node)
        edges[rid] = {
            "data": {
                "id": f"e{rid}",
                "source": str(rel.start_node.id),
                "target": str(rel.end_node.id),
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
                    elif hasattr(item, "relationships"):
                        for n in item.nodes:
                            _add_node(n)
                        for r in item.relationships:
                            _add_edge(r)
            if len(nodes) >= limit:
                break

        # Auto-enrich edges that exist between the nodes we collected,
        # so users can RETURN n1, n2 without binding every edge.
        # Memgraph: use id(n) not elementId().
        if nodes:
            id_list = list(nodes.keys())
            enrich = s.run(
                """
                MATCH (a)-[r]->(b)
                WHERE id(a) IN $ids AND id(b) IN $ids
                RETURN r
                """,
                ids=id_list,
            )
            for record in enrich:
                rel = record["r"]
                _add_edge(rel)

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}
