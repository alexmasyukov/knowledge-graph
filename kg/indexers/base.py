"""Shared types for extractors that turn SCIP/filesystem signals into
graph rows.

Each extractor is a callable that takes an IndexerContext and returns
an IngestResult. Writers (kg.db.session) live downstream — extractors
only produce data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..settings import ProjectConfig
from .scip_loader import ScipIndex


@dataclass(slots=True)
class IndexerContext:
    project: ProjectConfig
    scip: ScipIndex


@dataclass(slots=True)
class IngestResult:
    """Generic shape returned by every extractor. Nodes are flat dicts
    keyed by label; `edges` describes relationships to MERGE later."""
    nodes: dict[str, list[dict]] = field(default_factory=dict)  # label → [{props}, ...]
    edges: list[dict] = field(default_factory=list)             # {source: {...}, type: str, target: {...}}
    stats: dict[str, int] = field(default_factory=dict)


class Extractor(Protocol):
    NAME: str

    def run(self, ctx: IndexerContext) -> IngestResult: ...
