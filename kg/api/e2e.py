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
    cypher = """
        MATCH (s:E2eSpec {project: $project})
        WHERE $q IS NULL OR toLower(s.name) CONTAINS toLower($q)
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
    cypher = """
        MATCH (t:TestId {project: $project, value: $value})
        OPTIONAL MATCH (f:File)-[h:HAS_TESTID]->(t)
        OPTIONAL MATCH (l:TestIdLoc)-[:RESOLVES_TO]->(t)
        OPTIONAL MATCH (po:PageObject)-[:DEFINES_LOC]->(l)
        OPTIONAL MATCH (sp:E2eSpec)-[:USES_POM]->(po)
        WITH t,
             collect(DISTINCT {file: f.path, line: h.line}) AS adsw_sources,
             collect(DISTINCT {name: l.name, file: l.file, line: l.line}) AS locators,
             collect(DISTINCT po.name) AS page_objects,
             collect(DISTINCT {name: sp.name, file: sp.file, line: sp.line}) AS specs
        RETURN
          t.value AS value,
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

    Only adsw-defined testids count toward the denominator; locator-only
    values (referenced in e2e but not present in app code) are reported
    separately."""
    cypher = """
        MATCH (t:TestId {project: $project})
        OPTIONAL MATCH (f:File)-[:HAS_TESTID]->(t)
        OPTIONAL MATCH (l:TestIdLoc)-[:RESOLVES_TO]->(t)
        WITH t, count(DISTINCT f) AS in_adsw, count(DISTINCT l) AS in_loc
        RETURN
          sum(CASE WHEN in_adsw > 0 THEN 1 ELSE 0 END) AS adsw_total,
          sum(CASE WHEN in_adsw > 0 AND in_loc > 0 THEN 1 ELSE 0 END) AS covered,
          sum(CASE WHEN in_adsw > 0 AND in_loc = 0 THEN 1 ELSE 0 END) AS uncovered,
          sum(CASE WHEN in_adsw = 0 AND in_loc > 0 THEN 1 ELSE 0 END) AS stale_e2e_only
    """
    with session() as s_:
        rec = s_.run(cypher, project=project).single()
    return {"project": project, **(rec.data() if rec else {})}


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
