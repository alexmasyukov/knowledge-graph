"""SCSS modules extractor.

Walks src/**/*.module.scss for class declarations, then scans .tsx/.ts
sources for `import s from './X.module.scss'` to link components to
their scss modules.

Schema:
    (:ScssModule {project, file, classes})
    (:ScssModule)-[:IN_PROJECT]->(Project)
    (:File)-[:USES_SCSS]->(:ScssModule)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..db import session
from ..settings import settings


_CLASS_RX = re.compile(r"^\s*\.([A-Za-z_][A-Za-z0-9_-]*)\b", re.MULTILINE)
_IMPORT_SCSS_RX = re.compile(
    r"""import\s+\w+\s+from\s+['"](?P<spec>[^'"]*\.module\.scss)['"]""",
)


def _project_root(project: str) -> Path | None:
    cfg = next((p for p in settings.projects if p.name == project), None)
    return cfg.root if cfg else None


def _classes_in(text: str) -> list[str]:
    # Collect unique class names; nested selectors and modifiers stay top-level only.
    classes: set[str] = set()
    for m in _CLASS_RX.finditer(text):
        classes.add(m.group(1))
    return sorted(classes)


def _collect_modules(project_root: Path) -> list[dict[str, Any]]:
    src = project_root / "src"
    out: list[dict[str, Any]] = []
    if not src.is_dir():
        return out
    for p in sorted(src.rglob("*.module.scss")):
        if "node_modules" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        out.append(
            {
                "file": str(p.relative_to(project_root)),
                "classes": _classes_in(text),
            }
        )
    return out


def _collect_imports(project_root: Path) -> list[dict[str, Any]]:
    src = project_root / "src"
    out: list[dict[str, Any]] = []
    if not src.is_dir():
        return out
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
                # Resolve relative
                target = (p.parent / spec).resolve()
                if not target.is_file():
                    continue
                try:
                    rel_target = target.relative_to(project_root)
                except ValueError:
                    continue
                out.append(
                    {
                        "consumer": str(p.relative_to(project_root)),
                        "module_file": str(rel_target),
                    }
                )
    return out


def write_scss(project: str, modules: list[dict[str, Any]], imports: list[dict[str, Any]]) -> dict[str, int]:
    with session() as s:
        s.run(
            """
            MATCH (m:ScssModule {project: $project})
            DETACH DELETE m
            """,
            project=project,
        )
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


async def run_for_project(project: str) -> dict[str, Any]:
    root = _project_root(project)
    if root is None:
        return {"project": project, "skipped": "no settings"}
    modules = _collect_modules(root)
    imports = _collect_imports(root)
    counts = write_scss(project, modules, imports)
    return {"project": project, "written": counts}
