"""Pages endpoints (Phase 3)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ._models import (
    DomainsListResponse,
    PageGetResponse,
    PagesListResponse,
)


router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("/list", response_model=PagesListResponse)
async def list_pages(
    project: str,
    domain: str | None = Query(default=None, description="filter by domain"),
) -> dict:
    cypher = """
        MATCH (pg:Page {project: $project})
        WHERE ($domain IS NULL OR pg.domain = $domain)
        OPTIONAL MATCH (co:Component)-[:BELONGS_TO]->(pg)
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(co)
        WITH pg, collect(DISTINCT co.name) AS components, collect(DISTINCT r.path) AS routes
        RETURN pg.domain AS domain, pg.entity AS entity, pg.dir AS dir,
               pg.files AS files, pg.subdirs AS subdirs,
               components, routes
        ORDER BY pg.domain, pg.entity
    """
    with session() as s:
        rows = s.run(cypher, project=project, domain=domain).data()
    return {"project": project, "count": len(rows), "pages": rows}


@router.get("/get", response_model=PageGetResponse)
async def get_page(project: str, domain: str, entity: str = "") -> dict:
    cypher = """
        MATCH (pg:Page {project: $project, domain: $domain, entity: $entity})
        OPTIONAL MATCH (co:Component)-[:BELONGS_TO]->(pg)
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(co)
        OPTIONAL MATCH (co)-[:CALLS_HOOK]->(h:GqlHook)
        OPTIONAL MATCH (co)-[:USES_OPERATION]->(o:GqlOperation)
        OPTIONAL MATCH (h)-[:WRAPS]->(opH:GqlOperation)
        WITH pg,
             collect(DISTINCT {name: co.name, file: co.file, line: co.line}) AS components,
             collect(DISTINCT r.path) AS routes,
             collect(DISTINCT h.name) AS hooks,
             collect(DISTINCT {symbol: o.symbol, kind: o.kind, gql_name: o.gql_name}) AS ops_direct,
             collect(DISTINCT {symbol: opH.symbol, kind: opH.kind, gql_name: opH.gql_name}) AS ops_via_hooks
        RETURN
          {domain: pg.domain, entity: pg.entity, dir: pg.dir, files: pg.files, subdirs: pg.subdirs} AS page,
          [c IN components WHERE c.name IS NOT NULL] AS components,
          [r IN routes WHERE r IS NOT NULL] AS routes,
          [h IN hooks WHERE h IS NOT NULL] AS hooks,
          [o IN ops_direct WHERE o.symbol IS NOT NULL] AS ops_direct,
          [o IN ops_via_hooks WHERE o.symbol IS NOT NULL] AS ops_via_hooks
    """
    with session() as s:
        rec = s.run(cypher, project=project, domain=domain, entity=entity).single()
    if not rec or rec["page"] is None:
        raise HTTPException(404, f"page not found: {domain}/{entity} (project={project})")
    return {"project": project, **rec.data()}


@router.get("/domains", response_model=DomainsListResponse)
async def list_domains(project: str) -> dict:
    cypher = """
        MATCH (d:Domain {project: $project})
        OPTIONAL MATCH (pg:Page)-[:IN_DOMAIN]->(d)
        RETURN d.name AS name, count(DISTINCT pg) AS pages
        ORDER BY d.name
    """
    with session() as s:
        rows = s.run(cypher, project=project).data()
    return {"project": project, "count": len(rows), "domains": rows}
