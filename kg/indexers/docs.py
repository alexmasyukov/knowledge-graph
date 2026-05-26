"""Documentation extractor.

Picks up Markdown files we care about for context:

  - The project's top-level README.md and CLAUDE.md
  - Anything under a `CLAUDE/` directory at the repo root
  - Anything under `mcp/` and `docs/` directories at the repo root

Emits one Doc node per file:

  Doc (project, file, name, title, size, headings)

`title` is the first `#` heading, `headings` is the flat list of all
`#`/`##`/… heading texts in source order. Tree-sitter-markdown gives us
both reliably even when files mix code blocks and HTML.

`/docs/search` uses the headings + a small body sample for substring
matching; full-text search lives in the rag layer, not here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from .base import Extractor, IndexerContext, IngestResult
from .tree_sitter_util import parse, text, walk

# Where to look for markdown. Relative to repo_root; we walk these
# recursively but bail out on node_modules / build artifacts.
_DOC_ROOTS: Final = ("CLAUDE", "docs", "mcp")
_DOC_TOPLEVEL: Final = ("README.md", "CLAUDE.md")
_IGNORE_PARTS: Final = {"node_modules", "dist", "build", "test-results", "playwright-report"}


def _doc_name(rel_path: str) -> str:
    """A short, MCP-friendly identifier — the path stem with directory
    parts joined by '/'. e.g. 'CLAUDE/RAG_vs_KG_use_cases' for
    'CLAUDE/RAG_vs_KG_use_cases.md'."""
    p = Path(rel_path)
    parts = list(p.parts)
    if parts and parts[-1].lower().endswith(".md"):
        parts[-1] = parts[-1][:-3]
    return "/".join(parts)


def _scan_markdown(path: Path) -> tuple[str | None, list[str]]:
    """Return (title, headings)."""
    try:
        tree, src = parse("markdown", path)
    except Exception:
        return None, []
    title: str | None = None
    headings: list[str] = []
    for node in walk(tree.root_node):
        if node.type not in {"atx_heading", "setext_heading"}:
            continue
        # The first `inline` child carries the text.
        head_text = ""
        for c in node.named_children:
            if c.type == "inline":
                head_text = text(c, src).strip()
                break
        if not head_text:
            continue
        if title is None:
            title = head_text
        headings.append(head_text)
    return title, headings


def _candidate_files(repo_root: Path) -> list[Path]:
    found: list[Path] = []
    for name in _DOC_TOPLEVEL:
        p = repo_root / name
        if p.is_file():
            found.append(p)
    for sub in _DOC_ROOTS:
        d = repo_root / sub
        if not d.is_dir():
            continue
        for path in d.rglob("*.md"):
            if any(part in _IGNORE_PARTS for part in path.parts):
                continue
            found.append(path)
    return sorted(set(found))


class DocsExtractor:
    NAME = "docs"

    def run(self, ctx: IndexerContext) -> IngestResult:
        repo_root = ctx.project.repo_root
        files = _candidate_files(repo_root)
        if not files:
            return IngestResult(stats={"docs": 0})

        docs: list[dict] = []
        for path in files:
            rel = str(path.relative_to(repo_root))
            title, headings = _scan_markdown(path)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            docs.append({
                "project": ctx.project.name,
                "file": rel,
                "name": _doc_name(rel),
                "title": title,
                "size": size,
                "headings": headings,
            })

        return IngestResult(
            nodes={"Doc": docs},
            edges=[],
            stats={"docs": len(docs)},
        )


EXTRACTOR: Extractor = DocsExtractor()  # type: ignore[assignment]
