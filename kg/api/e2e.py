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
    # We union two sources of TestId nodes for the same requested value:
    # the literal node (if any) and any pattern node whose static prefix
    # matches. This way `sidebar-booking` shows BOTH the locator (literal
    # node, created from `[data-testid="sidebar-booking"]`) AND the adsw
    # source files of the pattern `sidebar-` it actually maps to.
    cypher = """
        MATCH (t:TestId {project: $project})
        WHERE (t.pattern = false AND t.value = $value)
           OR (t.pattern = true  AND size(t.value) > 0 AND $value STARTS WITH t.value)
        WITH collect(t) AS ts
        WHERE size(ts) > 0
        UNWIND ts AS t
        OPTIONAL MATCH (f:File)-[h:HAS_TESTID]->(t)
        OPTIONAL MATCH (l:TestIdLoc)-[:RESOLVES_TO]->(t)
        OPTIONAL MATCH (po:PageObject)-[:DEFINES_LOC]->(l)
        OPTIONAL MATCH (sp:E2eSpec)-[:USES_POM]->(po)
        WITH
          collect(DISTINCT {file: f.path, line: h.line}) AS adsw_sources,
          collect(DISTINCT {name: l.name, file: l.file, line: l.line}) AS locators,
          collect(DISTINCT po.name) AS page_objects,
          collect(DISTINCT {name: sp.name, file: sp.file, line: sp.line}) AS specs
        RETURN
          $value AS value,
          [s IN adsw_sources WHERE s.file IS NOT NULL] AS adsw_sources,
          [l IN locators     WHERE l.name IS NOT NULL] AS locators,
          [p IN page_objects WHERE p IS NOT NULL]     AS page_objects,
          [s IN specs        WHERE s.name IS NOT NULL] AS specs
    """
    with session() as s_:
        rec = s_.run(cypher, project=project, value=value).single()
    if not rec or rec["value"] is None:
        raise HTTPException(404, f"testid not found: {value} (project={project})")
    return {"project": project, **rec.data()}


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
