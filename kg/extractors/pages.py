"""Pages filesystem-convention extractor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import session, wipe_labels
from ..settings import settings


NAME = "pages"
LABELS = ("Page", "Domain")


ROLE_BY_FILENAME = {
    "types.ts": "types",
    "helpers.ts": "helpers",
    "constants.ts": "constants",
    "csvReport.ts": "csv_report",
    "toast-contracts.ts": "toast_contracts",
}

IGNORED_DIRS = {"node_modules", "__tests__", "__mocks__", ".DS_Store"}


def _is_pascal(name: str) -> bool:
    return bool(name) and name[0].isupper()


def _scan_page_dir(d: Path, root: Path) -> dict[str, Any]:
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
    cfg = settings.project(project)
    if cfg is None:
        return [], []
    root = cfg.code_root
    pages_root = root / "src" / "pages"
    if not pages_root.is_dir():
        return [], []

    pages: list[dict[str, Any]] = []
    domains: set[str] = set()

    for domain_dir in sorted(pages_root.iterdir()):
        if not domain_dir.is_dir():
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
        sub_entries = [p for p in domain_dir.iterdir() if not p.name.startswith(".")]

        flat_files = _scan_page_dir(domain_dir, root)
        if flat_files["components"] or flat_files["by_role"]:
            pages.append(
                {
                    "domain": domain,
                    "entity": None,
                    "dir": str(domain_dir.relative_to(root)),
                    **flat_files,
                }
            )
            domains.add(domain)

        for entity_dir in sorted(sub_entries):
            if not entity_dir.is_dir():
                continue
            if entity_dir.name in {"common", "components", "hooks", "contexts", "constants"}:
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


def _write(project: str, pages: list[dict[str, Any]], domains: list[str]) -> dict[str, int]:
    wipe_labels(project, list(LABELS))

    with session() as s:
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

    return {"pages": len(pages), "domains": len(domains)}


async def run(project: str) -> dict[str, Any]:
    pages, domains = _collect_pages(project)
    counts = _write(project, pages, domains)
    return {"written": counts, "domains": domains}
