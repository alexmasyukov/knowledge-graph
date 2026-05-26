"""SCSS modules extractor.

For every `.module.scss` file under the project's `src/`:

  ScssModule (project, file)
  ScssClass  (project, module, name)
    — only top-level class selectors are stored; nested selectors,
      element selectors (e.g. `aside { }`) and pseudo-selectors are
      ignored.

Edges:
  ScssModule -[:DEFINES]-> ScssClass
  File       -[:CONSUMES]-> ScssModule    when a TSX/TS file imports it

The consumer pass walks every TSX/TS file and looks for
`from '<...>.module.scss'` imports. The resolved file path is computed
relative to the importing file, which mirrors how the bundler sees it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from .base import Extractor, IndexerContext, IngestResult
from .tree_sitter_util import parse, text, walk

_IMPORT_SCSS_RX: Final = re.compile(
    r"""import\s+\w+\s+from\s+['"]([^'"]+\.module\.scss)['"]"""
)


def _scan_module(path: Path) -> list[str]:
    """Top-level class selectors. tree-sitter-scss exposes them as
    `class_selector` nodes inside `rule_set` nodes at the root level."""
    try:
        tree, src = parse("scss", path)
    except Exception:
        return []
    out: set[str] = set()
    for rs in tree.root_node.children:
        if rs.type != "rule_set":
            continue
        sel_block = rs.named_children[0] if rs.named_child_count else None
        if sel_block is None:
            continue
        for n in walk(sel_block):
            if n.type == "class_selector":
                raw = text(n, src)
                if raw.startswith("."):
                    out.add(raw[1:])
    return sorted(out)


def _consumers(code_root: Path, repo_root: Path) -> dict[str, list[str]]:
    """Map repo-relative scss-module path → list of repo-relative TSX/TS
    files that import it."""
    out: dict[str, list[str]] = {}
    src_dir = code_root / "src"
    if not src_dir.is_dir():
        return out
    for ext in ("*.tsx", "*.ts"):
        for path in src_dir.rglob(ext):
            if "node_modules" in path.parts:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in _IMPORT_SCSS_RX.finditer(content):
                imp = m.group(1)
                if imp.startswith("."):
                    resolved = (path.parent / imp).resolve()
                else:
                    resolved = (code_root / imp).resolve()
                try:
                    rel_mod = str(resolved.relative_to(code_root))
                except ValueError:
                    continue
                rel_consumer = str(path.relative_to(code_root))
                out.setdefault(rel_mod, []).append(rel_consumer)
    return out


class ScssExtractor:
    NAME = "scss"

    def run(self, ctx: IndexerContext) -> IngestResult:
        src_dir = ctx.project.code_root / "src"
        if not src_dir.is_dir():
            return IngestResult(stats={"modules": 0, "skipped": 1})

        modules: list[dict] = []
        classes: list[dict] = []
        edges: list[dict] = []
        files_seen: set[str] = set()
        consumers_map = _consumers(ctx.project.code_root, ctx.project.repo_root)

        for path in sorted(src_dir.rglob("*.module.scss")):
            rel = str(path.relative_to(ctx.project.code_root))
            class_names = _scan_module(path)
            modules.append({
                "project": ctx.project.name,
                "file": rel,
                "classes": class_names,
            })
            for c in class_names:
                classes.append({
                    "project": ctx.project.name,
                    "module": rel,
                    "name": c,
                })
                edges.append({
                    "source": {"label": "ScssModule", "key": {"project": ctx.project.name, "file": rel}},
                    "type": "DEFINES",
                    "target": {"label": "ScssClass", "key": {"project": ctx.project.name, "module": rel, "name": c}},
                })
            for consumer in consumers_map.get(rel, []):
                files_seen.add(consumer)
                edges.append({
                    "source": {"label": "File", "key": {"project": ctx.project.name, "path": consumer}},
                    "type": "CONSUMES",
                    "target": {"label": "ScssModule", "key": {"project": ctx.project.name, "file": rel}},
                })

        file_nodes = [{"project": ctx.project.name, "path": p} for p in sorted(files_seen)]

        return IngestResult(
            nodes={
                "ScssModule": modules,
                "ScssClass": classes,
                "File": file_nodes,
            },
            edges=edges,
            stats={
                "modules": len(modules),
                "classes": len(classes),
                "consumers": len(file_nodes),
                "edges": len(edges),
            },
        )


EXTRACTOR: Extractor = ScssExtractor()  # type: ignore[assignment]
