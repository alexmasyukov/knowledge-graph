"""Domain extractors. Each module conforms to the Extractor protocol:

    NAME    — str, key used in reindex result payload
    LABELS  — tuple[str, ...] of Neo4j node labels this extractor owns
              (used by wipe_labels before write)
    async run(project: str) -> dict
            — orchestrates fetch + write, returns counts/stats

Order in ALL matters. gql must come first (routes links Component to
GqlHook/GqlOperation by known names); the rest is independent.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from . import docs, e2e, gql, pages, permissions, routes, scss


@runtime_checkable
class Extractor(Protocol):
    """Shape every domain extractor module must satisfy.

    Implemented as a module-level Protocol so each extractor stays a
    plain module (no class boilerplate) but `isinstance(mod, Extractor)`
    works at runtime and static type checkers flag a module that
    forgot to define NAME / LABELS / run.
    """

    NAME: str
    LABELS: tuple[str, ...]

    async def run(self, project: str) -> dict[str, Any]: ...


ALL: list[Extractor] = [gql, routes, permissions, pages, docs, e2e, scss]


# Sanity check at import time — fail fast if a new extractor doesn't
# expose the expected attributes. Cheap, runs once per process.
for _ext in ALL:
    assert isinstance(_ext.NAME, str) and _ext.NAME, f"{_ext.__name__}: NAME is required"
    assert isinstance(_ext.LABELS, tuple), f"{_ext.__name__}: LABELS must be a tuple"
    assert callable(getattr(_ext, "run", None)), f"{_ext.__name__}: missing async run()"
