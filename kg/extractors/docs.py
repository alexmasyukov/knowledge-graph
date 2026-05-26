"""Project docs extractor (CLAUDE/ + root .md files)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..db import session, wipe_labels
from ..settings import settings


NAME = "docs"
LABELS = ("Doc",)


# Folders inside repo root to scan. CLAUDE_OTHER/ deliberately excluded.
DOC_FOLDERS: tuple[str, ...] = ("CLAUDE",)
ROOT_FILES: tuple[str, ...] = ("README.md", "CLAUDE.md")


_HEADING_RX = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _parse_md(text: str) -> dict[str, Any]:
    headings = [
        {"level": len(m.group(1)), "text": m.group(2)}
        for m in _HEADING_RX.finditer(text)
    ]
    title = headings[0]["text"] if headings and headings[0]["level"] == 1 else None
    return {"title": title, "headings": headings}


def _repo_root(project: str) -> Path | None:
    cfg = settings.project(project)
    return cfg.repo_root if cfg else None


def _collect_docs(project: str) -> list[dict[str, Any]]:
    root = _repo_root(project)
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
            out.append({
                "name": p.stem,
                "file": str(p.relative_to(root)),
                "title": meta["title"],
                "size": len(text),
                "headings": meta["headings"],
            })

    for fname in ROOT_FILES:
        p = root / fname
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = _parse_md(text)
        out.append({
            "name": p.stem,
            "file": str(p.relative_to(root)),
            "title": meta["title"],
            "size": len(text),
            "headings": meta["headings"],
        })

    return out


def read_doc(project: str, name: str) -> dict[str, Any] | None:
    root = _repo_root(project)
    if root is None:
        return None
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


def _write(project: str, docs: list[dict[str, Any]]) -> dict[str, int]:
    wipe_labels(project, list(LABELS))
    if docs:
        with session() as s:
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


async def run(project: str) -> dict[str, Any]:
    docs = _collect_docs(project)
    counts = _write(project, docs)
    return {"written": counts}
