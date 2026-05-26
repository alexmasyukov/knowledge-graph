"""End-to-end testing read API.

Endpoints:
  GET /e2e/specs?project[&name][&limit]
  GET /e2e/testid/{value}?project
  GET /e2e/coverage?project
  GET /e2e/uncovered?project[&limit&group_by_file]

The data behind these comes from the `e2e` extractor — TestId nodes
from adsw `data-testid` attributes and E2eSpec nodes from Playwright
specs. Coverage is currently a static accounting (TestId vs E2eSpec
text match); a future locator-aware variant will collapse via
TestIdLoc when those files appear in the project.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session

router = APIRouter(prefix="/e2e", tags=["e2e"])


def _spec_text_contains(testid_value: str, spec_files: dict[str, str]) -> list[tuple[str, int]]:
    """Plain substring match — good enough for the coverage signal we
    need today. Returns list of (spec_file, line) hits."""
    hits: list[tuple[str, int]] = []
    for file, text in spec_files.items():
        if testid_value in text:
            line = text.count("\n", 0, text.find(testid_value)) + 1
            hits.append((file, line))
    return hits


@router.get("/specs")
async def list_specs(
    project: str,
    name: str | None = Query(default=None, description="substring filter on spec title"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    where = ["s.project = $project"]
    if name:
        where.append("toLower(s.name) CONTAINS toLower($name)")
    cypher = f"""
        MATCH (s:E2eSpec)
        WHERE {' AND '.join(where)}
        RETURN s.name AS name, s.file AS file, s.line AS line, s.kind AS kind
        ORDER BY s.file, s.line
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, name=name, limit=limit).data()
    return {"project": project, "count": len(rows), "specs": rows}


@router.get("/testid/{value:path}")
async def testid_info(value: str, project: str) -> dict:
    """For a given testid value, return its adsw source locations plus
    every spec file that references that literal value."""
    cypher_exact = """
        MATCH (t:TestId {project: $project, value: $value, pattern: false})
        OPTIONAL MATCH (t)-[r:OWNED_BY]->(f:File)
        RETURN collect(DISTINCT {file: f.path, line: r.line}) AS adsw_sources
    """
    cypher_pattern = """
        // Pattern match: any TestId whose prefix is a prefix of the requested value.
        MATCH (t:TestId {project: $project, pattern: true})
        WHERE $value STARTS WITH t.value
        OPTIONAL MATCH (t)-[r:OWNED_BY]->(f:File)
        RETURN collect(DISTINCT {file: f.path, line: r.line, prefix: t.value}) AS pattern_sources
    """
    with session() as s:
        exact = s.run(cypher_exact, project=project, value=value).single() or {}
        pat = s.run(cypher_pattern, project=project, value=value).single() or {}
        # Spec files — substring grep against their on-disk text.
        spec_files_rows = s.run(
            "MATCH (sp:E2eSpec {project: $project}) RETURN DISTINCT sp.file AS f",
            project=project,
        ).data()

    adsw_sources = [r for r in (exact.get("adsw_sources") or []) if r.get("file")]
    pattern_sources = [r for r in (pat.get("pattern_sources") or []) if r.get("file")]

    if not adsw_sources and not pattern_sources:
        raise HTTPException(404, f"testid not found: {value} (project={project})")

    # Read spec files lazily to count references.
    from ..settings import settings
    proj = settings.project(project)
    spec_refs: list[dict] = []
    if proj is not None:
        for row in spec_files_rows:
            spec_path = proj.repo_root / row["f"]
            try:
                txt = spec_path.read_text(encoding="utf-8")
            except Exception:
                continue
            if value in txt:
                line = txt.count("\n", 0, txt.find(value)) + 1
                spec_refs.append({"file": row["f"], "line": line})

    return {
        "project": project,
        "value": value,
        "adsw_sources": adsw_sources,
        "pattern_sources": pattern_sources,
        "specs": spec_refs,
    }


@router.get("/coverage")
async def coverage(project: str) -> dict:
    """Total adsw testids vs spec-referenced testids.

    We approximate "covered" by direct substring presence of the
    testid value in any spec file. That matches the master heuristic
    closely enough for the signal to remain meaningful — and is the
    only thing we can compute without TestIdLoc nodes."""
    from ..settings import settings
    proj = settings.project(project)
    with session() as s:
        testids = s.run(
            "MATCH (t:TestId {project: $project}) RETURN t.value AS v, t.pattern AS p",
            project=project,
        ).data()
        spec_files = [r["f"] for r in s.run(
            "MATCH (sp:E2eSpec {project: $project}) RETURN DISTINCT sp.file AS f",
            project=project,
        ).data()]

    spec_text = ""
    if proj is not None:
        for f in spec_files:
            try:
                spec_text += "\n" + (proj.repo_root / f).read_text(encoding="utf-8")
            except Exception:
                continue

    total = len(testids)
    covered = sum(1 for t in testids if not t["p"] and t["v"] and t["v"] in spec_text)
    return {
        "project": project,
        "adsw_total": total,
        "covered": covered,
        "uncovered": total - covered,
        "stale_e2e_only": 0,
    }


@router.get("/uncovered")
async def uncovered(
    project: str,
    limit: int = Query(default=500, ge=1, le=5000),
    group_by_file: bool = Query(default=False, description="bucket uncovered ids per source file"),
) -> dict:
    from ..settings import settings
    proj = settings.project(project)
    with session() as s:
        rows = s.run(
            """
            MATCH (t:TestId {project: $project})
            OPTIONAL MATCH (t)-[r:OWNED_BY]->(f:File)
            RETURN t.value AS value, t.pattern AS pattern, f.path AS file, r.line AS line
            """,
            project=project,
        ).data()
        spec_files = [r["f"] for r in s.run(
            "MATCH (sp:E2eSpec {project: $project}) RETURN DISTINCT sp.file AS f",
            project=project,
        ).data()]

    spec_text = ""
    if proj is not None:
        for f in spec_files:
            try:
                spec_text += "\n" + (proj.repo_root / f).read_text(encoding="utf-8")
            except Exception:
                continue

    items: list[dict] = []
    for r in rows:
        if not r["value"]:
            continue
        if not r["pattern"] and r["value"] in spec_text:
            continue
        items.append({
            "value": r["value"],
            "pattern": bool(r["pattern"]),
            "file": r["file"],
            "line": r["line"] or 0,
        })

    if not group_by_file:
        return {"project": project, "files": 0, "total": len(items), "items": items[:limit]}

    by_file: dict[str, list[dict]] = {}
    for it in items:
        by_file.setdefault(it["file"] or "<unknown>", []).append(it)
    groups = [
        {"file": f, "count": len(ids), "testids": ids}
        for f, ids in sorted(by_file.items(), key=lambda kv: -len(kv[1]))
    ][:limit]
    return {
        "project": project,
        "files": len(by_file),
        "total": len(items),
        "groups": groups,
    }
