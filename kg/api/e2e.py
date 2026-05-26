"""E2E endpoints (Phase 4)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ._models import (
    CoverageResponse,
    SpecsListResponse,
    TestIdInfoResponse,
    TestIdSearchResponse,
)

router = APIRouter(prefix="/e2e", tags=["e2e"])


@router.get("/specs", response_model=SpecsListResponse)
async def list_specs(
    project: str,
    name_substr: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    # Match against both test title AND filename — Cyrillic test titles
    # often don't share english slugs with the spec file (e.g. "бронь" vs
    # create-update-booking.spec.ts), so filename matching is essential.
    cypher = """
        MATCH (s:E2eSpec {project: $project})
        WHERE $q IS NULL
           OR toLower(s.name) CONTAINS toLower($q)
           OR toLower(s.file) CONTAINS toLower($q)
        OPTIONAL MATCH (s)-[:USES_POM]->(p:PageObject)
        WITH s, collect(DISTINCT p.name) AS poms
        RETURN s.name AS name, s.file AS file, s.line AS line, s.kind AS kind, poms
        ORDER BY s.file, s.line
        LIMIT $limit
    """
    with session() as s_:
        rows = s_.run(cypher, project=project, q=name_substr, limit=limit).data()
    return {"project": project, "count": len(rows), "specs": rows}


@router.get("/spec")
async def spec_info(project: str, file: str, line: int) -> dict:
    cypher = """
        MATCH (s:E2eSpec {project: $project, file: $file, line: $line})
        OPTIONAL MATCH (s)-[:USES_POM]->(po:PageObject)
        OPTIONAL MATCH (po)-[:DEFINES_LOC]->(l:TestIdLoc)
        OPTIONAL MATCH (l)-[:RESOLVES_TO]->(t:TestId)
        WITH s, po,
             collect(DISTINCT l.name) AS locators,
             collect(DISTINCT t.value) AS testids
        RETURN
          {name: s.name, file: s.file, line: s.line, kind: s.kind} AS spec,
          collect(DISTINCT {name: po.name, file: po.file, locators: locators, testids: testids}) AS page_objects
    """
    with session() as s_:
        rec = s_.run(cypher, project=project, file=file, line=line).single()
    if not rec or rec["spec"] is None:
        raise HTTPException(404, f"spec not found: {file}:{line} (project={project})")
    return {"project": project, **rec.data()}


@router.get("/testid/{value:path}", response_model=TestIdInfoResponse)
async def testid_info(value: str, project: str) -> dict:
    # Three passes — cleaner than juggling literal-direct and pattern-prefix
    # locator lookups inside one OPTIONAL MATCH tree, which mishandles the
    # nested collect()/WITH bookkeeping. The same `value` may resolve to
    # several TestId nodes (e.g. literal `sidebar-booking` + pattern
    # `sidebar-`); we union them.
    q_nodes = """
        MATCH (t:TestId {project: $project})
        WHERE (t.pattern = false AND t.value = $value)
           OR (t.pattern = true  AND size(t.value) > 0 AND $value STARTS WITH t.value)
        RETURN t.value AS v, t.pattern AS p
    """
    q_sources = """
        MATCH (t:TestId {project: $project, value: $v, pattern: $p})
        MATCH (f:File)-[h:HAS_TESTID]->(t)
        RETURN DISTINCT f.path AS file, h.line AS line
    """
    q_locs_literal = """
        MATCH (t:TestId {project: $project, value: $v, pattern: false})
        MATCH (l:TestIdLoc)-[:RESOLVES_TO]->(t)
        OPTIONAL MATCH (po:PageObject)-[:DEFINES_LOC]->(l)
        OPTIONAL MATCH (sp:E2eSpec)-[:USES_POM]->(po)
        RETURN l.name AS name, l.file AS file, l.line AS line,
               collect(DISTINCT po.name) AS poms,
               collect(DISTINCT {name: sp.name, file: sp.file, line: sp.line}) AS specs
    """
    # For pattern testids we go through literal TestId nodes whose value
    # starts with the pattern's prefix — those carry RESOLVES_TO edges
    # from locators. TestIdLoc.value itself is the full CSS selector
    # (e.g. `[data-testid="sidebar-booking"]`), not the testid string.
    q_locs_pattern = """
        MATCH (t:TestId {project: $project, value: $v, pattern: true})
        MATCH (lit:TestId {project: $project, pattern: false})
          WHERE size(t.value) > 0 AND lit.value STARTS WITH t.value
        MATCH (l:TestIdLoc)-[:RESOLVES_TO]->(lit)
        OPTIONAL MATCH (po:PageObject)-[:DEFINES_LOC]->(l)
        OPTIONAL MATCH (sp:E2eSpec)-[:USES_POM]->(po)
        RETURN l.name AS name, l.file AS file, l.line AS line,
               collect(DISTINCT po.name) AS poms,
               collect(DISTINCT {name: sp.name, file: sp.file, line: sp.line}) AS specs
    """

    sources: list[dict] = []
    locators: list[dict] = []
    poms: set[str] = set()
    specs_by_key: dict[tuple, dict] = {}

    with session() as s_:
        nodes = s_.run(q_nodes, project=project, value=value).data()
        if not nodes:
            raise HTTPException(404, f"testid not found: {value} (project={project})")
        for n in nodes:
            for r in s_.run(q_sources, project=project, v=n["v"], p=n["p"]).data():
                sources.append({"file": r["file"], "line": r["line"]})
            q = q_locs_literal if not n["p"] else q_locs_pattern
            for r in s_.run(q, project=project, v=n["v"]).data():
                locators.append({"name": r["name"], "file": r["file"], "line": r["line"]})
                for pn in r["poms"] or []:
                    if pn:
                        poms.add(pn)
                for sp in r["specs"] or []:
                    if sp.get("name"):
                        specs_by_key[(sp["file"], sp["line"])] = sp

    seen_src: set[tuple] = set()
    sources = [
        src for src in sources
        if (src["file"], src["line"]) not in seen_src
        and not seen_src.add((src["file"], src["line"]))
    ]
    seen_loc: set[tuple] = set()
    locators = [
        loc for loc in locators
        if (loc["name"], loc["file"]) not in seen_loc
        and not seen_loc.add((loc["name"], loc["file"]))
    ]

    return {
        "project": project,
        "value": value,
        "adsw_sources": sources,
        "locators": locators,
        "page_objects": sorted(poms),
        "specs": list(specs_by_key.values()),
    }


@router.get("/coverage", response_model=CoverageResponse)
async def coverage(project: str) -> dict:
    """How many testids defined in adsw source are referenced by e2e locators.

    A testid is considered covered if a locator references it directly
    (literal) OR if it is a template pattern and at least one locator
    value starts with its static prefix (e.g. `sidebar-` matches the
    `sidebar-booking` locator).

    Only adsw-defined testids count toward the denominator; locator-only
    values (referenced in e2e but not present in app code as either a
    literal or a matching pattern prefix) are reported separately."""
    cypher_main = """
        MATCH (t:TestId {project: $project})
        OPTIONAL MATCH (f:File)-[:HAS_TESTID]->(t)
        OPTIONAL MATCH (l_lit:TestIdLoc)-[:RESOLVES_TO]->(t)
        OPTIONAL MATCH (l_pat:TestIdLoc {project: $project})
          WHERE t.pattern = true AND size(t.value) > 0
            AND l_pat.value STARTS WITH t.value
        WITH t,
             count(DISTINCT f)     AS in_adsw,
             count(DISTINCT l_lit) AS in_lit,
             count(DISTINCT l_pat) AS in_pat
        WITH in_adsw, (in_lit + in_pat) AS in_loc
        RETURN
          sum(CASE WHEN in_adsw > 0 THEN 1 ELSE 0 END) AS adsw_total,
          sum(CASE WHEN in_adsw > 0 AND in_loc > 0 THEN 1 ELSE 0 END) AS covered,
          sum(CASE WHEN in_adsw > 0 AND in_loc = 0 THEN 1 ELSE 0 END) AS uncovered
    """
    # Stale = literal TestId nodes referenced by e2e (no adsw HAS_TESTID),
    # and not covered by any pattern node whose prefix matches.
    cypher_stale = """
        MATCH (t:TestId {project: $project, pattern: false})
        OPTIONAL MATCH (:File)-[:HAS_TESTID]->(t)
          WITH t, count(*) AS in_adsw
        WHERE in_adsw = 0
        OPTIONAL MATCH (pt:TestId {project: $project, pattern: true})
          WHERE size(pt.value) > 0 AND t.value STARTS WITH pt.value
        WITH t, count(pt) AS pattern_hits
        WHERE pattern_hits = 0
        RETURN count(DISTINCT t) AS stale_e2e_only
    """
    with session() as s_:
        m = s_.run(cypher_main, project=project).single()
        st = s_.run(cypher_stale, project=project).single()
    data = {"adsw_total": 0, "covered": 0, "uncovered": 0, "stale_e2e_only": 0}
    if m:
        data.update({k: m[k] or 0 for k in ("adsw_total", "covered", "uncovered")})
    if st:
        data["stale_e2e_only"] = st["stale_e2e_only"] or 0
    return {"project": project, **data}


@router.get("/uncovered")
async def uncovered_testids(
    project: str,
    limit: int = Query(default=200, ge=1, le=2000),
    group_by_file: bool = Query(default=True),
) -> dict:
    """Adsw testids that no locator hits — either directly or via a pattern.

    With group_by_file=True (default) the result is bucketed by source
    file and sorted by descending uncovered-count, which is how e2e
    planners tend to look at it ('this form has 12 untestable inputs').
    Toggle off to get a flat list."""
    cypher = """
        MATCH (t:TestId {project: $project})
        MATCH (f:File)-[h:HAS_TESTID]->(t)
        OPTIONAL MATCH (l_lit:TestIdLoc)-[:RESOLVES_TO]->(t)
        OPTIONAL MATCH (l_pat:TestIdLoc {project: $project})
          WHERE t.pattern = true AND size(t.value) > 0
            AND l_pat.value STARTS WITH t.value
        WITH t, f, h, count(DISTINCT l_lit) + count(DISTINCT l_pat) AS in_loc
        WHERE in_loc = 0
        RETURN t.value AS value, t.pattern AS pattern,
               f.path AS file, h.line AS line
        ORDER BY f.path, h.line
        LIMIT $limit
    """
    with session() as s_:
        rows = s_.run(cypher, project=project, limit=limit).data()

    if not group_by_file:
        return {"project": project, "count": len(rows), "uncovered": rows}

    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(
            {"value": r["value"], "line": r["line"], "pattern": r["pattern"]}
        )
    groups = sorted(
        ({"file": f, "count": len(items), "testids": items} for f, items in by_file.items()),
        key=lambda g: (-g["count"], g["file"]),
    )
    return {"project": project, "files": len(groups), "total": len(rows), "groups": groups}


@router.get("/testid-search", response_model=TestIdSearchResponse)
async def testid_search(
    project: str,
    q: str = Query(..., min_length=2, description="prefix or substring of testid value"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Substring search across all testid values (handles template patterns)."""
    cypher = """
        MATCH (t:TestId {project: $project})
        WHERE toLower(t.value) CONTAINS toLower($q)
        OPTIONAL MATCH (:TestIdLoc)-[:RESOLVES_TO]->(t)
        WITH t, count(*) AS via_locator
        RETURN t.value AS value, via_locator > 0 AS has_locator
        ORDER BY t.value
        LIMIT $limit
    """
    with session() as s_:
        rows = s_.run(cypher, project=project, q=q, limit=limit).data()
    return {"project": project, "query": q, "count": len(rows), "matches": rows}
