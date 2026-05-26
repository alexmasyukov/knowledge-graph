"""Project docs extractor (CLAUDE/ + root .md files).

Indexes markdown files into the graph so MCP tools can search through
project-specific docs without crawling the filesystem.

Schema:
    (:Doc {project, name, file, title, size, headings})
    (:Doc)-[:IN_PROJECT]->(Project)

`headings` is a JSON-encoded list of {level, text} for h1-h3.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..db import session
from ..settings import settings


# Folders inside project root to scan. CLAUDE_OTHER/ deliberately excluded.
DOC_FOLDERS: tuple[str, ...] = ("CLAUDE",)
ROOT_FILES: tuple[str, ...] = ("README.md", "CLAUDE.md", "MIGRATION_PLAN.md")


_HEADING_RX = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _parse_md(text: str) -> dict[str, Any]:
    """Pull title (first h1) and the list of h1-h3 headings out of markdown."""
    headings = [
        {"level": len(m.group(1)), "text": m.group(2)}
        for m in _HEADING_RX.finditer(text)
    ]
    title = headings[0]["text"] if headings and headings[0]["level"] == 1 else None
    return {"title": title, "headings": headings}


def _discover_project_root(project: str) -> Path | None:
    """The .env's PROJECT_<NAME>=<path> points at a sub-package (e.g. packages/adsw).
    Docs typically live two levels up at the repo root."""
    cfg = next((p for p in settings.projects if p.name == project), None)
    if cfg is None:
        return None
    root = cfg.root
    # Walk up looking for a CLAUDE/ folder or a README.md at the topmost git-controlled level
    candidates = [root, *root.parents]
    for c in candidates:
        if (c / "CLAUDE").is_dir() or any((c / f).exists() for f in ROOT_FILES):
            # stop at first ancestor that has any of our markers
            return c
    return root


def _collect_docs(project: str) -> list[dict[str, Any]]:
    root = _discover_project_root(project)
    if root is None:
        return []

    out: list[dict[str, Any]] = []
    for folder in DOC_FOLDERS:
        d = root / folder
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            meta = _parse_md(text)
            out.append(
                {
                    "name": p.stem,
                    "file": str(p.relative_to(root)),
                    "title": meta["title"],
                    "size": len(text),
                    "headings": meta["headings"],
                }
            )

    for fname in ROOT_FILES:
        p = root / fname
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = _parse_md(text)
        out.append(
            {
                "name": p.stem,
                "file": str(p.relative_to(root)),
                "title": meta["title"],
                "size": len(text),
                "headings": meta["headings"],
            }
        )

    return out


def write_docs(project: str, docs: list[dict[str, Any]]) -> dict[str, int]:
    with session() as s:
        s.run(
            """
            MATCH (d:Doc {project: $project})
            DETACH DELETE d
            """,
            project=project,
        )
        if docs:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $docs AS d
                CREATE (doc:Doc {
                    project:  $project,
                    name:     d.name,
                    file:     d.file,
                    title:    d.title,
                    size:     d.size,
                    headings: d.headings
                })
                MERGE (doc)-[:IN_PROJECT]->(p)
                """,
                project=project,
                docs=[
                    {**d, "headings": [f"{h['level']}|{h['text']}" for h in d["headings"]]}
                    for d in docs
                ],
            )
    return {"docs": len(docs)}


def read_doc(project: str, name: str) -> dict[str, Any] | None:
    """Return the full content of a doc by name (without ext)."""
    root = _discover_project_root(project)
    if root is None:
        return None
    # Search both CLAUDE/ and the root for <name>.md
    for folder in DOC_FOLDERS:
        p = root / folder / f"{name}.md"
        if p.is_file():
            return {
                "name": name,
                "file": str(p.relative_to(root)),
                "content": p.read_text(encoding="utf-8"),
            }
    for fname in ROOT_FILES:
        if Path(fname).stem.lower() == name.lower():
            p = root / fname
            if p.is_file():
                return {
                    "name": name,
                    "file": str(p.relative_to(root)),
                    "content": p.read_text(encoding="utf-8"),
                }
    return None


async def run_for_project(project: str) -> dict[str, Any]:
    docs = _collect_docs(project)
    counts = write_docs(project, docs)
    return {"project": project, "written": counts}
