"""End-to-end indexing pipeline: SCIP → extractors → Memgraph."""

from __future__ import annotations

import time

from ..db import ensure_project, wipe_project
from ..settings import ProjectConfig
from ..writers.graph import attach_to_project, write_edges, write_nodes
from .base import Extractor, IndexerContext, IngestResult
from .gql import EXTRACTOR as GQL
from .routes import EXTRACTOR as ROUTES
from .scip_loader import ScipIndex
from .scip_runner import index_project as run_scip

EXTRACTORS: list[Extractor] = [
    GQL,
    ROUTES,
    # permissions / pages / e2e / scss / docs / types — plug in as they land.
]


def reindex(project: ProjectConfig) -> dict:
    """Full reindex: run scip-typescript, parse the .scip, run every
    extractor, persist to Memgraph. Returns a structured report."""
    started = time.monotonic()
    wipe_project(project.name)
    ensure_project(project.name)

    scip_started = time.monotonic()
    scip_result = run_scip(project.name, project.code_root)
    scip_ms = int((time.monotonic() - scip_started) * 1000)

    scip = ScipIndex.load(scip_result.scip_path)
    ctx = IndexerContext(project=project, scip=scip)

    runs: list[dict] = []
    touched_labels: set[str] = set()

    for ext in EXTRACTORS:
        ext_started = time.monotonic()
        try:
            result: IngestResult = ext.run(ctx)
            node_counts = {label: write_nodes(label, rows) for label, rows in result.nodes.items()}
            edge_count = write_edges(result.edges)
            touched_labels.update(result.nodes.keys())
            runs.append({
                "name": ext.NAME,
                "nodes": node_counts,
                "edges": edge_count,
                "duration_ms": int((time.monotonic() - ext_started) * 1000),
                "error": None,
            })
        except Exception as e:
            runs.append({
                "name": ext.NAME,
                "nodes": {},
                "edges": 0,
                "duration_ms": int((time.monotonic() - ext_started) * 1000),
                "error": f"{type(e).__name__}: {e}",
            })

    attach_to_project(project.name, sorted(touched_labels))

    return {
        "ok": all(r["error"] is None for r in runs),
        "project": project.name,
        "scip_duration_ms": scip_ms,
        "total_duration_ms": int((time.monotonic() - started) * 1000),
        "extractors": runs,
    }
