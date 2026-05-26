"""Pages extractor.

A "page" is one entity directory under `src/pages/<domain>/<entity>/`.
That layout convention is enforced by the adsw codebase: every routed
view lives in such a directory, with its top-level TSX files (the
React components the router renders) and a handful of sub-directories
(`hooks/`, `Form/`, `components/`, …).

Emits:
  Page (project, domain, entity, dir, files, subdirs)
  Component (project, file, name)          — keyed by file path, so
                                             routes' RENDERS edges
                                             merge onto the same node.

Edges:
  Component -[:BELONGS_TO]-> Page          when the component file lives
                                            anywhere under the page dir
                                            (catches sub-component nesting).

Top-level files directly under `src/pages/` (e.g. `Page404.tsx`) are
skipped — they have no entity directory and don't fit the model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from .base import Extractor, IndexerContext, IngestResult

PAGES_ROOT: Final = "src/pages"

# A TSX file at the top of an entity directory whose name starts with an
# uppercase letter is considered a routed component. Subdirectories are
# tracked separately.
_COMPONENT_NAME_RX = re.compile(r"^[A-Z][A-Za-z0-9_]*$")

# Filesystem junk we never want to surface in the graph.
_IGNORE_NAMES = {".DS_Store", "Thumbs.db", ".gitkeep"}


def _is_component_file(file: Path) -> bool:
    return (
        file.is_file()
        and file.suffix in {".tsx", ".ts"}
        and bool(_COMPONENT_NAME_RX.match(file.stem))
    )


class PagesExtractor:
    NAME = "pages"

    def run(self, ctx: IndexerContext) -> IngestResult:
        pages_dir = ctx.project.code_root / PAGES_ROOT
        if not pages_dir.is_dir():
            return IngestResult(stats={"pages": 0, "skipped": 1})

        pages: list[dict] = []
        components: list[dict] = []
        edges: list[dict] = []
        comp_seen: set[tuple[str, str]] = set()  # (file, name)

        for domain_dir in sorted(pages_dir.iterdir()):
            if not domain_dir.is_dir():
                continue
            domain = domain_dir.name
            for entity_dir in sorted(domain_dir.iterdir()):
                if not entity_dir.is_dir():
                    continue
                entity = entity_dir.name

                rel_dir = str(entity_dir.relative_to(ctx.project.code_root)) + "/"
                files: list[str] = []
                subdirs: list[str] = []
                # Top-level files only — sub-directories listed separately.
                for child in sorted(entity_dir.iterdir()):
                    if child.name in _IGNORE_NAMES:
                        continue
                    rel_child = str(child.relative_to(ctx.project.code_root))
                    if child.is_dir():
                        subdirs.append(rel_child + "/")
                    else:
                        files.append(rel_child)
                        if _is_component_file(child):
                            name = child.stem
                            key = (rel_child, name)
                            if key in comp_seen:
                                continue
                            comp_seen.add(key)
                            components.append({
                                "project": ctx.project.name,
                                "name": name,
                                "file": rel_child,
                                "line": 1,
                                "exported": True,
                                "router_local": None,
                            })

                pages.append({
                    "project": ctx.project.name,
                    "domain": domain,
                    "entity": entity,
                    "dir": rel_dir,
                    "files": files,
                    "subdirs": subdirs,
                })

        # Component -[:BELONGS_TO]-> Page  via file-path prefix.
        # Iterate every Component already created (just above) and
        # match it to the deepest page whose dir is a prefix of its
        # file. Cheaper to precompute pages-by-prefix.
        pages_by_dir = sorted(((p["dir"], p) for p in pages), key=lambda t: -len(t[0]))
        for comp in components:
            for prefix, page in pages_by_dir:
                if comp["file"].startswith(prefix):
                    edges.append({
                        "source": {
                            "label": "Component",
                            "key": {
                                "project": ctx.project.name,
                                "file": comp["file"],
                                "name": comp["name"],
                            },
                        },
                        "type": "BELONGS_TO",
                        "target": {
                            "label": "Page",
                            "key": {
                                "project": ctx.project.name,
                                "domain": page["domain"],
                                "entity": page["entity"],
                            },
                        },
                    })
                    break

        return IngestResult(
            nodes={
                "Page": pages,
                "Component": components,
            },
            edges=edges,
            stats={
                "pages": len(pages),
                "components": len(components),
                "edges": len(edges),
            },
        )


EXTRACTOR: Extractor = PagesExtractor()  # type: ignore[assignment]
