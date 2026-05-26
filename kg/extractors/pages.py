"""Pages filesystem-convention extractor.

ADSW convention:
    src/pages/<domain>/<entity>/
        Entity.tsx       — form/detail
        Entities.tsx     — table/list
        types.ts
        helpers.ts
        constants.ts
        csvReport.ts     — optional
        toast-contracts.ts — optional
        Form/, hooks/    — sub-folders

Some pages skip the entity layer (e.g. home/Home.tsx).

Schema:
    (:Domain  {project, name})
    (:Page    {project, domain, entity, dir})
    (:Page)-[:IN_DOMAIN]->(:Domain)
    (:Page)-[:HAS_FILE]->(:File)
    (:Component)-[:BELONGS_TO]->(:Page)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import session
from ..settings import settings


# Files we classify by role
ROLE_BY_FILENAME = {
    "types.ts": "types",
    "helpers.ts": "helpers",
    "constants.ts": "constants",
    "csvReport.ts": "csv_report",
    "toast-contracts.ts": "toast_contracts",
}

IGNORED_DIRS = {"node_modules", "__tests__", "__mocks__", ".DS_Store"}


def _project_root(project: str) -> Path | None:
    cfg = next((p for p in settings.projects if p.name == project), None)
    return cfg.root if cfg else None


def _is_pascal(name: str) -> bool:
    return bool(name) and name[0].isupper()


def _scan_page_dir(d: Path, root: Path) -> dict[str, Any]:
    """Returns the file inventory of a page directory."""
    files = {"components": [], "by_role": {}, "subdirs": []}
    for p in d.iterdir():
        if p.name.startswith(".") or p.name in IGNORED_DIRS:
            continue
        if p.is_dir():
            files["subdirs"].append(p.name)
            continue
        rel = str(p.relative_to(root))
        if p.suffix in (".tsx", ".jsx") and _is_pascal(p.stem):
            files["components"].append({"name": p.stem, "file": rel})
        elif p.name in ROLE_BY_FILENAME:
            files["by_role"][ROLE_BY_FILENAME[p.name]] = rel
    return files


def _collect_pages(project: str) -> tuple[list[dict[str, Any]], list[str]]:
    root = _project_root(project)
    if root is None:
        return [], []

    pages_root = root / "src" / "pages"
    if not pages_root.is_dir():
        return [], []

    pages: list[dict[str, Any]] = []
    domains: set[str] = set()

    for domain_dir in sorted(pages_root.iterdir()):
        if not domain_dir.is_dir():
            # e.g. Page404.tsx at root — treat as a standalone page
            if domain_dir.suffix in (".tsx", ".jsx") and _is_pascal(domain_dir.stem):
                pages.append(
                    {
                        "domain": "_root",
                        "entity": domain_dir.stem,
                        "dir": str(domain_dir.parent.relative_to(root)),
                        "components": [{"name": domain_dir.stem, "file": str(domain_dir.relative_to(root))}],
                        "by_role": {},
                        "subdirs": [],
                    }
                )
                domains.add("_root")
            continue
        if domain_dir.name in IGNORED_DIRS or domain_dir.name.startswith("."):
            continue

        domain = domain_dir.name
        # Is this a flat domain (only PascalCase .tsx files, no entity folders)?
        sub_entries = [p for p in domain_dir.iterdir() if not p.name.startswith(".")]
        has_entity_subdirs = any(
            p.is_dir() and p.name not in {"common", "components", "hooks", "contexts", "constants"}
            and _is_pascal(p.name) is False  # entity folders are camelCase, not PascalCase
            for p in sub_entries
        )

        flat_files = _scan_page_dir(domain_dir, root)
        if flat_files["components"] or flat_files["by_role"]:
            # Flat domain page (e.g. home/Home.tsx)
            pages.append(
                {
                    "domain": domain,
                    "entity": None,
                    "dir": str(domain_dir.relative_to(root)),
                    **flat_files,
                }
            )
            domains.add(domain)

        # Walk entity sub-dirs
        for entity_dir in sorted(sub_entries):
            if not entity_dir.is_dir():
                continue
            if entity_dir.name in {"common", "components", "hooks", "contexts", "constants"}:
                # shared utilities for the domain — not a page
                continue
            inv = _scan_page_dir(entity_dir, root)
            if not inv["components"] and not inv["by_role"]:
                continue
            pages.append(
                {
                    "domain": domain,
                    "entity": entity_dir.name,
                    "dir": str(entity_dir.relative_to(root)),
                    **inv,
                }
            )
            domains.add(domain)

    return pages, sorted(domains)


def write_pages(project: str, pages: list[dict[str, Any]], domains: list[str]) -> dict[str, int]:
    with session() as s:
        s.run(
            """
            MATCH (n {project: $project})
            WHERE n:Page OR n:Domain
            DETACH DELETE n
            """,
            project=project,
        )

        if domains:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $names AS n
                MERGE (d:Domain {project: $project, name: n})
                MERGE (d)-[:IN_PROJECT]->(p)
                """,
                project=project,
                names=domains,
            )

        if pages:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $pages AS pg
                CREATE (page:Page {
                    project: $project,
                    domain:  pg.domain,
                    entity:  coalesce(pg.entity, ''),
                    dir:     pg.dir,
                    files:   pg.flat_files,
                    subdirs: pg.subdirs
                })
                MERGE (page)-[:IN_PROJECT]->(p)
                WITH page, pg
                MATCH (d:Domain {project: $project, name: pg.domain})
                MERGE (page)-[:IN_DOMAIN]->(d)
                """,
                project=project,
                pages=[
                    {
                        "domain": pg["domain"],
                        "entity": pg["entity"],
                        "dir": pg["dir"],
                        "flat_files": [c["file"] for c in pg["components"]]
                                       + list(pg["by_role"].values()),
                        "subdirs": pg["subdirs"],
                    }
                    for pg in pages
                ],
            )

        # Component → Page links (Component nodes were created in Phase 2)
        comp_to_page = []
        for pg in pages:
            for c in pg["components"]:
                comp_to_page.append(
                    {"component": c["name"], "domain": pg["domain"], "entity": pg["entity"] or ""}
                )
        if comp_to_page:
            s.run(
                """
                UNWIND $items AS i
                MATCH (co:Component {project: $project, name: i.component})
                MATCH (pg:Page {project: $project, domain: i.domain, entity: i.entity})
                MERGE (co)-[:BELONGS_TO]->(pg)
                """,
                project=project,
                items=comp_to_page,
            )

        # File nodes for each page file
        file_links = []
        for pg in pages:
            for c in pg["components"]:
                file_links.append(
                    {"path": c["file"], "domain": pg["domain"], "entity": pg["entity"] or ""}
                )
            for role, path in pg["by_role"].items():
                file_links.append(
                    {"path": path, "domain": pg["domain"], "entity": pg["entity"] or ""}
                )
        if file_links:
            s.run(
                """
                UNWIND $items AS i
                MERGE (f:File {project: $project, path: i.path})
                WITH f, i
                MATCH (pg:Page {project: $project, domain: i.domain, entity: i.entity})
                MERGE (pg)-[:HAS_FILE]->(f)
                """,
                project=project,
                items=file_links,
            )

    return {
        "pages": len(pages),
        "domains": len(domains),
    }


async def run_for_project(project: str) -> dict[str, Any]:
    pages, domains = _collect_pages(project)
    counts = write_pages(project, pages, domains)
    return {"project": project, "written": counts, "domains": domains}
