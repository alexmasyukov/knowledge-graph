"""Shared types for project-convention rules.

Conventions reuse the Extractor protocol from kg.indexers.base — same
shape, same writer pipeline. The distinction is conceptual:

  - Extractors record structural facts (a Route exists at path P; a hook
    file exports useX; a class Y inherits from Z).
  - Conventions encode project semantics (a JSX wrapper means data flow
    follows it; a component matching pattern X has implicit dependencies).

Adding a new convention is one file under kg/indexers/conventions/ plus
one entry in CONVENTIONS list in kg.indexers.ingest.
"""

from __future__ import annotations

from ..base import Extractor, IndexerContext, IngestResult

__all__ = ["Convention", "IndexerContext", "IngestResult"]

# Conventions are structurally just Extractors — same Protocol.
Convention = Extractor
