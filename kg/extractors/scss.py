"""SCSS modules extractor."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..db import session, wipe_labels
from ..settings import settings


NAME = "scss"
LABELS = ("ScssModule",)


_CLASS_RX = re.compile(r"^\s*\.([A-Za-z_][A-Za-z0-9_-]*)\b", re.MULTILINE)
_IMPORT_SCSS_RX = re.compile(
    r"""import\s+\w+\s+from\s+['"](?P<spec>[^'"]*\.module\.scss)['"]""",
)


def _classes_in(text: str) -> list[str]:
    return sorted({m.group(1) for m in _CLASS_RX.finditer(text)})


def _collect_modules(project_root: Path) -> list[dict[str, Any]]:
    src = project_root / "src"
    if not src.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(src.rglob("*.module.scss")):
        if "node_modules" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        out.append({
            "file": str(p.relative_to(project_root)),
            "classes": _classes_in(text),
        })
    return out


def _collect_imports(project_root: Path) -> list[dict[str, Any]]:
    src = project_root / "src"
    if not src.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for ext in ("*.tsx", "*.ts"):
        for p in src.rglob(ext):
            if "node_modules" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in _IMPORT_SCSS_RX.finditer(text):
                spec = m.group("spec")
                target = (p.parent / spec).resolve()
                if not target.is_file():
                    continue
                try:
                    rel_target = target.relative_to(project_root)
                except ValueError:
                    continue
                out.append({
                    "consumer": str(p.relative_to(project_root)),
                    "module_file": str(rel_target),
                })
    return out


def _write(project: str, modules: list[dict[str, Any]], imports: list[dict[str, Any]]) -> dict[str, int]:
    wipe_labels(project, list(LABELS))
    with session() as s:
        if modules:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $items AS m
                CREATE (sm:ScssModule {
                    project: $project,
                    file:    m.file,
                    classes: m.classes
                })
                MERGE (sm)-[:IN_PROJECT]->(p)
                """,
                project=project,
                items=modules,
            )
        if imports:
            s.run(
                """
                UNWIND $items AS i
                MERGE (f:File {project: $project, path: i.consumer})
                WITH f, i
                MATCH (sm:ScssModule {project: $project, file: i.module_file})
                MERGE (f)-[:USES_SCSS]->(sm)
                """,
                project=project,
                items=imports,
            )
    return {"modules": len(modules), "imports": len(imports)}


async def run(project: str) -> dict[str, Any]:
    cfg = settings.project(project)
    if cfg is None:
        return {"skipped": "no settings"}
    modules = _collect_modules(cfg.code_root)
    imports = _collect_imports(cfg.code_root)
    counts = _write(project, modules, imports)
    return {"written": counts}
