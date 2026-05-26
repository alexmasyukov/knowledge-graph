"""Domain extractors. Each module exports:

    NAME    — str, key used in reindex result payload
    LABELS  — tuple[str, ...] of Neo4j node labels this extractor owns
              (used by wipe_labels before write)
    run(project: str) -> dict
            — async, orchestrates fetch + write, returns counts/stats

Order in ALL is the order they run. gql must come before routes (routes
links Component → GqlHook/GqlOperation by known names).
"""
from __future__ import annotations

from . import gql, routes, permissions, pages, docs, e2e, scss


ALL = [gql, routes, permissions, pages, docs, e2e, scss]
